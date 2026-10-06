"""
Traitement de génération des brouillons Vinted : appelle Gemini + résolution
référentiel pour un lot, et écrit le résultat en base (status='generation_ok'/
'generation_error'). Fonction 100% synchrone (aucun appel async restant depuis
le retrait de l'estimation de prix), donc appelable directement depuis le
thread du watcher pour un déclenchement immédiat par lot.

Ne touche pas encore à Vinted -- prépare uniquement la donnée. La création du
brouillon sur Vinted (automatisation CDP) est une étape séparée, ultérieure.
"""
import logging
import uuid
from datetime import datetime, timezone

from database import supabase
from services.gemini_brouillon_service import generer_brouillon_ia

logger = logging.getLogger(__name__)


def traiter_un_lot(lot_id, photos):
    """
    Traite UN lot déjà présent en base (status='pending_generation') : génère
    son contenu et met à jour son statut. Utilisé pour le déclenchement immédiat
    depuis le watcher.
    """
    resultat = generer_brouillon_ia(photos)

    if resultat["status"] != "success":
        logger.warning(f"⚠️ [BROUILLON WORKER] Lot {lot_id} — génération échouée : {resultat['message']}")
        _mettre_a_jour_lot(lot_id, status="generation_error", error_message=resultat["message"])
        return resultat

    gemini_raw = resultat["gemini_raw"]
    resolution = resultat["resolution"]
    prix_estimation = resultat.get("prix_estimation")

    _mettre_a_jour_lot(
        lot_id,
        status="generation_ok",
        gemini_output={"raw": gemini_raw, "resolution": resolution},
        prix_estimation=prix_estimation,
    )
    logger.info(f"✅ [BROUILLON WORKER] Lot {lot_id} — génération OK.")
    return resultat


def traiter_lots_pending_generation():
    """
    Traite en bloc tous les lots encore en 'pending_generation'. Sert de
    rattrapage au démarrage du watcher (ex: lots restés bloqués après un
    redémarrage backend en plein traitement) -- le fonctionnement normal passe
    par traiter_un_lot() appelé immédiatement par le watcher.
    Retourne un résumé {"traites": int, "succes": int, "erreurs": int}.
    """
    try:
        reponse = supabase.table("brouillons_pending") \
            .select("id, photos") \
            .eq("status", "pending_generation") \
            .execute()
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur lecture Supabase : {e}")
        return {"traites": 0, "succes": 0, "erreurs": 0}

    lots = reponse.data or []
    if not lots:
        return {"traites": 0, "succes": 0, "erreurs": 0}

    logger.info(f"🧵 [BROUILLON WORKER] Rattrapage — {len(lots)} lot(s) à traiter.")

    succes, erreurs = 0, 0
    for lot in lots:
        resultat = traiter_un_lot(lot["id"], lot.get("photos") or [])
        if resultat["status"] == "success":
            succes += 1
        else:
            erreurs += 1

    return {"traites": len(lots), "succes": succes, "erreurs": erreurs}


def _mettre_a_jour_lot(lot_id, status, gemini_output=None, prix_estimation=None, error_message=None, ajustements_manuels=None):
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if gemini_output is not None:
        payload["gemini_output"] = gemini_output
    if prix_estimation is not None:
        payload["prix_estimation"] = prix_estimation
    if error_message is not None:
        payload["error_message"] = error_message
    if ajustements_manuels is not None:
        payload["ajustements_manuels"] = ajustements_manuels

    try:
        supabase.table("brouillons_pending").update(payload).eq("id", lot_id).execute()
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur mise à jour Supabase (lot {lot_id}) : {e}")


def regenerer_lot_avec_ajustements(lot_id, ajustements):
    """
    Relance la génération IA d'un lot déjà traité, en tenant compte d'informations
    complémentaires confirmées manuellement par l'utilisateur (état neuf/défaut,
    fabrication) -- boutons ajoutés sous la description dans Préparer Annonces.
    Réutilise les photos déjà archivées du lot, pas de nouvel upload nécessaire.
    """
    reponse = supabase.table("brouillons_pending").select("photos").eq("id", lot_id).execute()
    if not reponse.data:
        return {"status": "error", "message": "Lot introuvable."}

    photos = reponse.data[0].get("photos") or []
    if not photos:
        return {"status": "error", "message": "Lot sans photo archivée -- régénération impossible."}

    resultat = generer_brouillon_ia(photos, ajustements)

    if resultat["status"] != "success":
        logger.warning(f"⚠️ [BROUILLON WORKER] Lot {lot_id} — régénération échouée : {resultat['message']}")
        _mettre_a_jour_lot(lot_id, status="generation_error", error_message=resultat["message"])
        return resultat

    gemini_raw = resultat["gemini_raw"]
    resolution = resultat["resolution"]
    prix_estimation = resultat.get("prix_estimation")

    _mettre_a_jour_lot(
        lot_id,
        status="generation_ok",
        gemini_output={"raw": gemini_raw, "resolution": resolution},
        prix_estimation=prix_estimation,
        ajustements_manuels=ajustements,
    )
    logger.info(f"✅ [BROUILLON WORKER] Lot {lot_id} — régénération avec ajustements OK ({ajustements}).")
    return resultat

