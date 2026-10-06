"""
Service de surveillance des photos, piloté par toggle depuis le dashboard
(page "Préparer Annonces") plutôt que lancé comme script Windows autonome.

Reprend la logique de clustering + debounce déjà validée. Les lots regroupés
automatiquement attendent désormais une VALIDATION explicite (individuelle ou
groupée) avant tout appel Gemini -- plus de génération immédiate. Expose
start()/stop()/statut() pour les routes API.
"""
import logging
import os
import shutil
import threading
import time
import uuid

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from photo_compressor import compress_photo
from photo_grouping import EXTENSIONS_VALIDES, grouper_photos_par_article
from photo_similarity import affiner_groupes_par_similarite
from database import supabase
from services.brouillon_worker import traiter_un_lot, traiter_lots_pending_generation

logger = logging.getLogger(__name__)

DOSSIER_SURVEILLE = r"C:\Users\hamdi\Sync_Vinted_Photos"
DOSSIER_TRAITE = os.path.join(DOSSIER_SURVEILLE, "_traite")


DRESSING_PAR_DEFAUT = "Dressing 1"


def creer_lot_depuis_fichiers(fichiers, dressing=DRESSING_PAR_DEFAUT):
    """
    Crée directement UN lot à partir de fichiers uploadés depuis le navigateur
    (mode "Regrouper manuellement" -- glisser-déposer depuis l'explorateur de
    fichiers dans une zone dédiée). Reprend le même archivage que le watcher
    (compression + DOSSIER_TRAITE/<lot_id>/), mais sans passer par le
    clustering automatique : le regroupement est décidé par l'utilisateur.

    fichiers : liste de tuples (nom_fichier, contenu_bytes).
    dressing : dressing choisi pour ce lot (modifiable ensuite depuis Préparer
    Annonces jusqu'à la création effective du brouillon -- cf. échange du
    05/10/2026). Retourne l'id du lot créé, ou None si aucun fichier n'a pu
    être archivé.
    """
    lot_id = str(uuid.uuid4())
    dossier_archive = os.path.join(DOSSIER_TRAITE, lot_id)
    os.makedirs(dossier_archive, exist_ok=True)

    chemins_archives = []
    for nom_fichier, contenu in fichiers:
        destination = os.path.join(dossier_archive, nom_fichier)
        try:
            with open(destination, "wb") as f:
                f.write(contenu)
        except Exception as e:
            logger.error(f"🚨 [UPLOAD MANUEL] Erreur écriture {nom_fichier} : {e}")
            continue
        try:
            compress_photo(destination)
        except Exception as e:
            logger.warning(f"⚠️ [UPLOAD MANUEL] Compression échouée pour {destination}, gardée non compressée : {e}")
        chemins_archives.append(destination)

    if not chemins_archives:
        logger.error(f"🚨 [UPLOAD MANUEL] Lot {lot_id} vide après échec d'écriture, abandon.")
        return None

    try:
        supabase.table("brouillons_pending").insert({
            "id": lot_id,
            "photos": chemins_archives,
            "status": "pending_validation",
            "dressing": dressing,
        }).execute()
    except Exception as e:
        logger.error(f"🚨 [UPLOAD MANUEL] Erreur insertion Supabase pour le lot {lot_id} : {e}")
        return None

    logger.info(f"📦 [UPLOAD MANUEL] Lot {lot_id} ({len(chemins_archives)} photo(s)) créé manuellement.")
    return lot_id

DELAI_FINALISATION_SECONDES = 600  # 10 min d'inactivité -> voir photo_grouping.py

DELAI_STABILISATION_SECONDES = 1.0
NB_VERIFICATIONS_STABILITE = 3


def attendre_fichier_stable(chemin_fichier):
    """Attend que la taille du fichier arrête de changer (upload/sync en cours)."""
    tailles_stables = 0
    derniere_taille = -1
    while tailles_stables < NB_VERIFICATIONS_STABILITE:
        if not os.path.isfile(chemin_fichier):
            return False
        taille_actuelle = os.path.getsize(chemin_fichier)
        if taille_actuelle == derniere_taille:
            tailles_stables += 1
        else:
            tailles_stables = 0
            derniere_taille = taille_actuelle
        time.sleep(DELAI_STABILISATION_SECONDES)
    return True


