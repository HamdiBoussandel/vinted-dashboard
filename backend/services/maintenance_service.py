import asyncio
import glob
import json
import os
import shutil
import logging
from datetime import datetime
from playwright.async_api import async_playwright

from services.clemz_auto_message import ClemzAutoMessageWatchdog

logger = logging.getLogger(__name__)

# Dossier où Chrome stocke ses extensions installées (Clemz s'y met à jour
# automatiquement tout seul -- c'est la source de vérité pour la dernière version).
CLEMZ_CHROME_EXTENSION_ID = "kjpggncklgopkhbfpohiaomjpljkbifg"
CLEMZ_CHROME_EXTENSIONS_PATH = (
    r"C:\Users\hamdi\AppData\Local\Google\Chrome\User Data\Default\Extensions"
    "\\" + CLEMZ_CHROME_EXTENSION_ID
)


class MaintenanceService:
    """
    Gère l'ouverture manuelle des navigateurs Playwright -- exactement les mêmes
    profils (session + extension Clemz) que ceux utilisés par les tâches
    d'automatisation (cf. session_manager.py / clemz_automation.py). Sert à
    vérifier/rétablir la connexion Vinted et Clemz, ou ajuster des paramètres
    Clemz à la main, sans passer par le menu console maintenance_sessions.py.
    """

    def __init__(self):
        # backend/services/maintenance_service.py -> backend/
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        self.profiles = {
            "clemz_chrome": {
                # MIGRÉ (08/09/2026) : vrai Chrome + Clemz préchargée manuellement
                # dans le profil, même traitement que Dressing 2 -- "extension"
                # reste renseigné (indicateur "ce profil utilise Clemz" pour
                # reset_extension_state), mais n'est plus injecté via
                # --load-extension : voir la branche profile.get("channel")
                # dans open_browser().
                "name": "Automation D1 (Chrome + Clemz)",
                "session": os.path.join(_base, "clemz_session_chrome_d1_reel"),
                "executable": None,
                "extension": os.path.join(_base, "clemz_extension"),
                "url": "https://www.vinted.fr/member/287248160",
                "channel": "chrome",
            },
            "clemz_edge": {
                # MIGRÉ (05/09/2026) : vrai Chrome + Clemz préchargée manuellement
                # dans le profil (chrome://extensions > Charger l'extension non
                # empaquetée) -- corrige l'empreinte TLS/JA3-JA4 du Chromium
                # embarqué. "extension" reste renseigné (utilisé par
                # reset_extension_state comme simple indicateur "ce profil utilise
                # Clemz"), mais n'est plus injecté via --load-extension : voir la
                # branche profile.get("channel") dans open_browser().
                "name": "Automation D2 (Chrome + Clemz)",
                "session": os.path.join(_base, "clemz_session_chrome_reel"),
                "executable": None,
                "extension": os.path.join(_base, "clemz_extension"),
                "url": "https://www.vinted.fr/member/3136979514",
                "channel": "chrome",
            },
            "vinted_chrome": {
                "name": "Scraping D1 (Chrome, sans Clemz)",
                "session": os.path.join(_base, "vinted_session_chrome"),
                "executable": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "extension": None,
                "url": "https://www.vinted.fr/member/287248160",
            },
            "vinted_edge": {
                "name": "Scraping D2 (Edge, sans Clemz)",
                "session": os.path.join(_base, "vinted_session_edge"),
                "executable": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                "extension": None,
                "url": "https://www.vinted.fr/member/3136979514",
            },
            # Dressing 3 (brouillons, Brave) a fusionné dans Dressing 1 le
            # 08/09/2026 -- profil "clemz_chrome" ci-dessus sert désormais aussi
            # à la création de brouillons (vinted_draft_creation_worker.py).
        }

        # État partagé, consulté par la route GET /api/maintenance/profiles
        self.status = {key: "ferme" for key in self.profiles}

        # Dossier local (copie nettoyée) chargé par --load-extension dans les profils Clemz
        self.extension_dir = os.path.join(_base, "clemz_extension")
        # État de la dernière mise à jour de l'extension, consulté par le front
        self.extension_info = {"version": None, "updated_at": None}

        # Baisse de prix automatique quotidienne (cron 14h05, automation_service.py)
        # -- persisté sur disque (contrairement au watchdog, volontairement en
        # mémoire) car ce réglage doit survivre à un redémarrage du serveur.
        self.baisse_prix_auto_settings_file = os.path.join(_base, "baisse_prix_auto_settings.json")
        self.baisse_prix_auto_active = self._load_baisse_prix_auto_active()

        # Partage des vues/favoris automatique -- déclenché normalement juste après
        # une republication réussie. Même logique de persistance que ci-dessus.
        self.partage_vues_favoris_auto_settings_file = os.path.join(_base, "partage_vues_favoris_auto_settings.json")
        self.partage_vues_favoris_auto_active = self._load_partage_vues_favoris_auto_active()

        self.scraping_auto_settings_file = os.path.join(_base, "scraping_auto_settings.json")
        self.scraping_auto_active = self._load_scraping_auto_active()

        # Republication automatique (plan du jour + rattrapage au démarrage) --
        # ajouté le 10/10/2026 : c'était la seule automatisation sans
        # interrupteur, donc le PC de dev republiait dès que son propre plan
        # (tiré localement) rendait un dressing actif, en plus de la VM. Même
        # persistance par machine que les 3 autres (fichier hors git).
        self.republication_auto_settings_file = os.path.join(_base, "republication_auto_settings.json")
        self.republication_auto_active = self._load_republication_auto_active()

        self.clemz_visible_settings_file = os.path.join(_base, "clemz_visible_settings.json")
        self.clemz_visible_mode = self._load_clemz_visible_mode()

        # Watchdog "Messages automatiques" (Chrome + Edge) -- piloté manuellement
        # depuis la section Automatisation de la page Maintenance, plutôt qu'un
        # démarrage automatique au boot du serveur (cf. main.py, désactivé le temps
        # de régler le conflit entre reconnexion manuelle et tentatives simultanées).
        self.watchdog_task = None
        self.watchdog_active = False

        # Intention de l'utilisateur (distincte de watchdog_active, qui reflète
        # l'état RÉEL de la tâche et retombe à False aussi bien sur un arrêt
        # volontaire que sur un crash -- impossible de distinguer les deux avec ce
        # seul flag). watchdog_desired_active ne change QUE sur une action
        # explicite de l'utilisateur (toggle Maintenance), jamais sur un crash --
        # c'est ce que la vérification périodique (toutes les 30 min) consulte
        # pour savoir si elle doit relancer une tâche tombée en erreur, sans
        # jamais réactiver un arrêt volontaire.
        self.watchdog_desired_active = False

    def list_profiles(self):
        return [
            {"key": key, "name": p["name"], "status": self.status.get(key, "ferme")}
            for key, p in self.profiles.items()
        ]

    async def open_browser(self, key: str):
        """
        Ouvre le navigateur persistant pour le profil donné et attend sa fermeture
        manuelle par l'utilisateur. Conçu pour tourner en tâche de fond
        (asyncio.create_task) -- ne bloque jamais la requête HTTP qui le déclenche.
        """
        profile = self.profiles.get(key)
        if not profile:
            logger.error(f"❌ [MAINTENANCE] Profil inconnu : {key}")
            return

        if self.status.get(key) in ("ouverture", "ouvert"):
            logger.warning(f"⚠️ [MAINTENANCE] {profile['name']} déjà ouvert ou en cours d'ouverture.")
            return

        self.status[key] = "ouverture"
        logger.info(f"🚀 [MAINTENANCE] Ouverture : {profile['name']}")

        args = [
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,800",
            "--window-position=0,0",
        ]

        launch_options = {
            "headless": False,
            "args": args,
        }
        if profile["executable"]:
            launch_options["executable_path"] = profile["executable"]

        if profile.get("channel"):
            # Vrai navigateur avec l'extension déjà installée manuellement dans
            # le profil -- pas de --load-extension, juste neutraliser le
            # --disable-extensions par défaut de Playwright qui la désactiverait
            # sinon (cf. profil "clemz_edge", migré 05/09/2026).
            launch_options["channel"] = profile["channel"]
            launch_options["ignore_default_args"] = [
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ]
        elif profile["extension"]:
            args += [
                f"--disable-extensions-except={profile['extension']}",
                f"--load-extension={profile['extension']}",
            ]

        try:
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(profile["session"], **launch_options)

                # Le profil peut avoir restauré plusieurs onglets de la session
                # précédente (accueil Vinted, page membre, etc.) -- on n'en
                # garde qu'un seul pour ne pas noyer l'utilisateur à l'ouverture.
                pages = context.pages
                page = pages[0] if pages else await context.new_page()
                for onglet in pages[1:]:
                    try:
                        await onglet.close()
                    except Exception:
                        pass

                await page.goto(profile["url"], wait_until="domcontentloaded")

                self.status[key] = "ouvert"
                logger.info(f"✅ [MAINTENANCE] {profile['name']} ouvert -- en attente de fermeture manuelle.")

                close_event = asyncio.Event()
                page.on("close", lambda _: close_event.set())
                await close_event.wait()

        except Exception as e:
            logger.error(f"❌ [MAINTENANCE] Erreur ouverture {profile['name']} : {e}")
        finally:
            self.status[key] = "ferme"
            logger.info(f"🔒 [MAINTENANCE] {profile['name']} fermé.")

    def _version_key(self, path):
        # Dossiers du type "6.5.2_0" -- on parse le numéro de version pour trier
        # sémantiquement (1.9.0 doit passer après 1.10.0), plutôt que de se fier
        # à la date de modification (os.path.getmtime), peu fiable : Chrome peut
        # toucher d'anciens dossiers de version pendant une mise à jour, ce qui
        # les fait remonter comme "plus récents" alors qu'ils ne le sont pas.
        folder = os.path.basename(path)
        version_part = folder.split("_")[0]
        try:
            return tuple(int(x) for x in version_part.split("."))
        except ValueError:
            return (0,)

    def _find_latest_chrome_version(self):
        """
        Cherche, parmi les dossiers de version de l'extension Clemz installée dans
        Chrome, le plus récent contenant un manifest.json valide. Retourne
        (chemin_du_dossier, numero_de_version) ou lève FileNotFoundError.
        Opération synchrone (I/O fichier uniquement) -- à appeler via
        asyncio.to_thread() depuis une route.
        """
        versions = glob.glob(os.path.join(CLEMZ_CHROME_EXTENSIONS_PATH, "*"))
        if not versions:
            raise FileNotFoundError(
                "Extension Clemz introuvable dans Chrome. Vérifie qu'elle est bien "
                "installée et à jour dans Chrome (chrome://extensions)."
            )

        # Filtre les dossiers incomplets/corrompus (sans manifest.json lisible)
        # avant de trier, pour ne jamais retenir une version mi-écrite par Chrome.
        valid_versions = []
        for v in versions:
            manifest_check = os.path.join(v, "manifest.json")
            if os.path.exists(manifest_check):
                try:
                    with open(manifest_check, "r", encoding="utf-8") as f:
                        json.load(f)
                    valid_versions.append(v)
                except (json.JSONDecodeError, OSError):
                    logger.warning(f"⚠️ [MAINTENANCE] Dossier de version ignoré (manifest invalide) : {v}")

        if not valid_versions:
            raise FileNotFoundError(
                "Aucune version valide de l'extension Clemz trouvée dans Chrome "
                "(manifests illisibles ou absents)."
            )

        latest = max(valid_versions, key=self._version_key)
        return latest, os.path.basename(latest)

    def get_local_extension_version(self):
        """Lit la version actuellement chargée dans la copie locale (clemz_extension/manifest.json)."""
        manifest_path = os.path.join(self.extension_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            return None
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            return manifest.get("version")
        except (json.JSONDecodeError, OSError):
            logger.warning(f"⚠️ [MAINTENANCE] Manifest local illisible : {manifest_path}")
            return None

    def check_extension_status(self):
        """
        Compare la version locale (celle chargée par Playwright) à la dernière
        version disponible dans Chrome, sans rien copier. Utilisé par le front
        pour afficher un badge "à jour" / "mise à jour disponible".
        Opération synchrone -- à appeler via asyncio.to_thread() depuis une route.
        """
        local_version = self.get_local_extension_version()

        chrome_version = None
        chrome_found = False
        try:
            latest_dir, _ = self._find_latest_chrome_version()
            with open(os.path.join(latest_dir, "manifest.json"), "r", encoding="utf-8") as f:
                chrome_version = json.load(f).get("version")
            chrome_found = True
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

        up_to_date = (
            local_version is not None
            and chrome_version is not None
            and self._version_key_str(local_version) >= self._version_key_str(chrome_version)
        )

        return {
            "local_version": local_version,
            "chrome_version": chrome_version,
            "chrome_found": chrome_found,
            "up_to_date": up_to_date,
        }

    def _version_key_str(self, version_str):
        """Convertit une string 'x.y.z' en tuple comparable, pour comparer deux versions."""
        try:
            return tuple(int(x) for x in version_str.split("."))
        except (ValueError, AttributeError):
            return (0,)

    def update_clemz_extension(self):
        """
        Copie la dernière version de l'extension Clemz installée dans Chrome
        (mise à jour automatiquement par Chrome lui-même) vers la copie locale
        nettoyée utilisée par Playwright (backend/clemz_extension), puis retire
        update_url/key du manifest pour permettre le chargement en --load-extension
        (cf. learnings projet : Chrome 137+ a supprimé --load-extension pour les
        extensions installées normalement, d'où la copie locale nettoyée).

        Opération synchrone (I/O fichier uniquement) -- à appeler via
        asyncio.to_thread() depuis une route pour ne pas bloquer l'event loop.
        Lève FileNotFoundError si l'extension n'est pas trouvée dans Chrome ou
        si aucun dossier de version ne contient un manifest.json exploitable.
        """
        latest, version_number = self._find_latest_chrome_version()

        logger.info(f"📦 [MAINTENANCE] Version Clemz détectée dans Chrome : {version_number}")

        if os.path.exists(self.extension_dir):
            shutil.rmtree(self.extension_dir)
        shutil.copytree(latest, self.extension_dir)

        manifest_path = os.path.join(self.extension_dir, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        manifest.pop("update_url", None)
        manifest.pop("key", None)

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=3, ensure_ascii=False)

        self.extension_info = {
            "version": version_number,
            "updated_at": datetime.now().isoformat(),
        }

        logger.info(f"✅ [MAINTENANCE] Extension Clemz mise à jour vers {version_number}")
        return self.extension_info

    def _on_watchdog_task_done(self, task: asyncio.Task):
        """
        Callback appelé quand la task watchdog se termine, pour QUELQUE raison que ce
        soit (même logique que l'ancien _on_watchdog_task_done de main.py) -- logue
        l'erreur éventuelle sans jamais faire planter le reste du serveur.
        """
        self.watchdog_active = False
        if task.cancelled():
            logger.info("📨 [MAINTENANCE] Watchdog 'Messages automatiques' arrêté (annulation demandée).")
            return
        exc = task.exception()
        if exc is not None:
            logger.error(f"❌ [MAINTENANCE] Watchdog 'Messages automatiques' arrêté suite à une erreur : {exc}")

    def reset_extension_state(self, key: str):
        """
        Vide le stockage local de l'extension Clemz (chrome.storage.local +
        IndexedDB éventuel) pour le profil donné, en supprimant directement les
        fichiers concernés sur disque. Nécessaire après un crash de
        l'automatisation : ce stockage survit à la fermeture du navigateur
        (contrairement à une fermeture propre où Clemz nettoie son propre écran),
        et Clemz redémarre alors figé sur l'état laissé par le crash (envoi en
        cours, temporisation, etc.).

        Motif générique "chrome-extension_*" plutôt qu'un ID d'extension codé en
        dur : l'extension est chargée en unpacked (--load-extension) sans "key"
        dans le manifest (retiré par update_clemz_extension), donc son ID est
        généré dynamiquement par Chrome à partir du chemin -- pas trivial à
        reproduire côté Python de façon fiable.

        Opération synchrone (I/O fichier) -- à appeler via asyncio.to_thread()
        depuis la route. Lève ValueError si le profil est inconnu ou n'utilise
        pas Clemz, RuntimeError si le profil est actuellement ouvert (fichiers
        verrouillés par le navigateur).
        """
        profile = self.profiles.get(key)
        if not profile:
            raise ValueError("Profil de navigateur inconnu.")
        if not profile["extension"]:
            raise ValueError(f"{profile['name']} n'utilise pas l'extension Clemz -- rien à réinitialiser.")
        if self.status.get(key) in ("ouverture", "ouvert"):
            raise RuntimeError(f"{profile['name']} est actuellement ouvert -- ferme-le avant de réinitialiser.")

        default_dir = os.path.join(profile["session"], "Default")
        removed = []

        ext_settings_dir = os.path.join(default_dir, "Local Extension Settings")
        if os.path.isdir(ext_settings_dir):
            shutil.rmtree(ext_settings_dir, ignore_errors=True)
            # Annoncé seulement si le dossier existait vraiment -- avant ce
            # correctif, "removed" listait toujours "Local Extension Settings"
            # même quand il n'y avait rien à supprimer, rendant le retour
            # visuel ajouté côté frontend (cf. échange du 21/09/2026) trompeur
            # sur un profil déjà propre.
            removed.append("Local Extension Settings")

        for path in glob.glob(os.path.join(default_dir, "IndexedDB", "chrome-extension_*")):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    continue
            removed.append(os.path.basename(path))

        logger.info(
            f"🧹 [MAINTENANCE] État extension Clemz réinitialisé pour {profile['name']} : "
            f"{', '.join(removed) if removed else 'rien à supprimer (déjà propre)'}"
        )
        return {"profile": profile["name"], "removed": removed}


    def start_watchdog(self):
        """Démarre le watchdog 'Messages automatiques' (Chrome + Edge) en tâche de fond."""
        self.watchdog_desired_active = True

        if self.watchdog_task and not self.watchdog_task.done():
            logger.warning("⚠️ [MAINTENANCE] Watchdog déjà actif.")
            return

        watchdog = ClemzAutoMessageWatchdog()
        self.watchdog_task = asyncio.create_task(watchdog.run_forever())
        self.watchdog_task.add_done_callback(self._on_watchdog_task_done)
        self.watchdog_active = True
        logger.info("📨 [MAINTENANCE] Watchdog 'Messages automatiques' démarré (Chrome + Edge).")

    async def stop_watchdog(self):
        """Arrête proprement le watchdog 'Messages automatiques' s'il tourne."""
        self.watchdog_desired_active = False

        if not self.watchdog_task or self.watchdog_task.done():
            self.watchdog_active = False
            return

        self.watchdog_task.cancel()
        try:
            await self.watchdog_task
        except asyncio.CancelledError:
            pass
        self.watchdog_active = False
        logger.info("📨 [MAINTENANCE] Watchdog 'Messages automatiques' arrêté proprement.")

    def get_watchdog_status(self):
        return {"active": self.watchdog_active}

    def check_and_restart_watchdog_if_needed(self):
        """
        Vérification périodique (appelée toutes les 30 min par le scheduler) :
        si l'utilisateur veut le watchdog actif (watchdog_desired_active, réglé
        via le toggle Maintenance) mais que la tâche réelle est arrêtée/absente
        (crash, exception non prévue, jamais démarrée après un redémarrage
        backend...), on la relance via start_watchdog(). Ne touche jamais à
        watchdog_desired_active -- respecte toujours la dernière décision
        manuelle de l'utilisateur, marche ou arrêt.
        """
        if not self.watchdog_desired_active:
            logger.info("🔁 [MAINTENANCE] Vérification watchdog : désactivé par l'utilisateur, rien à faire.")
            return {"action": "none", "reason": "Watchdog désactivé manuellement -- rien à faire."}

        if self.watchdog_task and not self.watchdog_task.done():
            logger.info("🔁 [MAINTENANCE] Vérification watchdog : tâche déjà active, aucun redémarrage nécessaire.")
            return {"action": "none", "reason": "Watchdog déjà actif, aucun redémarrage nécessaire."}

        logger.warning("🔁 [MAINTENANCE] Vérification watchdog : désiré actif mais tâche arrêtée -- relance automatique.")
        self.start_watchdog()
        return {"action": "restarted", "reason": "Tâche watchdog relancée après détection d'arrêt inattendu."}

    def _load_baisse_prix_auto_active(self):
        if not os.path.exists(self.baisse_prix_auto_settings_file):
            return True
        try:
            with open(self.baisse_prix_auto_settings_file, "r", encoding="utf-8") as f:
                return json.load(f).get("active", True)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"⚠️ [MAINTENANCE] Erreur lecture réglage baisse de prix auto : {e}")
            return True

    def set_baisse_prix_auto_active(self, active: bool):
        self.baisse_prix_auto_active = active
        with open(self.baisse_prix_auto_settings_file, "w", encoding="utf-8") as f:
            json.dump({"active": active}, f)
        logger.info(f"💸 [MAINTENANCE] Baisse de prix automatique quotidienne : {'activée' if active else 'désactivée'}.")

    def get_baisse_prix_auto_status(self):
        return {"active": self.baisse_prix_auto_active}

    def _load_partage_vues_favoris_auto_active(self):
        if not os.path.exists(self.partage_vues_favoris_auto_settings_file):
            return True
        try:
            with open(self.partage_vues_favoris_auto_settings_file, "r", encoding="utf-8") as f:
                return json.load(f).get("active", True)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"⚠️ [MAINTENANCE] Erreur lecture réglage partage vues/favoris auto : {e}")
            return True

    def set_partage_vues_favoris_auto_active(self, active: bool):
        self.partage_vues_favoris_auto_active = active
        with open(self.partage_vues_favoris_auto_settings_file, "w", encoding="utf-8") as f:
            json.dump({"active": active}, f)
        logger.info(f"👀 [MAINTENANCE] Partage vues/favoris automatique : {'activé' if active else 'désactivé'}.")

    def get_partage_vues_favoris_auto_status(self):
        return {"active": self.partage_vues_favoris_auto_active}

    def _load_scraping_auto_active(self):
        if not os.path.exists(self.scraping_auto_settings_file):
            return True
        try:
            with open(self.scraping_auto_settings_file, "r", encoding="utf-8") as f:
                return json.load(f).get("active", True)
        except (json.JSONDecodeError, OSError):
            return True

    def set_scraping_auto_active(self, active: bool):
        self.scraping_auto_active = active
        with open(self.scraping_auto_settings_file, "w", encoding="utf-8") as f:
            json.dump({"active": active}, f)

    def get_scraping_auto_status(self):
        return {"active": self.scraping_auto_active}

    def _load_republication_auto_active(self):
        if not os.path.exists(self.republication_auto_settings_file):
            return True
        try:
            with open(self.republication_auto_settings_file, "r", encoding="utf-8") as f:
                return json.load(f).get("active", True)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"⚠️ [MAINTENANCE] Erreur lecture réglage republication auto : {e}")
            return True

    def set_republication_auto_active(self, active: bool):
        self.republication_auto_active = active
        with open(self.republication_auto_settings_file, "w", encoding="utf-8") as f:
            json.dump({"active": active}, f)
        logger.info(f"♻️ [MAINTENANCE] Republication automatique : {'activée' if active else 'désactivée'}.")

    def get_republication_auto_status(self):
        return {"active": self.republication_auto_active}

    def _load_clemz_visible_mode(self):
        if not os.path.exists(self.clemz_visible_settings_file):
            return False
        try:
            with open(self.clemz_visible_settings_file, "r", encoding="utf-8") as f:
                return json.load(f).get("visible", False)
        except (json.JSONDecodeError, OSError):
            return False

    def set_clemz_visible_mode(self, visible: bool):
        """
        Bascule le mode "navigateur visible" pour le scraping -- remplace le
        réglage .env CLEMZ_VISIBLE (qui exigeait un redémarrage complet du
        backend pour prendre effet). Persisté sur disque (contrairement au
        watchdog, volontairement toujours désactivé au démarrage et piloté
        exclusivement via le toggle Maintenance) : ce réglage survit désormais
        à un redémarrage du backend.
        """
        self.clemz_visible_mode = visible
        with open(self.clemz_visible_settings_file, "w", encoding="utf-8") as f:
            json.dump({"visible": visible}, f)
        logger.info(f"👁️ [MAINTENANCE] Mode navigateur visible (scraping) : {'activé' if visible else 'désactivé'}.")

    def get_clemz_visible_mode(self):
        return {"visible": self.clemz_visible_mode}


# Singleton partagé par les routes (même pattern que les autres services du projet)
maintenance_service = MaintenanceService()