def diviser_lot(lot_id, photos_a_extraire):
    """
    Scinde un lot mal groupé en deux : les photos listées dans photos_a_extraire
    partent dans un NOUVEAU lot, le reste reste dans le lot d'origine. Les deux
    repartent en 'pending_validation' -- PAS de génération immédiate : la
    composition vient de changer, elle doit repasser par la validation (unitaire
    ou groupée) avant tout appel Gemini, comme n'importe quel autre lot.
    """
    reponse = supabase.table("brouillons_pending").select("*").eq("id", lot_id).execute()
    if not reponse.data:
        return {"status": "error", "message": "Lot introuvable."}

    lot = reponse.data[0]
    photos_actuelles = lot.get("photos") or []

    photos_restantes = [p for p in photos_actuelles if p not in photos_a_extraire]
    photos_extraites = [p for p in photos_actuelles if p in photos_a_extraire]

    if not photos_extraites or not photos_restantes:
        return {"status": "error", "message": "Sélection invalide : il faut garder au moins une photo dans chaque lot."}

    nouveau_lot_id = str(uuid.uuid4())

    try:
        supabase.table("brouillons_pending").update({
            "photos": photos_restantes,
            "status": "pending_validation",
            "gemini_output": None,
            "prix_estimation": None,
            "error_message": None,
        }).eq("id", lot_id).execute()

        supabase.table("brouillons_pending").insert({
            "id": nouveau_lot_id,
            "photos": photos_extraites,
            "status": "pending_validation",
            "dressing": lot.get("dressing"),
        }).execute()
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur division du lot {lot_id} : {e}")
        return {"status": "error", "message": f"Erreur base de données : {e}"}

    return {"status": "success", "lot_original": lot_id, "nouveau_lot": nouveau_lot_id}

def deplacer_photos(lot_id_source, photos_a_deplacer, lot_id_cible=None):
    """
    Déplace une ou plusieurs photos d'un lot vers un autre lot EXISTANT
    (lot_id_cible fourni -> fusion), ou vers un nouveau lot si lot_id_cible est
    None (équivalent à un split ciblé). Le lot source vide après déplacement
    est supprimé plutôt que laissé en base avec une liste de photos vide.
    Les deux lots impactés repassent en 'pending_validation'.
    """
    reponse = supabase.table("brouillons_pending").select("*").eq("id", lot_id_source).execute()
    if not reponse.data:
        return {"status": "error", "message": "Lot source introuvable."}

    lot_source = reponse.data[0]
    photos_source_actuelles = lot_source.get("photos") or []
    photos_restantes = [p for p in photos_source_actuelles if p not in photos_a_deplacer]
    photos_a_deplacer_reelles = [p for p in photos_source_actuelles if p in photos_a_deplacer]

    if not photos_a_deplacer_reelles:
        return {"status": "error", "message": "Aucune des photos indiquées n'appartient à ce lot."}

    try:
        if photos_restantes:
            supabase.table("brouillons_pending").update({
                "photos": photos_restantes,
                "status": "pending_validation",
                "gemini_output": None,
                "prix_estimation": None,
                "error_message": None,
            }).eq("id", lot_id_source).execute()
        else:
            supabase.table("brouillons_pending").delete().eq("id", lot_id_source).execute()

        if lot_id_cible:
            reponse_cible = supabase.table("brouillons_pending").select("photos").eq("id", lot_id_cible).execute()
            if not reponse_cible.data:
                return {"status": "error", "message": "Lot cible introuvable."}
            photos_cible = (reponse_cible.data[0].get("photos") or []) + photos_a_deplacer_reelles
            supabase.table("brouillons_pending").update({
                "photos": photos_cible,
                "status": "pending_validation",
                "gemini_output": None,
                "prix_estimation": None,
                "error_message": None,
            }).eq("id", lot_id_cible).execute()
            lot_resultat = lot_id_cible
        else:
            lot_resultat = str(uuid.uuid4())
            supabase.table("brouillons_pending").insert({
                "id": lot_resultat,
                "photos": photos_a_deplacer_reelles,
                "status": "pending_validation",
                "dressing": lot_source.get("dressing"),
            }).execute()
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur déplacement de photos ({lot_id_source} -> {lot_id_cible or 'nouveau lot'}) : {e}")
        return {"status": "error", "message": f"Erreur base de données : {e}"}

    return {"status": "success", "lot_source": lot_id_source if photos_restantes else None, "lot_resultat": lot_resultat}


DRESSINGS_VALIDES = ("Dressing 1", "Dressing 2")