class _PhotoHandler(FileSystemEventHandler):
    """
    Gère deux cas distincts :
    1. traiter_photos_existantes() -- appelé une fois à l'activation du watcher,
       scanne TOUT ce qui est déjà dans le dossier (séance photo terminée avant
       activation) et le traite immédiatement, sans attendre de délai d'inactivité.
    2. on_created/on_moved -- pour toute photo qui arriverait APRÈS l'activation
       (cas rare : ajout tardif pendant que le watcher tourne déjà), avec le
       même mécanisme de debounce qu'avant, en filet de sécurité.
    """

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._pool = []
        self._timer = None

    def traiter_photos_existantes(self):
        """Scan + compression + regroupement + génération immédiate de tout ce qui
        est déjà présent dans DOSSIER_SURVEILLE au moment de l'appel."""
        chemins = [
            os.path.join(DOSSIER_SURVEILLE, f) for f in os.listdir(DOSSIER_SURVEILLE)
            if f.lower().endswith(EXTENSIONS_VALIDES)
        ]
        if not chemins:
            logger.info("ℹ️ [WATCHER] Aucune photo trouvée à l'activation.")
            return

        logger.info(f"🔎 [WATCHER] {len(chemins)} photo(s) trouvée(s) à l'activation, traitement immédiat.")

        chemins_compresses = []
        for chemin in chemins:
            if not attendre_fichier_stable(chemin):
                logger.warning(f"⚠️ [WATCHER] {chemin} a disparu avant stabilisation, ignoré.")
                continue
            try:
                result = compress_photo(chemin)
                if not result["compressed"]:
                    logger.info(f"⏭️  [WATCHER] Pas touchée : {result['reason']} ({result['size_mo']} Mo)")
            except Exception as e:
                logger.error(f"🚨 [WATCHER] Erreur compression {chemin} : {e}")
                continue
            chemins_compresses.append(chemin)

        self._finaliser_photos(chemins_compresses)

    def on_created(self, event):
        self._traiter_si_photo(event, event.src_path)

    def on_moved(self, event):
        self._traiter_si_photo(event, event.dest_path)

    def _traiter_si_photo(self, event, chemin_fichier):
        if event.is_directory:
            return
        if not chemin_fichier.lower().endswith(EXTENSIONS_VALIDES):
            return
        if os.path.dirname(chemin_fichier) != DOSSIER_SURVEILLE:
            return

        logger.info(f"📸 [WATCHER] Nouvelle photo détectée (après activation) : {chemin_fichier}")

        if not attendre_fichier_stable(chemin_fichier):
            logger.warning(f"⚠️ [WATCHER] {chemin_fichier} a disparu avant stabilisation, ignoré.")
            return

        try:
            result = compress_photo(chemin_fichier)
            if not result["compressed"]:
                logger.info(f"⏭️  [WATCHER] Pas touchée : {result['reason']} ({result['size_mo']} Mo)")
        except Exception as e:
            logger.error(f"🚨 [WATCHER] Erreur compression {chemin_fichier} : {e}")
            return

        with self._lock:
            self._pool.append(chemin_fichier)
            self._reset_timer()

    def _reset_timer(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(DELAI_FINALISATION_SECONDES, self._finaliser_pool_differe)
        self._timer.daemon = True
        self._timer.start()

    def _finaliser_pool_differe(self):
        with self._lock:
            if not self._pool:
                return
            photos_a_traiter = list(self._pool)
            self._pool = []
        self._finaliser_photos(photos_a_traiter)

    def _finaliser_photos(self, chemins):
        if not chemins:
            return

        try:
            groupes, a_trier_manuellement = grouper_photos_par_article(chemins)
            groupes = affiner_groupes_par_similarite(groupes)
        except Exception as e:
            logger.error(f"🚨 [WATCHER] Erreur clustering : {e}")
            return

        # Les groupes automatiques passent désormais par 'pending_validation' --
        # plus de génération Gemini immédiate. La validation (unitaire ou groupée
        # via "Valider tout") est un geste explicite depuis Préparer Annonces.
        for groupe in groupes:
            chemins_groupe = [chemin for chemin, _, _ in groupe]
            self._creer_lot(chemins_groupe, status="pending_validation")

        for chemin, _ in a_trier_manuellement:
            self._creer_lot([chemin], status="a_regrouper_manuellement")

    def _creer_lot(self, chemins, status):
        lot_id = str(uuid.uuid4())
        dossier_archive = os.path.join(DOSSIER_TRAITE, lot_id)
        os.makedirs(dossier_archive, exist_ok=True)

        chemins_archives = []
        for chemin in chemins:
            try:
                destination = os.path.join(dossier_archive, os.path.basename(chemin))
                shutil.move(chemin, destination)
                chemins_archives.append(destination)
            except Exception as e:
                logger.error(f"🚨 [WATCHER] Erreur déplacement {chemin} vers archive : {e}")

        if not chemins_archives:
            logger.error(f"🚨 [WATCHER] Lot {lot_id} vide après échec de déplacement, abandon.")
            return

        try:
            supabase.table("brouillons_pending").insert({
                "id": lot_id,
                "photos": chemins_archives,
                "status": status,
                "dressing": DRESSING_PAR_DEFAUT,
            }).execute()
            logger.info(f"📦 [WATCHER] Lot {lot_id} ({len(chemins_archives)} photo(s)) -> statut '{status}'")
        except Exception as e:
            logger.error(f"🚨 [WATCHER] Erreur insertion Supabase pour le lot {lot_id} : {e}")

    def taille_pool(self):
        with self._lock:
            return len(self._pool)


class PhotoWatcherService:
    """Singleton contrôlé par les routes API (start/stop/statut)."""

    def __init__(self):
        self._observer = None
        self._handler = None
        self._lock = threading.Lock()
        self._scan_initial_en_cours = False

    def est_actif(self):
        return self._observer is not None and self._observer.is_alive()

    def demarrer(self):
        with self._lock:
            if self.est_actif():
                return {"status": "already_running"}

            if not os.path.isdir(DOSSIER_SURVEILLE):
                return {"status": "error", "message": f"Dossier introuvable : {DOSSIER_SURVEILLE}"}

            os.makedirs(DOSSIER_TRAITE, exist_ok=True)

            # Rattrapage : traite tout lot resté en pending_generation d'une session précédente.
            traiter_lots_pending_generation()

            self._handler = _PhotoHandler()
            self._observer = Observer()
            self._observer.schedule(self._handler, DOSSIER_SURVEILLE, recursive=False)
            self._observer.start()
            logger.info("🟢 [WATCHER] Démarré.")

        # Traitement immédiat de tout ce qui est déjà dans le dossier -- en tâche
        # de fond pour que la requête HTTP réponde tout de suite (le traitement
        # peut prendre plusieurs minutes selon le nombre de lots, à cause du
        # rate limit Gemini de 15 appels/minute).
        threading.Thread(target=self._traiter_photos_existantes, daemon=True).start()

        return {"status": "started"}

    def _traiter_photos_existantes(self):
        self._scan_initial_en_cours = True
        try:
            self._handler.traiter_photos_existantes()
        except Exception as e:
            logger.error(f"🚨 [WATCHER] Erreur traitement initial : {e}")
        finally:
            self._scan_initial_en_cours = False

    def arreter(self):
        with self._lock:
            if not self.est_actif():
                return {"status": "already_stopped"}
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            self._handler = None
            logger.info("🔴 [WATCHER] Arrêté.")
            return {"status": "stopped"}

    def statut(self):
        return {
            "actif": self.est_actif(),
            "scan_initial_en_cours": self._scan_initial_en_cours,
            "photos_en_attente": self._handler.taille_pool() if self._handler else 0,
        }


# Singleton partagé par les routes FastAPI
watcher_service = PhotoWatcherService()