def changer_dressing_lot(lot_id, dressing):
    """
    Change le dressing choisi pour un lot (sélecteur sur la carte, Préparer
    Annonces -- cf. échange du 05/10/2026). Déterminera quel profil Chrome
    (Dressing 1 ou 2) vinted_draft_creation_worker utilisera pour créer le
    brouillon. Bloqué une fois le brouillon déjà créé ou en cours de création,
    puisque le navigateur du dressing d'origine est alors déjà engagé.
    """
    if dressing not in DRESSINGS_VALIDES:
        return {"status": "error", "message": f"Dressing invalide : {dressing}"}

    reponse = supabase.table("brouillons_pending").select("status").eq("id", lot_id).execute()
    if not reponse.data:
        return {"status": "error", "message": "Lot introuvable."}
    if reponse.data[0]["status"] in ("creating_draft", "draft_created"):
        return {"status": "error", "message": "Brouillon déjà créé (ou en cours de création) -- dressing non modifiable."}

    try:
        supabase.table("brouillons_pending").update({"dressing": dressing}).eq("id", lot_id).execute()
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur changement de dressing du lot {lot_id} : {e}")
        return {"status": "error", "message": f"Erreur base de données : {e}"}

    return {"status": "success"}


def valider_lot(lot_id):
    """Déclenche la génération Gemini pour UN lot en 'pending_validation' (ou
    'a_regrouper_manuellement', une fois corrigé manuellement)."""
    reponse = supabase.table("brouillons_pending").select("photos").eq("id", lot_id).execute()
    if not reponse.data:
        return {"status": "error", "message": "Lot introuvable."}
    photos = reponse.data[0].get("photos") or []
    if not photos:
        return {"status": "error", "message": "Lot sans photo -- rien à valider."}

    supabase.table("brouillons_pending").update({"status": "pending_generation"}).eq("id", lot_id).execute()
    return traiter_un_lot(lot_id, photos)


def reessayer_creation_brouillon(lot_id):
    """
    Remet UN lot en 'draft_error' en 'generation_ok', sans repasser par Gemini --
    le contenu (gemini_output) généré précédemment reste valable, seule la
    création du brouillon Vinted (étape séparée, via vinted_draft_creation_worker)
    a échoué. Le lot sera repris au prochain lancement de ce worker.
    """
    reponse = supabase.table("brouillons_pending").select("status").eq("id", lot_id).execute()
    if not reponse.data:
        return {"status": "error", "message": "Lot introuvable."}
    if reponse.data[0]["status"] != "draft_error":
        return {"status": "error", "message": "Ce lot n'est pas en erreur de création de brouillon."}

    try:
        supabase.table("brouillons_pending").update({
            "status": "generation_ok",
            "error_message": None,
        }).eq("id", lot_id).execute()
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur remise en file du lot {lot_id} : {e}")
        return {"status": "error", "message": f"Erreur base de données : {e}"}

    logger.info(f"🔁 [BROUILLON WORKER] Lot {lot_id} remis en 'generation_ok' pour nouvelle tentative de création.")
    return {"status": "success"}


def valider_tous_les_lots():
    """
    Valide en bloc tous les lots en 'pending_validation' -- mode "valider tout"
    par défaut. Traités séquentiellement : le rythme Gemini (wait_for_gemini_rate_limit,
    appelé depuis generer_brouillon_ia) s'applique naturellement à chaque appel,
    donc aucun risque de rafale même avec plusieurs dizaines de lots d'un coup.
    """
    reponse = supabase.table("brouillons_pending").select("id, photos").eq("status", "pending_validation").execute()
    lots = reponse.data or []
    if not lots:
        return {"traites": 0, "succes": 0, "erreurs": 0}

    logger.info(f"✅ [BROUILLON WORKER] Validation groupée -- {len(lots)} lot(s) à traiter.")
    succes, erreurs = 0, 0
    for lot in lots:
        resultat = valider_lot(lot["id"])
        if resultat["status"] == "success":
            succes += 1
        else:
            erreurs += 1

    return {"traites": len(lots), "succes": succes, "erreurs": erreurs}

def vider_historique():
    """
    Supprime TOUTES les lignes de brouillons_pending (reset complet du dashboard
    Préparer Annonces). Les photos archivées sur le disque (_traite/<id>/) ne
    sont PAS touchées, uniquement les entrées en base.
    """
    try:
        # Supabase exige une condition sur delete() -- neq sur un id impossible
        # couvre "toutes les lignes" sans avoir besoin d'un filtre métier réel.
        supabase.table("brouillons_pending").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        logger.info("🗑️ [BROUILLON WORKER] Historique brouillons_pending vidé.")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur lors du vidage de l'historique : {e}")
        return {"status": "error", "message": str(e)}

def supprimer_lot(lot_id):
    """
    Supprime UN lot précis de brouillons_pending -- contrairement à
    vider_historique() qui efface tout. Ne touche pas aux photos archivées sur
    disque : le nettoyage du dossier _traite/<lot_id>/ est fait par l'appelant
    (route), qui a déjà accès à DOSSIER_TRAITE sans créer d'import circulaire
    avec photo_watcher_service.
    """
    try:
        supabase.table("brouillons_pending").delete().eq("id", lot_id).execute()
        logger.info(f"🗑️ [BROUILLON WORKER] Lot {lot_id} supprimé.")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"🚨 [BROUILLON WORKER] Erreur suppression du lot {lot_id} : {e}")
        return {"status": "error", "message": str(e)}