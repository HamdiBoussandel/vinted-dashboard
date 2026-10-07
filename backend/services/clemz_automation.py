import os
import asyncio
import logging
import sys
import re
from playwright.async_api import async_playwright
import random
from datetime import date

# Import robuste : fonctionne à la fois en exécution directe depuis services/
# (python run_test_baisse_prix.py, run_test_complet.py) et en import package
# via l'app FastAPI complète (main.py -> routes/inventory.py -> services.clemz_automation).
try:
    from clemz_cdp import (
        snapshot_by_id,
        get_live_props,
        click_element,
        set_input_value,
        set_select_value,
        query_selector_in,
        get_center_coords,
        real_mouse_click,
        ensure_panel_open,
        force_panel_visible,
    )
    from session_manager import ensure_session
except ImportError:
    from services.clemz_cdp import (
        snapshot_by_id,
        get_live_props,
        click_element,
        set_input_value,
        set_select_value,
        query_selector_in,
        get_center_coords,
        real_mouse_click,
        ensure_panel_open,
        force_panel_visible,
    )
    from services.session_manager import ensure_session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REPOST_TIMEOUT_MIN_SECONDS = 300       # 5 min plancher, même pour 1 seul article
REPOST_TIMEOUT_PER_ITEM_SECONDS = 240  # relevé de 90s -> 240s (4 min) suite au mode "très lent" +
                                        # pauses aléatoires introduites par Clemz. Valeur volontairement
                                        # généreuse en l'absence de mesure précise (durée annoncée comme
                                        # aléatoire) -- à resserrer plus tard si les logs réels montrent
                                        # une marge trop large (temps d'attente inutile en fin de batch).

# --- Mode pas-à-pas : passe à False pour désactiver toutes les pauses d'un coup ---
STEP_BY_STEP = False


def _pause(message):
    """Pause manuelle de vérification. N'a aucun effet si STEP_BY_STEP=False."""
    if STEP_BY_STEP:
        input(f"\n⏸️  {message} (Entrée pour continuer)...")


async def _close_clemz_promo_tabs(context, keep_page):
    """
    Ferme automatiquement les onglets promotionnels que l'extension Clemz ouvre
    parfois au démarrage (ex: clemz.app/documentation/demarrage-rapide), pour
    éviter d'encombrer les fenêtres à chaque lancement de l'automatisation.
    Ne touche jamais keep_page (l'onglet Vinted principal).
    """
    async def _maybe_close(new_page):
        try:
            await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        if new_page != keep_page and "clemz.app" in (new_page.url or ""):
            logger.info(f"🧹 Fermeture de l'onglet promo Clemz : {new_page.url}")
            try:
                await new_page.close()
            except Exception:
                pass

    # Onglets déjà ouverts au moment du lancement
    for p in list(context.pages):
        if p != keep_page and "clemz.app" in (p.url or ""):
            await _maybe_close(p)

    # Onglets ouverts un peu plus tard (l'extension peut ouvrir le sien après un délai)
    context.on("page", lambda p: asyncio.create_task(_maybe_close(p)))


async def _check_and_dismiss_major_error(cdp):
    """
    Détecte la modale bloquante "🛑 La liste contient une erreur 🛑" (constatée le
    17/08/2026) — se déclenche quand Clemz supprime l'ancienne annonce mais échoue
    à créer la nouvelle en plein milieu d'une action de masse. Sans gestion, cette
    modale bloque silencieusement toute la suite du batch jusqu'au timeout global.

    Choix délibéré : on clique systématiquement "Ignorer" (cancelMajorErrorModification),
    l'option la moins risquée (ne fait rien, laisse l'article tel quel dans la
    liste) plutôt que "Importer depuis Clemz" (risque de doublon) -- décision
    manuelle nécessaire ensuite, jamais automatisée.

    Retourne {"nom": ..., "url": ...} si une erreur a été détectée et ignorée,
    None sinon.
    """
    by_id = await snapshot_by_id(cdp)
    if "cancelMajorErrorModification" not in by_id:
        return None

    nom = "Article inconnu"
    url = None
    if "itemTitle" in by_id:
        props = await get_live_props(cdp, by_id["itemTitle"]["node_id"], props=("innerText",))
        nom = props.get("innerText", nom)
    if "itemUrl" in by_id:
        props = await get_live_props(cdp, by_id["itemUrl"]["node_id"], props=("href",))
        url = props.get("href")

    logger.error(
        f"🛑 Erreur majeure Clemz détectée sur '{nom}' ({url or 'URL inconnue'}) — "
        f"ancienne annonce supprimée, nouvelle non créée. Clic sur 'Ignorer' — "
        f"intervention manuelle nécessaire sur cet article."
    )
    await click_element(cdp, by_id["cancelMajorErrorModification"]["node_id"])
    await asyncio.sleep(1)
    return {"nom": nom, "url": url}


async def _check_captcha_blocage(cdp):
    """
    Détecte les 3 modales anti-bot natives de Clemz (trouvées dans le code
    source de l'extension, backend/clemz_extension/html/globalModals/) --
    jamais vérifiées jusqu'ici par notre automatisation. Constaté le
    03/10/2026 : se déclenche surtout en lançant une SÉRIE de baisses de prix,
    peu importe les articles choisis -- signal probable sur le RYTHME/VOLUME
    de l'action de masse elle-même, pas sur son contenu.

    - #captchaIframe (captchaResolvingModal.html) : CAPTCHA embarqué à résoudre.
    - #closeCaptchaWarningButton (blockedByCaptchaModal.html) : blocage captcha
      plus sévère (cookies/activité suspecte).
    - #continueButton (botChallengeModal.html) : vérification "prouve que tu es
      humain" (suivre un lien externe, attendre, continuer).

    Aucune des 3 n'est résolvable automatiquement par construction (c'est le
    principe d'un captcha) -- contrairement à _check_and_dismiss_major_error
    ci-dessus, cette fonction ne "continue" jamais : elle signale juste qu'une
    intervention humaine est nécessaire, pour que l'appelant arrête d'attendre
    un "terminé" qui ne viendra jamais et remonte une raison claire plutôt
    qu'un timeout générique après MAX_WAIT.

    Retourne une description du blocage si détecté, None sinon.
    """
    by_id = await snapshot_by_id(cdp)
    if "captchaIframe" in by_id:
        return "CAPTCHA Vinted affiché -- résolution manuelle nécessaire (fenêtre de résolution Clemz)."
    if "closeCaptchaWarningButton" in by_id:
        return "Vinted bloque temporairement par captcha (activité jugée suspecte) -- intervention manuelle nécessaire."
    if "continueButton" in by_id:
        return "Vinted demande une vérification anti-bot (\"prouve que tu es humain\") -- intervention manuelle nécessaire."
    return None


# Temps laissé pour une résolution manuelle à distance (connexion au PC +
# glisser le slider) avant d'abandonner la tâche -- cf. _attendre_resolution_captcha_manuelle.
CAPTCHA_WAIT_TIMEOUT_SECONDS = 15 * 60
CAPTCHA_POLL_INTERVAL_SECONDS = 5


async def _attendre_resolution_captcha_manuelle(cdp, acc, captcha_reason, action_label):
    """
    Un captcha Vinted (slider à faire glisser à la main, cf. échange du
    03/10/2026) ne doit JAMAIS être résolu automatiquement -- contourner une
    vérification anti-bot n'est pas quelque chose que je ferai, même pour ce
    compte. En revanche, la tâche ne doit pas échouer immédiatement dès sa
    détection ("rarement arrêtée") : elle attend ici, en repollant la
    disparition de la modale (= résolution manuelle effectuée), jusqu'à
    CAPTCHA_WAIT_TIMEOUT_SECONDS -- largement au-dessus du temps de connexion
    à distance + résolution, mais pas infini, pour que la tâche finisse par
    abandonner si vraiment personne ne répond (inactivité prolongée).

    Retourne True si la modale a disparu (résolue) avant le délai, False sinon.
    """
    logger.warning(
        f"⏸️ [{acc['name']}] En pause, en attente de résolution manuelle du captcha "
        f"(jusqu'à {CAPTCHA_WAIT_TIMEOUT_SECONDS // 60} min) : {captcha_reason}"
    )
    elapsed = 0
    while elapsed < CAPTCHA_WAIT_TIMEOUT_SECONDS:
        await asyncio.sleep(CAPTCHA_POLL_INTERVAL_SECONDS)
        elapsed += CAPTCHA_POLL_INTERVAL_SECONDS
        if await _check_captcha_blocage(cdp) is None:
            logger.info(f"✅ [{acc['name']}] Captcha résolu après {elapsed}s — reprise de {action_label}.")
            return True
        if elapsed % 60 == 0:
            logger.info(
                f"⏳ [{acc['name']}] Toujours en attente de résolution captcha... "
                f"({(CAPTCHA_WAIT_TIMEOUT_SECONDS - elapsed) // 60} min restantes)"
            )
    logger.error(f"❌ [{acc['name']}] Captcha toujours présent après {CAPTCHA_WAIT_TIMEOUT_SECONDS // 60} min — abandon.")
    return False


async def _positionner_hors_ecran(context, page):
    """
    Force la fenêtre hors de l'écran visible (coordonnées largement négatives)
    OU la ramène visiblement à l'écran (CLEMZ_VISIBLE=1), selon le réglage --
    dans les deux cas, la position est ACTIVEMENT forcée via CDP, jamais laissée
    telle quelle. Nécessaire même en mode visible : le profil Chromium
    persistant se souvient de la DERNIÈRE position connue (-3000,-3000 en usage
    normal), donc "ne rien faire" laisserait la fenêtre hors écran quand même au
    prochain lancement, peu importe CLEMZ_VISIBLE.

    CLEMZ_VISIBLE relu à CHAQUE appel (pas figé en constante de module) --
    lire os.getenv() une seule fois à l'import du fichier risquait de capturer
    "0" si ce module est importé avant que database.py ait appelé load_dotenv(),
    figeant la valeur pour tout le reste de l'exécution même avec un .env
    correctement configuré.
    """
    # Lu dynamiquement depuis le toggle dashboard (page Maintenance) plutôt que
    # le .env -- prend effet immédiatement, sans redémarrer le backend. Import
    # différé pour éviter tout risque de dépendance circulaire au chargement du module.
    from services.maintenance_service import maintenance_service
    clemz_visible = maintenance_service.get_clemz_visible_mode()["visible"]

    try:
        cdp = await context.new_cdp_session(page)
        info_fenetre = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds", {
            "windowId": info_fenetre["windowId"],
            "bounds": {"windowState": "normal"},
        })

        if clemz_visible:
            logger.info("👁️ [CLEMZ_VISIBLE=1] Navigateur ramené visiblement à l'écran, maximisé (mode test).")
            # Deux appels nécessaires : Chromium ignore windowState="maximized" s'il est
            # combiné avec des coordonnées left/top/width/height dans le même appel --
            # on repasse d'abord en "normal" à une position connue, PUIS on maximise.
            await cdp.send("Browser.setWindowBounds", {
                "windowId": info_fenetre["windowId"],
                "bounds": {"left": 0, "top": 0, "width": 1280, "height": 800, "windowState": "normal"},
            })
            await cdp.send("Browser.setWindowBounds", {
                "windowId": info_fenetre["windowId"],
                "bounds": {"windowState": "maximized"},
            })
        else:
            await cdp.send("Browser.setWindowBounds", {
                "windowId": info_fenetre["windowId"],
                "bounds": {"left": -3000, "top": -3000, "width": 1280, "height": 800, "windowState": "normal"},
            })
    except Exception as e:
        logger.warning(f"⚠️ Positionnement fenêtre échoué (non bloquant) : {e}")


def _args_et_kwargs_navigateur(acc, args_specifiques):
    """
    Construit (args, kwargs_supplementaires) pour launch_persistent_context à
    partir d'un compte de ClemzAutomation.accounts. Cas normal (Chromium
    embarqué) : ajoute --load-extension/--disable-extensions-except, comme
    avant. Cas d'un compte migré vers un vrai navigateur avec l'extension déjà
    installée manuellement dans le profil (acc["channel"] défini, ex:
    "chrome") : pas de --load-extension, ignore_default_args neutralise le
    --disable-extensions que Playwright ajoute par défaut et qui désactiverait
    sinon l'extension déjà présente dans le profil.
    """
    if acc.get("channel"):
        return list(args_specifiques), {
            "channel": acc["channel"],
            "ignore_default_args": [
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ],
        }
    args = [
        f"--disable-extensions-except={acc['extension']}",
        f"--load-extension={acc['extension']}",
    ] + list(args_specifiques)
    return args, {}


class ClemzAutomation:
    def __init__(self, produits_d1, produits_d2, task_type="republication", pourcentage_baisse_prix=20, prix_fixe=None):
        self.clean_d1 = [p.strip() for p in produits_d1 if p]
        self.clean_d2 = [p.strip() for p in produits_d2 if p]
        self.task_type = task_type  # "republication" ou "baisse_prix" — seule l'étape d'action change
        # Centralisé ici (au lieu d'être répété en dur à chaque appel de
        # _trigger_baisse_prix) pour garantir que la valeur RÉELLEMENT appliquée
        # est celle enregistrée dans price_drops -- une seule source de vérité.
        self.pourcentage_baisse_prix = pourcentage_baisse_prix
        # Mode "prix fixe" (ajouté le 03/10/2026, liquidation) -- distinct du mode
        # pourcentage ci-dessus : utilise le mode natif "set" de Clemz
        # (#modifyPriceDirection="set", #modifyPriceType="€") pour fixer TOUS les
        # articles du lot à EXACTEMENT ce prix en une seule action, plutôt qu'une
        # baisse relative en %. Prioritaire sur pourcentage_baisse_prix quand
        # renseigné (cf. _trigger_baisse_prix).
        self.prix_fixe = prix_fixe

        _base = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

        self.accounts = [
            {
                # MIGRÉ (08/09/2026) : vrai Chrome + Clemz préchargée manuellement
                # dans le profil, même traitement que Dressing 2 -- voir
                # _args_et_kwargs_navigateur. Coïncide avec le changement de compte
                # Vinted de Dressing 1.
                "name": "Chrome - Dressing 1",
                "account_key": "chrome_clemz",  # cf. session_manager.ACCOUNTS
                "produits": self.clean_d1,
                "executable": None,
                "extension": None,
                "session": os.path.join(_base, "clemz_session_chrome_d1_reel"),
                "url": "https://www.vinted.fr/member/287248160",
                "channel": "chrome",
            },
            {
                # MIGRÉ (05/09/2026) : vrai Chrome + Clemz préchargée manuellement
                # dans le profil (chrome://extensions > Charger l'extension non
                # empaquetée) -- corrige l'empreinte TLS/JA3-JA4 du Chromium
                # embarqué. Plus de --load-extension : voir _args_et_kwargs_navigateur.
                "name": "Chrome - Dressing 2",
                "account_key": "edge_clemz",  # cf. session_manager.ACCOUNTS -- clé historique, sans lien avec le nom affiché
                "produits": self.clean_d2,
                "executable": None,
                "extension": None,
                "session": os.path.join(_base, "clemz_session_chrome_reel"),
                "url": "https://www.vinted.fr/member/3136979514",
                "channel": "chrome",
            }
        ]

    async def _build_list(self, page, cdp, acc):
        """
        Constitue la liste Clemz : Smart Dressing -> recherche par produit ->
        sélection de la carte -> ajout à la liste, pour chaque produit du compte.
        Retourne une liste de résultats par produit :
        [{"nom": ..., "status": "success"/"failed", "reason": ...}]
        """
        results = []

        # --- Ouverture panneau + onglet Mon dressing + lancement Smart Dressing ---
        by_id = await snapshot_by_id(cdp)
        if "myDressingTabLink" not in by_id:
            return [{"nom": p, "status": "failed", "reason": "Panneau Clemz introuvable (toolBody/myDressingTabLink absent)"} for p in acc["produits"]]

        await click_element(cdp, by_id["myDressingTabLink"]["node_id"])
        await asyncio.sleep(0.5)
        _pause("Clic sur l'onglet 'Mon dressing' effectué — vérifie visuellement")

        # Capture du texte AVANT le clic, pour détecter un vrai changement d'état ensuite
        # (le texte résiduel d'un run précédent peut déjà contenir 'prêt' avant même de cliquer,
        # à cause de la session persistante -> #processStage pas réinitialisé au clic).
        by_id = await snapshot_by_id(cdp)
        baseline_text = ""
        if "processStage" in by_id:
            props = await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))
            baseline_text = props.get("innerText", "").lower()

        # CORRECTIF : ne JAMAIS supposer que "Tout mon dressing" est déjà coché par défaut --
        # vrai sur un profil déjà utilisé (état mémorisé), FAUX sur un profil neuf (aucun radio
        # coché du tout, name="myDressingTargetedItems" vierge) comme découvert sur Edge/Dressing 2
        # fraîchement configuré. Cliquer sur un radio déjà sélectionné ne change rien, donc on le
        # fait systématiquement, sans condition -- même logique que myDressingTargetList dans
        # _trigger_save_backup un peu plus bas dans ce fichier (même groupe de radios, l'autre option).
        if "myDressingTargetAll" in by_id:
            await click_element(cdp, by_id["myDressingTargetAll"]["node_id"])
            await asyncio.sleep(0.2)
            _pause("Radio 'Tout mon dressing' sélectionné explicitement")
        else:
            logger.warning(f"⚠️ [{acc['name']}] Radio 'Tout mon dressing' (myDressingTargetAll) introuvable — "
                            f"on suppose qu'il est déjà coché (ancien comportement), à vérifier.")

        await click_element(cdp, by_id["myDressingButton"]["node_id"])  # "Lancer !"
        _pause(f"Clic sur 'Lancer !' effectué — texte de référence AVANT clic : {baseline_text!r}")

        ready = False
        elapsed = 0
        MAX_WAIT_SMART_DRESSING = 120  # secondes, garde-fou
        POLL_INTERVAL = 1
        has_changed = False
        while elapsed < MAX_WAIT_SMART_DRESSING:
            by_id = await snapshot_by_id(cdp)
            if "processStage" in by_id:
                props = await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))
                text = props.get("innerText", "").lower()

                if not has_changed and text != baseline_text:
                    has_changed = True
                    logger.info(f"   [DIAG] #processStage a changé : {text!r}")

                if has_changed and "prêt" in text:
                    ready = True
                    break
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

        if not ready:
            logger.error(
                f"🔬 [DIAG SMART DRESSING TIMEOUT] [{acc['name']}] Panneau jamais devenu 'prêt' après "
                f"{MAX_WAIT_SMART_DRESSING}s -- changement de #processStage détecté ? {has_changed}. "
                f"Probablement une contention au lancement si un autre compte tournait en parallèle "
                f"au même moment (CPU/réseau) -- voir le délai de démarrage échelonné ajouté."
            )
            return [{"nom": p, "status": "failed", "reason": "Timeout Smart Dressing (non prêt après 120s)"} for p in acc["produits"]]

        _pause(f"Smart Dressing prêt après {elapsed}s (changement réel détecté)")

        # --- Réduire le panneau de progression, puis restaurer la vue normale ---
        by_id = await snapshot_by_id(cdp)
        if "progressMinimizeButton" in by_id:
            await click_element(cdp, by_id["progressMinimizeButton"]["node_id"])
            await asyncio.sleep(0.5)
            _pause("Clic sur 'Réduire' effectué")

            # Le panneau reste en mode réduit après ce clic -> on force sa visibilité directement,
            # plutôt que de cliquer #miniVinzLogo (même bouton toggle que #miniVinz : un clic à
            # l'aveugle risquerait de refermer le panneau au lieu de le restaurer).
            await force_panel_visible(cdp)
            await asyncio.sleep(0.5)
            _pause("force_panel_visible() appliqué — le panneau doit être revenu en vue normale")

        # --- Boucle produit par produit ---
        for index, produit in enumerate(acc["produits"]):
            logger.info(f"🔍 [{acc['name']}] {index + 1}/{len(acc['produits'])} : {produit}")

            # Le panneau peut être resté masqué après la fermeture de la popup de confirmation
            # du produit précédent (progressCloseButton) -> on s'assure qu'il est bien visible
            # avant d'attaquer ce nouveau produit.
            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() en début d'itération produit {index + 1}/{len(acc['produits'])} -> {reopened}")

            _pause(f"Début du traitement du produit {index + 1}/{len(acc['produits'])} : '{produit}'")

            try:
                # 1. Onglet listes (idempotent, coût faible)
                by_id = await snapshot_by_id(cdp)
                if "listsTabLink" not in by_id:
                    # DIAG ciblé, désormais inclus dans la raison stockée (visible dans
                    # TaskHistory), pas seulement dans les logs serveur.
                    current_url = page.url
                    has_toolbody = "toolBody" in by_id
                    has_minivinz = "miniVinz" in by_id
                    diag_reason = (
                        f"Onglet listes introuvable (panneau probablement perdu) — "
                        f"url={current_url!r}, toolBody={has_toolbody}, miniVinz={has_minivinz}"
                    )
                    logger.error(f"   🔬 [DIAG PANNEAU PERDU] produit='{produit}' {diag_reason}")
                    results.append({"nom": produit, "status": "failed", "reason": diag_reason})
                    continue
                await click_element(cdp, by_id["listsTabLink"]["node_id"])
                await asyncio.sleep(0.3)
                _pause("Clic sur l'onglet 'listes' effectué")

                # 2. Recherche : vidage puis saisie du nom exact
                by_id = await snapshot_by_id(cdp)
                if "searchWordForm" not in by_id:
                    logger.warning(f"   ⚠️ '{produit}' : Champ de recherche introuvable — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Champ de recherche introuvable"})
                    continue

                form_node_id = by_id["searchWordForm"]["node_id"]
                input_node_id = await query_selector_in(cdp, form_node_id, 'input[type="text"]')
                submit_node_id = await query_selector_in(cdp, form_node_id, 'button[type="submit"]')
                if not input_node_id or not submit_node_id:
                    logger.warning(f"   ⚠️ '{produit}' : Input/bouton recherche introuvable — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Input/bouton recherche introuvable"})
                    continue

                await set_input_value(cdp, input_node_id, "")
                await asyncio.sleep(0.2)
                await set_input_value(cdp, input_node_id, produit)
                await asyncio.sleep(0.3)

                readback = await get_live_props(cdp, input_node_id, props=("value",))
                logger.info(f"   [DIAG] Valeur lue dans le champ après saisie : {readback}")
                _pause(f"Champ de recherche rempli avec '{produit}' — vérifie visuellement avant le clic 'ok'")

                await click_element(cdp, submit_node_id)
                _pause("Clic sur 'ok' effectué")

                # Le clic 'ok' peut faire perdre la visibilité du panneau -> on s'assure qu'il reste ouvert
                reopened = await ensure_panel_open(cdp, page)
                _pause(f"ensure_panel_open() après recherche -> {reopened}")

                # Poll de l'apparition réelle des résultats filtrés (au lieu d'un sleep fixe).
                # CORRECTIF : la grille "Smart Dressing" de Clemz vit dans son propre shadow root
                # fermé -- document.querySelectorAll() via page.evaluate() ne peut PAS l'atteindre
                # (seul CDP le peut). L'ancienne version ciblait par erreur les classes natives de
                # la grille Vinted, jamais celles de Clemz -> le poll échouait quasi systématiquement
                # même quand le produit était bien visible dans la grille Smart Dressing.
                found_grid = False
                for _ in range(15):  # ~4.5s max
                    by_id_poll = await snapshot_by_id(cdp)
                    if "smartDressingGrid" in by_id_poll:
                        item_check = await query_selector_in(
                            cdp, by_id_poll["smartDressingGrid"]["node_id"], ".smart-dressing-item-img"
                        )
                        if item_check:
                            found_grid = True
                            break
                    await asyncio.sleep(0.3)

                if not found_grid:
                    logger.warning(f"   ⚠️ '{produit}' : Aucun résultat dans la grille après recherche — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Aucun résultat dans la grille après recherche"})
                    continue
                _pause("Grille filtrée trouvée — vérifie visuellement le résultat")

                # Le panneau doit être visible AVANT d'activer le mode sélection
                reopened = await ensure_panel_open(cdp, page)
                _pause(f"ensure_panel_open() avant mode sélection -> {reopened}")

                # 3. Activer le mode sélection
                by_id = await snapshot_by_id(cdp)
                if "selectItemsButton" not in by_id:
                    logger.warning(f"   ⚠️ '{produit}' : Bouton 'Lancer la selection' introuvable — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Bouton 'Lancer la selection' introuvable"})
                    continue
                await click_element(cdp, by_id["selectItemsButton"]["node_id"])
                await asyncio.sleep(0.5)
                _pause("Clic sur 'Lancer la selection' effectué — le mode sélection doit être actif")

                # Le panneau doit être visible AVANT le clic sur la carte, sinon le clic ne sera pas
                # comptabilisé par Clemz même s'il a bien lieu au niveau DOM.
                reopened = await ensure_panel_open(cdp, page)
                _pause(f"ensure_panel_open() avant clic carte -> {reopened}")

                # 4. Clic sur la première carte produit filtrée — VRAI clic souris (pas .click() JS,
                # qui est non-trusted et ne déclenche pas la logique de sélection de Clemz)
                # 4. Clic sur la carte dans la grille SMART DRESSING de Clemz (rendue dans son propre
                # shadow root — pas la grille native Vinted qu'on ciblait par erreur jusqu'ici)
                by_id = await snapshot_by_id(cdp)
                if "smartDressingGrid" not in by_id:
                    logger.warning(f"   ⚠️ '{produit}' : Grille Smart Dressing introuvable — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Grille Smart Dressing introuvable"})
                    continue

                item_node_id = await query_selector_in(
                    cdp, by_id["smartDressingGrid"]["node_id"], ".smart-dressing-item-img"
                )
                if not item_node_id:
                    logger.warning(f"   ⚠️ '{produit}' : Carte produit introuvable dans la grille Smart Dressing — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Carte produit introuvable dans la grille Smart Dressing"})
                    continue

                await click_element(cdp, item_node_id)
                await asyncio.sleep(0.4)

                # --- DIAGNOSTIC : état de la carte et du compteur APRÈS clic ---
                card_after = await page.evaluate("""
                    () => {
                        const el = document.querySelector('.new-item-box__overlay') ||
                                   document.querySelector('.feed-grid__item');
                        return el ? { className: el.className, outerHTML: el.outerHTML.slice(0, 200) } : null;
                    }
                """)
                logger.info(f"   [DIAG] Carte APRÈS clic : {card_after}")

                by_id_after_click = await snapshot_by_id(cdp)
                if "selectItemsButton" in by_id_after_click:
                    sel_after = await get_live_props(cdp, by_id_after_click["selectItemsButton"]["node_id"], props=("innerText",))
                    logger.info(f"   [DIAG] Compteur sélection APRÈS clic : {sel_after}")

                _pause("Clic souris réel sur la carte effectué — vérifie visuellement le cadre vert")

                # Le panneau doit rester visible AVANT le clic sur 'Ajouter'
                reopened = await ensure_panel_open(cdp, page)
                _pause(f"ensure_panel_open() avant clic 'Ajouter' -> {reopened}")

                # 5. Ajouter à la liste
                by_id = await snapshot_by_id(cdp)
                if "addToListButton" not in by_id:
                    logger.warning(f"   ⚠️ '{produit}' : Bouton 'Ajouter à la liste' introuvable — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Bouton 'Ajouter à la liste' introuvable"})
                    continue

                add_state = await get_live_props(cdp, by_id["addToListButton"]["node_id"], props=("disabled", "className"))
                _pause(f"État addToListButton avant clic : {add_state}")

                await click_element(cdp, by_id["addToListButton"]["node_id"])
                _pause("Clic sur 'Ajouter à la liste' effectué")

                # Poll de la popup de confirmation (au lieu d'un sleep fixe)
                popup_found = False
                for _ in range(10):  # ~3s max
                    by_id = await snapshot_by_id(cdp)
                    if "progressCloseButton" in by_id:
                        popup_found = True
                        break
                    await asyncio.sleep(0.3)

                if popup_found:
                    _pause("Popup 'Ajout à la liste terminé' détectée — vérifie visuellement")
                    await click_element(cdp, by_id["progressCloseButton"]["node_id"])
                    await asyncio.sleep(0.3)

                    # Comme #progressMinimizeButton plus haut : fermer cette popup replie le
                    # panneau dans un état que ensure_panel_open() seul ne restaure pas en
                    # début d'itération suivante -- constaté le 22/08/2026 (produit 1 réussi,
                    # puis "Onglet listes introuvable" en cascade sur tous les produits
                    # suivants, jamais rétabli). force_panel_visible() est nécessaire ici aussi.
                    await force_panel_visible(cdp)
                    await asyncio.sleep(0.3)
                    _pause("force_panel_visible() appliqué après fermeture popup — panneau doit être revenu en vue normale")

                    results.append({"nom": produit, "status": "success"})
                    logger.info(f"   ✅ {produit} ajouté avec succès.")
                    _pause("Popup fermée")
                else:
                    logger.warning(f"   ⚠️ '{produit}' : Popup de confirmation d'ajout non détectée — produit ignoré")
                    results.append({"nom": produit, "status": "failed", "reason": "Popup de confirmation d'ajout non détectée"})

            except Exception as e:
                results.append({"nom": produit, "status": "failed", "reason": f"Erreur inattendue : {str(e)[:120]}"})
                logger.error(f"   ❌ {produit} -> erreur : {e}")

            await asyncio.sleep(0.3)  # petit respiro entre deux produits, pas de sur-vitesse

        return results

    @staticmethod
    def _selection_results_finaux(selection_results, repost_result):
        """
        "success" dans selection_results ne veut dire que "ajouté à la liste
        Clemz avec succès" (étape de SÉLECTION) -- PAS "republié/baissé avec
        succès sur Vinted", qui dépend d'une action de masse ULTÉRIEURE
        partagée par tout le lot (repost_result, ex: clic sur "Voir mes
        listes" puis "Republier"). Si cette action de masse a échoué, AUCUN
        item n'a réellement été traité sur Vinted, même ceux marqués
        "success" à la sélection -- sans cette correction, le quota
        anti-détection (qui compte les "success") et l'historique affichaient
        des republications qui n'avaient en réalité jamais eu lieu (cf.
        échange du 21/09/2026 : 4/7 comptés "success" alors que le bouton
        "Voir mes listes" n'avait jamais pu être trouvé pour tout le lot).
        """
        if repost_result.get("status") == "success":
            return selection_results
        raison = repost_result.get("reason") or "Étape de republication/baisse de prix échouée après la sélection."
        return [
            {**r, "status": "failed", "reason": raison} if r["status"] == "success" else r
            for r in selection_results
        ]

    async def _navigate_to_repost_tab(self, cdp, page, acc):
        """
        Transition entre la constitution de liste et la republication :
        Voir mes listes (#showListsButton1) -> Republier (#repost-shortcut, dans #listsModal)
        -> bascule automatique sur l'onglet #repostTabLink.
        """
        reopened = await ensure_panel_open(cdp, page)
        _pause(f"ensure_panel_open() en entrée de navigation Republier -> {reopened}")

        by_id = await snapshot_by_id(cdp)
        if "showListsButton1" not in by_id:
            logger.error(
                f"   🔬 [DIAG PANNEAU PERDU] url={page.url!r} toolBody_present={'toolBody' in by_id} "
                f"miniVinz_present={'miniVinz' in by_id} nb_ids_total={len(by_id)}"
            )
            return {"status": "failed", "reason": "Bouton 'Voir mes listes' introuvable"}

        logger.info(f"📋 [{acc['name']}] Clic sur 'Voir mes listes'...")
        await click_element(cdp, by_id["showListsButton1"]["node_id"])
        await asyncio.sleep(0.8)
        _pause("Clic sur 'Voir mes listes' effectué")

        reopened = await ensure_panel_open(cdp, page)
        _pause(f"ensure_panel_open() avant clic 'Republier' -> {reopened}")

        by_id = await snapshot_by_id(cdp)
        if "repost-shortcut" not in by_id:
            return {"status": "failed", "reason": "Bouton 'Republier' (repost-shortcut) introuvable dans la liste"}

        logger.info(f"♻️  [{acc['name']}] Clic sur 'Republier' (bascule vers l'onglet Republier)...")
        await click_element(cdp, by_id["repost-shortcut"]["node_id"])
        await asyncio.sleep(0.8)
        _pause("Clic sur 'Republier' effectué — vérifie qu'on est bien sur l'onglet Republier")

        # Vérification que le switch d'onglet a bien eu lieu
        by_id = await snapshot_by_id(cdp)
        if "repostButton" not in by_id:
            return {"status": "failed", "reason": "Onglet Republier non atteint (repostButton absent après navigation)"}

        return {"status": "success"}

    # new_str
    async def _poll_mass_action(self, page, cdp, acc, expected_count, action_label):
        """
        Poll générique de fin d'action de masse Clemz (republication, baisse de
        prix, sauvegarde) via #processStage + #progressContent, jusqu'au mot
        'termin' ET au compteur final 'X/X' (voir _trigger_repost pour le détail
        du garde-fou anti faux-positif). Factorisé car strictement identique entre
        les 3 actions -- seul le comportement APRÈS la fin diffère (message,
        étape suivante), géré par l'appelant.

        action_label : texte pour les logs (ex: "Republication", "Baisse de prix",
        "Sauvegarde").
        Retourne {"status": "success"} ou {"status": "failed", "reason": ...}.

        IMPORTANT (bug constaté le 17/08/2026) : expected_count est le nombre
        d'articles qu'on vient NOUS-MÊMES d'ajouter, pas forcément le total réel
        traité par Clemz -- si la liste "à traiter" contenait déjà des résidus
        d'un run précédent (interrompu, jamais vidé), Clemz traite PLUS
        d'articles que prévu. Dans ce cas, le texte de fin attendu ('N / N' avec
        notre N à nous) n'apparaît jamais, même si tout se termine avec succès
        côté Clemz -- d'où un timeout silencieux malgré une republication réelle.
        Le vrai total est donc lu dynamiquement depuis le premier texte de
        progression ('X / VRAI_TOTAL'), plutôt que supposé égal à expected_count.
        """
        MAX_WAIT = max(REPOST_TIMEOUT_MIN_SECONDS, expected_count * (REPOST_TIMEOUT_PER_ITEM_SECONDS + 60))
        POLL_INTERVAL = 3
        elapsed = 0
        last_stage = None
        last_progress = None
        real_total = expected_count  # ajusté dès qu'on lit le vrai total Clemz
        expected_progress_full = f"{expected_count} / {expected_count}"
        total_pattern = re.compile(r"\d+\s*/\s*(\d+)")

        logger.info(
            f"👀 [{acc['name']}] Début du poll {action_label} — "
            f"{expected_count} article(s) attendu(s), timeout à {MAX_WAIT}s."
        )

        erreurs_majeures = []
        alerts_dismissed = 0

        while elapsed < MAX_WAIT:
            captcha_reason = await _check_captcha_blocage(cdp)
            if captcha_reason:
                logger.error(f"🤖 [{acc['name']}] [{elapsed}s] {captcha_reason}")
                try:
                    from services.email_notifier import envoyer_notification_echec
                    envoyer_notification_echec(
                        f"CAPTCHA Vinted -- {action_label} en pause ({acc['name']})",
                        f"{captcha_reason}\n\nLa tâche {action_label} en cours sur {acc['name']} est en "
                        f"pause, en attente d'une résolution manuelle (jusqu'à "
                        f"{CAPTCHA_WAIT_TIMEOUT_SECONDS // 60} min). Connecte-toi à distance au PC pour "
                        f"glisser le slider dans le navigateur ouvert -- la tâche reprend automatiquement "
                        f"dès que c'est fait.",
                    )
                except Exception as e:
                    logger.warning(f"⚠️ Échec envoi alerte captcha par e-mail : {e}")

                # N'échoue PAS tout de suite ("rarement arrêtée", cf. échange du
                # 03/10/2026) -- attend une résolution manuelle avant d'abandonner.
                # Le temps passé ici n'entame PAS le budget MAX_WAIT de l'action de
                # masse elle-même (elapsed n'est incrémenté qu'en dessous) : une
                # pause captcha ne doit pas faire échouer le reste du lot par
                # timeout juste après avoir été résolue.
                resolu = await _attendre_resolution_captcha_manuelle(cdp, acc, captcha_reason, action_label)
                if resolu:
                    continue  # reprend le poll normal de l'action de masse
                return {"status": "failed", "reason": captcha_reason, "erreurs_majeures": erreurs_majeures}

            major_error = await _check_and_dismiss_major_error(cdp)
            if major_error:
                erreurs_majeures.append(major_error)
                continue  # revérifie immédiatement (une autre erreur peut être empilée)

            by_id_poll = await snapshot_by_id(cdp)

            # Modale "Confirmes-tu la republication de N article(s) ?" -- vérifiée en
            # continu ici plutôt que dans une fenêtre fixe avant le poll (bug constaté
            # le 17/08/2026 : cette modale peut n'apparaître qu'APRÈS la gestion d'une
            # erreur majeure, donc après la fin d'une fenêtre d'attente fixe -- laissant
            # la modale ouverte indéfiniment sans jamais être cliquée).
            if "continueAlertButton" in by_id_poll:
                alerts_dismissed += 1
                logger.info(f"✅ [{acc['name']}] Alerte Clemz #{alerts_dismissed} détectée pendant le poll — attente 10s avant clic sur 'Confirmer/Continuer'.")
                await asyncio.sleep(10)
                x, y = await get_center_coords(cdp, by_id_poll["continueAlertButton"]["node_id"])
                await real_mouse_click(page, x, y)
                await asyncio.sleep(1)
                continue

            stage_text = ""
            if "processStage" in by_id_poll:
                props = await get_live_props(cdp, by_id_poll["processStage"]["node_id"], props=("innerText",))
                stage_text = props.get("innerText", "")
                if stage_text != last_stage:
                    last_stage = stage_text

            progress_text = ""
            if "progressContent" in by_id_poll:
                props = await get_live_props(cdp, by_id_poll["progressContent"]["node_id"], props=("innerText",))
                progress_text = props.get("innerText", "")
                if progress_text != last_progress:
                    # Une seule ligne par changement de progression (au lieu de deux logs
                    # quasi-identiques État/Progression) -- le compteur "X / N" et l'article
                    # en cours sont dans progress_text, donc suffisant pour suivre le run.
                    resume = progress_text.replace("\n", " · ")
                    logger.info(f"⏳ [{acc['name']}] [{elapsed}s/{MAX_WAIT}s] {resume}")
                    last_progress = progress_text

                # Ajuste le total réel dès qu'on le voit divergent de notre estimation
                # (liste Clemz contenant plus d'articles que ceux qu'on vient d'ajouter).
                match = total_pattern.search(progress_text)
                if match:
                    detected_total = int(match.group(1))
                    if detected_total != real_total:
                        logger.warning(
                            f"⚠️ [{acc['name']}] Total réel Clemz ({detected_total}) différent du "
                            f"nombre attendu ({real_total}) — probable résidu d'un run précédent "
                            f"dans la liste. Ajustement du critère de fin et du timeout."
                        )
                        real_total = detected_total
                        expected_progress_full = f"{real_total} / {real_total}"
                        MAX_WAIT = max(MAX_WAIT, real_total * (REPOST_TIMEOUT_PER_ITEM_SECONDS + 60))

            combined = (stage_text + " " + progress_text).lower()
            if "termin" in combined and expected_progress_full in progress_text:
                # BUG CONSTATÉ LE 24/09/2026 : le compteur "N/N" atteint le total ET
                # le mot "terminé(e)" apparaît, mais Clemz peut AUSSI afficher dans ce
                # même message final "➡️ X articles non republiés" -- un échec PARTIEL
                # à l'intérieur d'un lot par ailleurs "terminé". Sans cette vérification,
                # tout le lot (y compris les X en échec) était marqué "success", et leur
                # date_republication mise à jour comme si de rien n'était -- 2 articles
                # sur 3 sont ainsi restés invisibles des alertes de republication
                # pendant que le 3e (le seul vraiment réussi) partageait le même statut.
                # Ce message générique ne dit PAS lesquels des N articles ont échoué --
                # impossible de ne rejeter que les bons : tout le lot est donc traité
                # comme non fiable plutôt que de risquer de garder un faux succès.
                non_republies = re.search(r"(\d+)\s+articles?\s+non\s+republi", combined)
                if non_republies and int(non_republies.group(1)) > 0:
                    reason = (
                        f"{action_label} annoncée terminée par Clemz ({expected_progress_full}), mais son "
                        f"propre message signale {non_republies.group(1)} article(s) non republié(s) -- "
                        f"lesquels précisément n'est pas identifiable depuis ce message générique, donc "
                        f"aucun article de ce lot n'est considéré comme fiable."
                    )
                    logger.error(f"❌ [{acc['name']}] {action_label} — {reason}")
                    return {"status": "failed", "reason": reason, "erreurs_majeures": erreurs_majeures}
                logger.info(f"🎉 [{acc['name']}] {action_label} terminée avec succès.")
                return {"status": "success", "erreurs_majeures": erreurs_majeures}

            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

        timeout_reason = f"Timeout ({MAX_WAIT}s) : message de fin non détecté."
        logger.error(
            f"❌ [{acc['name']}] {action_label} — {timeout_reason} "
            f"Dernier état : {last_stage!r} | Dernière progression : {last_progress!r} | "
            f"Attendu : {expected_progress_full!r}"
        )
        return {"status": "failed", "reason": timeout_reason, "erreurs_majeures": erreurs_majeures}

    async def _trigger_repost(self, page, cdp, acc, expected_count):
        """
        Déclenche la republication de la liste Clemz constituée à l'étape précédente.
        Séquence : Go Clemz -> modale de confirmation -> poll de progression -> retour au dressing.

        Retourne {"status": "success"/"failed", "reason": ...}
        """
        try:
            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() avant 'Go Clemz !' -> {reopened}")

            by_id = await snapshot_by_id(cdp)
            if "repostButton" not in by_id:
                return {"status": "failed", "reason": "Bouton 'Go Clemz !' introuvable — pas sur l'onglet Republier ?"}

            logger.info(f"♻️  [{acc['name']}] Clic sur 'Go Clemz !' — {expected_count} article(s) en cours.")
            await click_element(cdp, by_id["repostButton"]["node_id"])
            await asyncio.sleep(1.5)
            _pause("Clic sur 'Go Clemz !' effectué")

            # --- Modale(s) de confirmation générique Clemz (#alert-content / #continueAlertButton) ---
            # Détectée et fermée en continu DANS _poll_mass_action, pas ici via une fenêtre
            # fixe -- cette modale ("Confirmes-tu la republication ?" ou une annonce "info
            # Clemz") peut apparaître à tout moment du processus, y compris APRÈS la gestion
            # d'une erreur majeure (bug constaté le 17/08/2026 : une fenêtre fixe avant le
            # poll pouvait se terminer avant que la modale n'apparaisse, la laissant ouverte
            # indéfiniment sans jamais être cliquée).
            _pause("Le poll de progression va démarrer maintenant (gère aussi les modales de confirmation en continu)")

            poll_result = await self._poll_mass_action(page, cdp, acc, expected_count, action_label="Republication")
            if poll_result["status"] != "success":
                return poll_result

            erreurs_majeures = poll_result.get("erreurs_majeures", [])

            _pause("Message de fin détecté — vérifie la popup 'Republication terminée' à l'écran")

            # --- Retour au dressing via le bouton 'Mon dressing' de la popup finale ---
            by_id_final = await snapshot_by_id(cdp)
            if "progressDressingButton" in by_id_final:
                await click_element(cdp, by_id_final["progressDressingButton"]["node_id"])
                await asyncio.sleep(0.5)
                logger.info(f"👗 [{acc['name']}] Retour au dressing effectué.")
                _pause("Clic sur 'Mon dressing' effectué")
            else:
                logger.warning(f"⚠️ [{acc['name']}] #progressDressingButton introuvable — popup finale peut-être déjà fermée.")

            return {"status": "success", "erreurs_majeures": erreurs_majeures}

        except Exception as e:
            return {"status": "failed", "reason": f"Erreur inattendue : {str(e)[:120]}"}

    async def _navigate_to_modify_tab(self, cdp, page, acc):
        """
        Transition vers l'onglet 'Modifier en masse' (baisse de prix) : contrairement à
        Republier, cet onglet est directement accessible dans la barre d'icônes du haut
        (#modifyTabLink) — pas besoin de passer par 'Voir mes listes' / 'repost-shortcut'.
        """
        reopened = await ensure_panel_open(cdp, page)
        _pause(f"ensure_panel_open() en entrée de navigation Modifier -> {reopened}")

        by_id = await snapshot_by_id(cdp)
        if "modifyTabLink" not in by_id:
            return {"status": "failed", "reason": "Onglet 'Modifier en masse' (modifyTabLink) introuvable"}

        logger.info(f"✍️ [{acc['name']}] Clic sur l'onglet 'Modifier en masse'...")
        await click_element(cdp, by_id["modifyTabLink"]["node_id"])
        await asyncio.sleep(0.5)
        _pause("Clic sur l'onglet 'Modifier en masse' effectué")

        reopened = await ensure_panel_open(cdp, page)
        _pause(f"ensure_panel_open() après clic onglet Modifier -> {reopened}")

        by_id = await snapshot_by_id(cdp)
        if "modifyButton" not in by_id:
            return {"status": "failed", "reason": "Onglet Modifier non atteint (modifyButton absent après navigation)"}

        return {"status": "success"}

    async def _trigger_baisse_prix(self, page, cdp, acc, expected_count, pourcentage=20, prix_fixe=None):
        """
        Déclenche la baisse de prix en masse de la liste "à traiter" via le panneau
        'Modifier en masse' de Clemz.

        Deux modes, selon prix_fixe :
        - prix_fixe est None (par défaut) : mode pourcentage -- sens 'diminuer',
          saisit `pourcentage`, unité '%', arrondi 'sans arrondir'.
        - prix_fixe renseigné (liquidation, cf. échange du 03/10/2026) : mode
          natif Clemz "set" (#modifyPriceDirection="set") -- fixe TOUS les
          articles du lot à EXACTEMENT ce prix en euros, pas une baisse relative.
          Confirmé dans le code source de l'extension (actionInterface.html) :
          #modifyPriceDirection a bien une option "set", utilisable uniquement
          avec #modifyPriceType="€" (Clemz refuse "set" combiné à "%", cf. message
          d'erreur modifyArticles_modifyPricePercentageError de l'extension).

        Dans les deux cas : clic 'Modifier les annonce(s)' -> poll de progression
        (mêmes IDs génériques processStage/progressContent que la republication)
        -> retour dressing.

        Retourne {"status": "success"/"failed", "reason": ...}
        """
        try:
            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() avant réglages prix -> {reopened}")

            by_id = await snapshot_by_id(cdp)
            if "modifyPrice" not in by_id:
                return {"status": "failed", "reason": "Toggle 'Prix' (modifyPrice) introuvable"}

            price_state = await get_live_props(cdp, by_id["modifyPrice"]["node_id"], props=("checked",))
            if not price_state.get("checked"):
                await click_element(cdp, by_id["modifyPrice"]["node_id"])
                await asyncio.sleep(0.3)
            _pause(f"Toggle 'Prix' vérifié/coché (état avant clic : {price_state})")

            by_id = await snapshot_by_id(cdp)
            if "modifyPriceDirection" not in by_id:
                return {"status": "failed", "reason": "Sélecteur 'modifyPriceDirection' introuvable"}
            await set_select_value(cdp, by_id["modifyPriceDirection"]["node_id"], "set" if prix_fixe is not None else "decrease")

            if "modifyPriceValue" not in by_id:
                return {"status": "failed", "reason": "Champ 'modifyPriceValue' introuvable"}
            valeur = prix_fixe if prix_fixe is not None else pourcentage
            await set_input_value(cdp, by_id["modifyPriceValue"]["node_id"], str(valeur))
            await asyncio.sleep(0.2)

            if "modifyPriceType" not in by_id:
                return {"status": "failed", "reason": "Sélecteur 'modifyPriceType' introuvable"}
            await set_select_value(cdp, by_id["modifyPriceType"]["node_id"], "€" if prix_fixe is not None else "%")

            if "modifyPriceRound" not in by_id:
                return {"status": "failed", "reason": "Sélecteur 'modifyPriceRound' introuvable"}
            await set_select_value(cdp, by_id["modifyPriceRound"]["node_id"], "noRound")

            resume_reglage = f"prix fixé à {prix_fixe}€" if prix_fixe is not None else f"-{pourcentage}%, sans arrondir"
            _pause(f"Options de baisse de prix réglées : {resume_reglage} — vérifie visuellement")

            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() avant clic 'Modifier les annonce(s)' -> {reopened}")

            by_id = await snapshot_by_id(cdp)
            if "modifyButton" not in by_id:
                return {"status": "failed", "reason": "Bouton 'Modifier les annonce(s)' introuvable"}

            logger.info(f"✍️ [{acc['name']}] Clic sur 'Modifier les annonce(s)' — {expected_count} article(s) en cours.")
            await click_element(cdp, by_id["modifyButton"]["node_id"])
            await asyncio.sleep(1.5)
            _pause("Clic sur 'Modifier les annonce(s)' effectué")

            by_id = await snapshot_by_id(cdp)
            if "continueAlertButton" in by_id:
                logger.info(f"✅ [{acc['name']}] Modale de confirmation détectée, clic sur 'Confirmer'.")
                _pause("Modale de confirmation détectée — vérifie visuellement avant de confirmer")
                # new_str
                x, y = await get_center_coords(cdp, by_id["continueAlertButton"]["node_id"])
                await real_mouse_click(page, x, y)
                await asyncio.sleep(1)

            _pause("Le poll de progression va démarrer maintenant (aucune pause pendant le poll lui-même)")

            poll_result = await self._poll_mass_action(page, cdp, acc, expected_count, action_label="Baisse de prix")
            if poll_result["status"] != "success":
                return poll_result
            
            _pause("Message de fin détecté — vérifie la popup 'Modification terminée' à l'écran")

            by_id_final = await snapshot_by_id(cdp)
            if "progressDressingButton" in by_id_final:
                await click_element(cdp, by_id_final["progressDressingButton"]["node_id"])
                await asyncio.sleep(0.5)
                logger.info(f"👗 [{acc['name']}] Retour au dressing effectué.")
                _pause("Clic sur 'Mon dressing' effectué")
            else:
                logger.warning(f"⚠️ [{acc['name']}] #progressDressingButton introuvable — popup finale peut-être déjà fermée.")

            return {"status": "success"}

        except Exception as e:
            return {"status": "failed", "reason": f"Erreur inattendue : {str(e)[:120]}"}

    async def run_baisse_prix_test(self, playwright, acc, expected_count, pourcentage=20):
        """
        Point d'entrée UNITAIRE pour tester la baisse de prix, isolé de la constitution
        de liste : suppose que la liste "à traiter" a déjà été remplie MANUELLEMENT dans
        Clemz avant l'appel (n'exécute PAS _build_list, contrairement à _run_single_automation).
        """
        context = None
        try:
            account_key = acc["account_key"]
            session_ok = await ensure_session(account_key)
            if not session_ok:
                logger.error(f"❌ [{acc['name']}] Session expirée — test annulé.")
                return {"status": "failed", "reason": "Session Vinted expirée — reconnexion manuelle nécessaire."}

            logger.info(f"🚀 [{acc['name']}] Ouverture du navigateur (test baisse de prix)...")
            args, extra_kwargs = _args_et_kwargs_navigateur(acc, [
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,800",
                "--window-position=2000,2000",
            ])
            context = await playwright.chromium.launch_persistent_context(
                acc["session"],
                executable_path=acc["executable"],
                headless=False,
                args=args,
                **extra_kwargs,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await _positionner_hors_ecran(context, page)
            await _close_clemz_promo_tabs(context, page)

            await page.goto(acc["url"], wait_until="networkidle")
            _pause("Page Vinted chargée")

            cdp = await context.new_cdp_session(page)
            await cdp.send("DOM.enable")

            by_id = await snapshot_by_id(cdp)
            if "miniVinz" not in by_id:
                logger.error(f"❌ [{acc['name']}] #miniVinz introuvable — extension non injectée.")
                return {"status": "failed", "reason": "Bouton Clemz introuvable au chargement"}

            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() initial -> {reopened}")

            navigation_result = await self._navigate_to_modify_tab(cdp, page, acc)
            if navigation_result["status"] != "success":
                logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                return navigation_result

            return await self._trigger_baisse_prix(
                page, cdp, acc, expected_count=expected_count, pourcentage=pourcentage
            )

        except Exception as e:
            logger.error(f"❌ [{acc['name']}] Erreur fatale : {e}")
            return {"status": "failed", "reason": f"Erreur fatale lors de l'ouverture ou du pilotage : {str(e)[:150]}"}

        finally:
            if context:
                try:
                    await context.close()
                    logger.info(f"🔒 [{acc['name']}] Navigateur fermé proprement.")
                except Exception:
                    pass


    async def _navigate_to_my_dressing_tab(self, cdp, page, acc):
        """
        Transition vers l'onglet 'Mon dressing' (#myDressingTabLink, accessible
        directement dans la barre du haut), nécessaire pour l'action 'Sauver annonces'.
        """
        reopened = await ensure_panel_open(cdp, page)
        _pause(f"ensure_panel_open() en entrée de navigation Mon dressing -> {reopened}")

        by_id = await snapshot_by_id(cdp)
        if "myDressingTabLink" not in by_id:
            return {"status": "failed", "reason": "Onglet 'Mon dressing' (myDressingTabLink) introuvable"}

        logger.info(f"👗 [{acc['name']}] Clic sur l'onglet 'Mon dressing'...")
        await click_element(cdp, by_id["myDressingTabLink"]["node_id"])
        await asyncio.sleep(0.5)
        _pause("Clic sur l'onglet 'Mon dressing' effectué")

        reopened = await ensure_panel_open(cdp, page)
        _pause(f"ensure_panel_open() après clic onglet Mon dressing -> {reopened}")

        by_id = await snapshot_by_id(cdp)
        missing = [k for k in ("myDressingButton", "myDressingTargetList", "myDressingSelectAction") if k not in by_id]
        if missing:
            return {"status": "failed", "reason": f"Onglet Mon dressing non atteint (absents : {missing})"}

        return {"status": "success"}

    async def _trigger_save_backup(self, page, cdp, acc, expected_count):
        """
        Sauvegarde la liste "à traiter" dans la base Clemz (action 'Sauver annonces') --
        ÉTAPE OBLIGATOIRE après une baisse de prix destinée à être suivie d'une
        republication : sans cette sauvegarde, Clemz republierait avec l'ANCIEN
        prix sauvegardé, annulant l'effet de la baisse.
        Cible : radio 'Ma liste "à traiter"' + action 'Sauver annonces' (value='2') + 'Lancer !'.
        """
        try:
            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() avant sauvegarde -> {reopened}")

            by_id = await snapshot_by_id(cdp)
            if "myDressingTargetList" not in by_id:
                return {"status": "failed", "reason": "Radio 'Ma liste à traiter' (myDressingTargetList) introuvable"}
            await click_element(cdp, by_id["myDressingTargetList"]["node_id"])
            await asyncio.sleep(0.2)
            _pause("Radio 'Ma liste à traiter' sélectionnée — vérifie visuellement qu'elle est bien cochée")

            by_id = await snapshot_by_id(cdp)
            if "myDressingSelectAction" not in by_id:
                return {"status": "failed", "reason": "Sélecteur d'action (myDressingSelectAction) introuvable"}
            await set_select_value(cdp, by_id["myDressingSelectAction"]["node_id"], "2")  # "Sauver annonces"
            _pause("Action 'Sauver annonces' sélectionnée dans le menu déroulant")

            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() avant clic 'Lancer !' -> {reopened}")

            by_id = await snapshot_by_id(cdp)
            if "myDressingButton" not in by_id:
                return {"status": "failed", "reason": "Bouton 'Lancer !' (myDressingButton) introuvable"}

            logger.info(f"⏫ [{acc['name']}] Clic sur 'Lancer !' (Sauver annonces) — {expected_count} article(s).")
            await click_element(cdp, by_id["myDressingButton"]["node_id"])
            await asyncio.sleep(1)
            _pause("Clic sur 'Lancer !' effectué — la modale 'Sauver des annonces' devrait apparaître")

            # --- Modale intermédiaire 'Sauver des annonces' ---
            # Par défaut, Clemz coche 'Uniquement celles jamais sauvées' -> on force
            # 'Toutes celles ciblées', pour garantir que le NOUVEAU prix (post-baisse)
            # écrase bien une éventuelle sauvegarde antérieure de l'article.
            by_id = await snapshot_by_id(cdp)
            if "confirmButton" not in by_id:
                return {"status": "failed", "reason": "Modale 'Sauver des annonces' non détectée (confirmButton absent)"}

            if "all-items" not in by_id:
                return {"status": "failed", "reason": "Radio 'Toutes celles ciblées' (all-items) introuvable"}
            await click_element(cdp, by_id["all-items"]["node_id"])
            await asyncio.sleep(0.3)
            _pause("Radio 'Toutes celles ciblées' sélectionnée — le choix des photos doit maintenant apparaître")

            # Choix des photos : on force 'Vinted => Remplacer par photos dressing'
            # (plutôt que le défaut 'Clemz.app => Garder photos déjà sauvées'), pour que
            # la sauvegarde utilise les photos actuelles du dressing Vinted.
            by_id = await snapshot_by_id(cdp)
            if "upload-vinted-photos" not in by_id:
                return {"status": "failed", "reason": "Radio 'Vinted => Remplacer par photos dressing' introuvable"}
            await click_element(cdp, by_id["upload-vinted-photos"]["node_id"])
            await asyncio.sleep(0.3)
            _pause("Radio 'Vinted => Remplacer par photos dressing' sélectionnée — vérifie visuellement")

            by_id = await snapshot_by_id(cdp)
            if "confirmButton" not in by_id:
                return {"status": "failed", "reason": "Bouton 'OK - Sauver' (confirmButton) introuvable"}
            await click_element(cdp, by_id["confirmButton"]["node_id"])
            await asyncio.sleep(1)
            _pause("Clic sur 'OK - Sauver' effectué — la modale d'avertissement 'perte de photos' devrait apparaître")

            # --- Modale d'avertissement 'Risque de perte de photos' ---
            # Apparaît car on a choisi de remplacer les photos par celles de Vinted (option
            # 'upload-vinted-photos'), écrasant une éventuelle sauvegarde existante. On confirme.
            by_id = await snapshot_by_id(cdp)
            if "continueAlertButton" in by_id:
                logger.info(f"⚠️  [{acc['name']}] Modale 'Risque de perte de photos' détectée, clic sur 'Confirmer'.")
                _pause("Modale d'avertissement détectée — vérifie visuellement avant de confirmer")
                x, y = await get_center_coords(cdp, by_id["continueAlertButton"]["node_id"])
                await real_mouse_click(page, x, y)
                await asyncio.sleep(1)
                _pause("Clic sur 'Confirmer' effectué")

            poll_result = await self._poll_mass_action(page, cdp, acc, expected_count, action_label="Sauvegarde")
            if poll_result["status"] != "success":
                return poll_result
            _pause("Sauvegarde confirmée — vérifie visuellement le message de fin")

            by_id_final = await snapshot_by_id(cdp)
            if "progressDressingButton" in by_id_final:
                await click_element(cdp, by_id_final["progressDressingButton"]["node_id"])
                await asyncio.sleep(0.5)
            elif "progressCloseButton" in by_id_final:
                await click_element(cdp, by_id_final["progressCloseButton"]["node_id"])
                await asyncio.sleep(0.5)

            return {"status": "success"}

        except Exception as e:
            return {"status": "failed", "reason": f"Erreur inattendue (sauvegarde) : {str(e)[:120]}"}

    async def run_save_backup_test(self, playwright, acc, expected_count):
        """
        Point d'entrée UNITAIRE pour tester la sauvegarde Clemz ('Sauver annonces'),
        isolé de la baisse de prix et de la republication : suppose que la liste
        "à traiter" a déjà été remplie MANUELLEMENT (idéalement avec un prix déjà
        modifié à la main ou via un test baisse_prix précédent, pour vérifier que
        la sauvegarde capture bien le nouveau prix).
        """
        context = None
        try:
            account_key = acc["account_key"]
            session_ok = await ensure_session(account_key)
            if not session_ok:
                logger.error(f"❌ [{acc['name']}] Session expirée — test annulé.")
                return {"status": "failed", "reason": "Session Vinted expirée — reconnexion manuelle nécessaire."}

            logger.info(f"🚀 [{acc['name']}] Ouverture du navigateur (test sauvegarde Clemz)...")
            args, extra_kwargs = _args_et_kwargs_navigateur(acc, [
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,800",
                "--window-position=2000,2000",
            ])
            context = await playwright.chromium.launch_persistent_context(
                acc["session"],
                executable_path=acc["executable"],
                headless=False,
                args=args,
                **extra_kwargs,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await _close_clemz_promo_tabs(context, page)
            await page.goto(acc["url"], wait_until="networkidle")
            _pause("Page Vinted chargée")

            cdp = await context.new_cdp_session(page)
            await cdp.send("DOM.enable")

            by_id = await snapshot_by_id(cdp)
            if "miniVinz" not in by_id:
                logger.error(f"❌ [{acc['name']}] #miniVinz introuvable — extension non injectée.")
                return {"status": "failed", "reason": "Bouton Clemz introuvable au chargement"}

            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() initial -> {reopened}")

            navigation_result = await self._navigate_to_my_dressing_tab(cdp, page, acc)
            if navigation_result["status"] != "success":
                logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                return navigation_result

            return await self._trigger_save_backup(page, cdp, acc, expected_count=expected_count)

        except Exception as e:
            logger.error(f"❌ [{acc['name']}] Erreur fatale : {e}")
            return {"status": "failed", "reason": f"Erreur fatale lors de l'ouverture ou du pilotage : {str(e)[:150]}"}

        finally:
            if context:
                try:
                    await context.close()
                    logger.info(f"🔒 [{acc['name']}] Navigateur fermé proprement.")
                except Exception:
                    pass

    async def _run_single_automation(self, playwright, acc):
        if not acc["produits"]:
            logger.info(f"⏭️  [{acc['name']}] Aucun produit à traiter, on passe.")
            return {"account": acc["name"], "selection_results": [], "repost_result": None}

        context = None
        try:
            account_key = acc["account_key"]
            session_ok = await ensure_session(account_key)
            if not session_ok:
                logger.error(f"❌ [{acc['name']}] Session expirée — automatisation annulée.")
                return {
                    "account": acc["name"],
                    "selection_results": [],
                    "repost_result": {"status": "failed", "reason": "Session Vinted expirée — reconnexion manuelle nécessaire."},
                }

            # Décale légèrement le démarrage de chaque compte pour réduire la contention
            # au lancement quand les deux tournent en parallèle -- observé : le panneau
            # Smart Dressing de Clemz peut ne jamais atteindre l'état "prêt" dans les 120s
            # si les deux navigateurs s'initialisent exactement au même moment (CPU/réseau).
            await asyncio.sleep(random.uniform(0, 5))

            logger.info(f"🚀 [{acc['name']}] Ouverture du navigateur...")
            args, extra_kwargs = _args_et_kwargs_navigateur(acc, [
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,800",
                "--window-position=2000,2000",
            ])
            context = await playwright.chromium.launch_persistent_context(
                acc["session"],
                executable_path=acc["executable"],
                headless=False,
                args=args,
                **extra_kwargs,
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await _positionner_hors_ecran(context, page)
            await _close_clemz_promo_tabs(context, page)

            await page.goto(acc["url"], wait_until="networkidle")
            _pause("Page Vinted chargée")

            cdp = await context.new_cdp_session(page)
            await cdp.send("DOM.enable")

            # --- Ouverture automatique du panneau (idempotent : ne clique que si nécessaire) ---
            by_id = await snapshot_by_id(cdp)
            if "miniVinz" not in by_id:
                logger.error(f"❌ [{acc['name']}] #miniVinz introuvable — extension non injectée.")
                return {
                    "account": acc["name"],
                    "selection_results": [],
                    "repost_result": {"status": "failed", "reason": "Bouton Clemz introuvable au chargement"},
                }
            reopened = await ensure_panel_open(cdp, page)
            _pause(f"ensure_panel_open() initial -> {reopened}")

            # --- Étape A : Constitution de la liste ---
            selection_results = await self._build_list(page, cdp, acc)

            success_count = sum(1 for r in selection_results if r["status"] == "success")
            logger.info(
                f"📋 [{acc['name']}] Sélection terminée : "
                f"{success_count}/{len(acc['produits'])} produit(s) ajouté(s) avec succès."
            )
            _pause(f"Constitution de liste terminée : {success_count}/{len(acc['produits'])} succès")

            if success_count == 0:
                logger.warning(f"⚠️  [{acc['name']}] Aucun produit sélectionné — republication annulée.")
                return {
                    "account": acc["name"],
                    "selection_results": selection_results,
                    "repost_result": {"status": "failed", "reason": "Aucun produit n'a pu être ajouté à la liste Clemz."},
                }

            if self.task_type == "republication_baisse":
                # Chaîne en 3 étapes sur le MÊME compte : baisse de prix -> sauvegarde ->
                # republication. La liste "à traiter" étant vidée après CHAQUE action de
                # masse (confirmé en test), on reconstruit la liste avant chaque étape.

                # --- Étape 1/3 : baisse de prix -20% (utilise la liste déjà construite plus haut) ---
                navigation_result = await self._navigate_to_modify_tab(cdp, page, acc)
                if navigation_result["status"] != "success":
                    logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, navigation_result), "repost_result": navigation_result}

                baisse_result = await self._trigger_baisse_prix(
                    page, cdp, acc, expected_count=success_count, pourcentage=self.pourcentage_baisse_prix,
                    prix_fixe=self.prix_fixe,
                )
                if baisse_result.get("status") != "success":
                    logger.error(f"❌ [{acc['name']}] Baisse de prix échouée : {baisse_result.get('reason')}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, baisse_result), "repost_result": baisse_result}
                logger.info(f"✅ [{acc['name']}] Baisse de -20% appliquée. Reconstruction de la liste pour la sauvegarde...")

                # --- Reconstruction de liste avant l'étape 2 ---
                selection_results_2 = await self._build_list(page, cdp, acc)
                success_count_2 = sum(1 for r in selection_results_2 if r["status"] == "success")
                if success_count_2 == 0:
                    echec = {"status": "failed", "reason": "Liste reconstruite vide avant l'étape sauvegarde"}
                    return {
                        "account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, echec),
                        "repost_result": echec,
                    }

                # --- Étape 2/3 : sauvegarde Clemz (capture le nouveau prix) ---
                navigation_result = await self._navigate_to_my_dressing_tab(cdp, page, acc)
                if navigation_result["status"] != "success":
                    logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, navigation_result), "repost_result": navigation_result}

                save_result = await self._trigger_save_backup(page, cdp, acc, expected_count=success_count_2)
                if save_result.get("status") != "success":
                    logger.error(f"❌ [{acc['name']}] Sauvegarde échouée : {save_result.get('reason')}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, save_result), "repost_result": save_result}
                logger.info(f"✅ [{acc['name']}] Sauvegarde effectuée. Reconstruction de la liste pour la republication...")

                # --- Reconstruction de liste avant l'étape 3 ---
                selection_results_3 = await self._build_list(page, cdp, acc)
                success_count_3 = sum(1 for r in selection_results_3 if r["status"] == "success")
                if success_count_3 == 0:
                    echec = {"status": "failed", "reason": "Liste reconstruite vide avant l'étape republication"}
                    return {
                        "account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, echec),
                        "repost_result": echec,
                    }

                # --- Étape 3/3 : republication ---
                navigation_result = await self._navigate_to_repost_tab(cdp, page, acc)
                if navigation_result["status"] != "success":
                    logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, navigation_result), "repost_result": navigation_result}

                repost_result = await self._trigger_repost(page, cdp, acc, expected_count=success_count_3)

                # Le bloc de mise à jour Supabase ci-dessous doit refléter la DERNIÈRE
                # reconstruction de liste (celle utilisée pour la republication finale).
                selection_results = selection_results_3

            elif self.task_type == "baisse_prix":
                # Sauvegarde Clemz ('Sauver annonces') ENCHAÎNÉE juste après la baisse --
                # ÉTAPE OBLIGATOIRE (cf. _trigger_save_backup) : sans elle, une
                # republication ultérieure récupère l'ANCIEN prix encore sauvegardé côté
                # Clemz et annule l'effet de la baisse. Même séquence que les étapes 1+2
                # du chaînage "republication_baisse" ci-dessus, sans la republication
                # finale (cf. échange du 06/10/2026).
                navigation_result = await self._navigate_to_modify_tab(cdp, page, acc)
                if navigation_result["status"] != "success":
                    logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, navigation_result), "repost_result": navigation_result}

                baisse_result = await self._trigger_baisse_prix(
                    page, cdp, acc, expected_count=success_count, pourcentage=self.pourcentage_baisse_prix,
                    prix_fixe=self.prix_fixe,
                )
                if baisse_result.get("status") != "success":
                    logger.error(f"❌ [{acc['name']}] Baisse de prix échouée : {baisse_result.get('reason')}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, baisse_result), "repost_result": baisse_result}
                logger.info(f"✅ [{acc['name']}] Baisse de prix appliquée. Reconstruction de la liste pour la sauvegarde...")

                # --- Reconstruction de liste avant la sauvegarde (vidée après l'action de masse) ---
                selection_results_save = await self._build_list(page, cdp, acc)
                success_count_save = sum(1 for r in selection_results_save if r["status"] == "success")
                if success_count_save == 0:
                    echec = {"status": "failed", "reason": "Liste reconstruite vide avant l'étape sauvegarde"}
                    return {
                        "account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, echec),
                        "repost_result": echec,
                    }

                navigation_result = await self._navigate_to_my_dressing_tab(cdp, page, acc)
                if navigation_result["status"] != "success":
                    logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, navigation_result), "repost_result": navigation_result}

                repost_result = await self._trigger_save_backup(page, cdp, acc, expected_count=success_count_save)
                # La bookkeeping ci-dessous (price_drops, est_traite) doit refléter la
                # DERNIÈRE reconstruction de liste (celle utilisée pour la sauvegarde),
                # même raisonnement que pour "republication_baisse" plus haut.
                selection_results = selection_results_save

            else:  # "republication"
                navigation_result = await self._navigate_to_repost_tab(cdp, page, acc)
                if navigation_result["status"] != "success":
                    logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                    return {"account": acc["name"], "selection_results": self._selection_results_finaux(selection_results, navigation_result), "repost_result": navigation_result}
                repost_result = await self._trigger_repost(
                    page, cdp, acc, expected_count=success_count
                )

            erreurs_majeures = repost_result.get("erreurs_majeures", [])
            noms_erreurs_majeures = {erreur["nom"] for erreur in erreurs_majeures}
            if erreurs_majeures:
                from database import supabase as db_client
                from datetime import datetime as dt
                for erreur in erreurs_majeures:
                    try:
                        db_client.table("articles").update({
                            "erreur_clemz": True,
                            "erreur_clemz_reason": "Ancienne annonce supprimée, nouvelle non créée — intervention manuelle nécessaire.",
                            "erreur_clemz_date": dt.now().isoformat(),
                        }).ilike("nom", erreur["nom"]).execute()
                        logger.info(f"🛑 [{acc['name']}] '{erreur['nom']}' marqué en erreur Clemz en base.")
                    except Exception as e:
                        logger.error(f"❌ [{acc['name']}] Échec marquage erreur Clemz pour '{erreur['nom']}' : {e}")

            if repost_result.get("status") == "success" and self.task_type in ("republication", "republication_baisse"):
                from database import supabase as db_client
                from datetime import date

                # Un seul calcul de date : identique pour tous les produits de ce lot,
                # puisqu'ils partagent le même message de succès final de republication.
                today_str = date.today().isoformat()

                # EXCLUT les items en erreur_clemz (cf. ci-dessus) -- un repost_result
                # "success" au niveau du LOT n'empêche pas un échec INDIVIDUEL pour un
                # article précis (ancienne annonce supprimée, nouvelle jamais créée).
                # Sans cette exclusion, date_republication était quand même mis à jour
                # pour cet article -- il paraissait "frais" et disparaissait des alertes
                # de republication pendant des semaines, alors qu'il n'a AUCUNE annonce
                # active sur Vinted (cf. échange du 24/09/2026, Jean Diesel Fayza).
                produits_reussis = [
                    r["nom"] for r in selection_results
                    if r["status"] == "success" and r["nom"] not in noms_erreurs_majeures
                ]
                for product_name in produits_reussis:
                    try:
                        res = db_client.table("articles") \
                            .select("id, nb_republications_sans_vente") \
                            .ilike("nom", product_name) \
                            .execute()

                        matches = res.data or []

                        if len(matches) == 0:
                            logger.info(f"ℹ️  '{product_name}' republié sur Vinted, mais absent de Supabase "
                                        f"(pas suivi dans le catalogue — probablement non scrapé car masqué).")
                            continue

                        if len(matches) > 1:
                            logger.warning(f"⚠️  '{product_name}' correspond à {len(matches)} articles Supabase "
                                           f"(nom dupliqué) — mise à jour de nb_republications_sans_vente ignorée "
                                           f"pour éviter de cibler le mauvais article. À corriger manuellement si besoin.")
                            continue

                        article = matches[0]
                        new_count = (article.get("nb_republications_sans_vente") or 0) + 1
                        db_client.table("articles") \
                            .update({
                                "nb_republications_sans_vente": new_count,
                                "date_republication": today_str,
                                # Une republication rouvre le cycle : une nouvelle baisse
                                # redevient possible. Sans ça, est_traite (posé par la baisse
                                # de prix) restait à True indéfiniment après une republication
                                # Clemz -- seul republish_by_name_logic (bouton dashboard) le
                                # remettait à False, contrairement à ce que disait le commentaire
                                # de la branche baisse_prix plus bas (cf. échange du 20/09/2026).
                                "est_traite": False,
                            }) \
                            .eq("id", article["id"]) \
                            .execute()
                        logger.info(f"📊 {product_name} → {new_count} republication(s) sans vente, republié le {today_str}")
                    except Exception as e:
                        logger.warning(f"⚠️ Impossible de mettre à jour '{product_name}' : {e}")

            elif repost_result.get("status") == "success" and self.task_type in ("baisse_prix", "republication_baisse"):
                from database import supabase as db_client

                # Marque les produits comme traités pour qu'ils disparaissent immédiatement
                # de "Mauvaise Performance" côté dashboard (même convention que le bouton
                # manuel "Marquer comme traité" -> est_traite=True). Le scraper ne remet
                # jamais ce champ à False sur un article déjà existant, donc ça persiste
                # jusqu'à une éventuelle republication future (qui remet est_traite=False).
                # Même exclusion que la branche republication ci-dessus -- pas la peine de
                # marquer "traité" un article dont l'annonce vient d'être perdue.
                produits_reussis = [
                    r["nom"] for r in selection_results
                    if r["status"] == "success" and r["nom"] not in noms_erreurs_majeures
                ]
                for product_name in produits_reussis:
                    try:
                        check = db_client.table("articles") \
                            .select("id, prix_vente") \
                            .ilike("nom", product_name) \
                            .execute()
                        matches = check.data or []

                        if len(matches) > 1:
                            logger.warning(f"⚠️  '{product_name}' correspond à {len(matches)} articles Supabase "
                                           f"(nom dupliqué) — marquage est_traite ignoré pour éviter de traiter "
                                           f"le mauvais article. À corriger manuellement si besoin.")
                            continue

                        article = matches[0] if matches else None

                        db_client.table("articles") \
                            .update({"est_traite": True}) \
                            .ilike("nom", product_name) \
                            .execute()
                        logger.info(f"📉 {product_name} → baisse de prix appliquée, marqué comme traité (est_traite=True)")

                        # Capture pour analyse ultérieure (chantier "mesure d'impact des
                        # baisses de prix par tranche de valeur"), même si l'article n'est
                        # pas suivi dans Supabase (article_id reste None dans ce cas, mais
                        # on garde quand même la trace via le nom).
                        # Mode prix fixe (self.prix_fixe) : le prix après coup est CONNU
                        # exactement (pas besoin de l'estimer), et le pourcentage réellement
                        # appliqué varie par article (même prix fixe, prix de départ
                        # différents) -- calculé ici à partir du vrai prix_avant, au lieu de
                        # réutiliser self.pourcentage_baisse_prix qui n'a pas de sens dans ce mode.
                        if article:
                            prix_avant = float(article.get("prix_vente") or 0)
                            if self.prix_fixe is not None:
                                prix_apres_estime = self.prix_fixe
                                pourcentage_applique = (
                                    round((1 - self.prix_fixe / prix_avant) * 100, 1) if prix_avant > 0 else None
                                )
                            else:
                                prix_apres_estime = round(prix_avant * (1 - self.pourcentage_baisse_prix / 100), 2)
                                pourcentage_applique = self.pourcentage_baisse_prix
                        else:
                            prix_avant = None
                            prix_apres_estime = self.prix_fixe  # connu même sans l'article Supabase
                            pourcentage_applique = None if self.prix_fixe is not None else self.pourcentage_baisse_prix

                        try:
                            db_client.table("price_drops").insert({
                                "article_id": article["id"] if article else None,
                                "nom": product_name,
                                "dressing": acc["name"],
                                "prix_avant": prix_avant,
                                "pourcentage_applique": pourcentage_applique,
                                "prix_apres_estime": prix_apres_estime,
                            }).execute()
                        except Exception as e:
                            logger.warning(f"⚠️ Impossible d'enregistrer price_drops pour '{product_name}' : {e}")

                    except Exception as e:
                        logger.warning(f"⚠️ Impossible de marquer '{product_name}' comme traité : {e}")

            return {
                "account": acc["name"],
                # Filet de sécurité : couvre aussi le cas où repost_result
                # (branches "baisse_prix"/"republication" simples) échoue au
                # niveau du DÉCLENCHEMENT lui-même (ex: timeout du poll), pas
                # seulement de la navigation qui le précède -- ce chemin ne
                # passe par aucun des early-return déjà corrigés ci-dessus.
                "selection_results": self._selection_results_finaux(selection_results, repost_result),
                "repost_result": repost_result,
            }

        except Exception as e:
            logger.error(f"❌ [{acc['name']}] Erreur fatale : {e}")
            return {
                "account": acc["name"],
                "selection_results": [],
                "repost_result": {"status": "failed", "reason": f"Erreur fatale lors de l'ouverture ou du pilotage : {str(e)[:150]}"},
            }

        finally:
            if context:
                try:
                    await context.close()
                    logger.info(f"🔒 [{acc['name']}] Navigateur fermé proprement.")
                except Exception:
                    pass

    async def run(self):
        from services.clemz_auto_message import request_preemption, release_preemption, get_profile_lock

        # Préemption CIBLÉE : on ne coupe le watchdog QUE sur le(s) compte(s) qui ont
        # réellement des produits à traiter. Si seul le Dressing 1 (Chrome) est concerné,
        # le watchdog Edge continue de tourner tranquillement sur le Dressing 2.
        active_account_keys = sorted(set(
            acc["account_key"]
            for acc in self.accounts
            if acc["produits"]
        ))
        # Tri systématique : garantit un ordre d'acquisition identique pour toute
        # automatisation concurrente touchant plusieurs comptes -- nécessaire pour
        # exclure tout deadlock si deux automatisations se chevauchent sur des
        # sous-ensembles de comptes différents (voir clemz_partage.py, qui utilise
        # le même ordre fixe issu de ACCOUNTS).
        profile_locks = [get_profile_lock(key) for key in active_account_keys]

        if active_account_keys:
            await asyncio.gather(*[request_preemption(key) for key in active_account_keys])

        # Verrou de profil : protège CETTE automatisation contre une AUTRE
        # automatisation planifiée (pas le watchdog, déjà couvert par la
        # préemption ci-dessus) qui viserait le(s) même(s) compte(s) en parallèle.
        for lock in profile_locks:
            await lock.acquire()
        try:
            async with async_playwright() as p:
                # Exécution décalée (pas de gather simultané) : deux comptes qui
                # démarrent une republication à la même seconde depuis la même IP
                # est un signal de coordination détectable. Un délai aléatoire de
                # quelques minutes entre les deux casse cette corrélation.
                #
                # Le délai ne se justifie qu'ENTRE deux comptes qui vont RÉELLEMENT
                # agir -- avant, il se basait sur l'index (i > 0), donc se déclenchait
                # même quand le compte précédent (ou le suivant) n'avait aucun produit
                # à traiter (retour immédiat dans _run_single_automation), ajoutant une
                # attente de 2-5 min pour rien (ex: cron "midi"/"soir", qui ne concerne
                # qu'un seul dressing à la fois par construction).
                results = []
                compte_reel_deja_traite = False
                for acc in self.accounts:
                    a_du_travail = bool(acc["produits"])
                    if a_du_travail and compte_reel_deja_traite:
                        delay = random.uniform(120, 300)
                        await asyncio.sleep(delay)
                    try:
                        result = await self._run_single_automation(p, acc)
                    except Exception as e:
                        result = e
                    results.append(result)
                    if a_du_travail:
                        compte_reel_deja_traite = True
        finally:
            for lock in profile_locks:
                lock.release()
            if active_account_keys:
                await asyncio.gather(*[release_preemption(key) for key in active_account_keys])

        normalized = []
        for acc, result in zip(self.accounts, results):
            if isinstance(result, Exception):
                normalized.append({
                    "account": acc["name"],
                    "selection_results": [],
                    "repost_result": {"status": "failed", "reason": f"Exception non interceptée : {str(result)[:150]}"},
                })
            else:
                normalized.append(result)

        return normalized


if __name__ == "__main__":
    async def _test():
        automation = ClemzAutomation(
            produits_d1=[
                "Jean Blanc Morgan - Taille 38 (M) - Détails Chaînes Argentées - Chic",
            ],
            produits_d2=[],
        )
        async with async_playwright() as p:
            result = await automation._run_single_automation(p, automation.accounts[0])
            print(result)

    asyncio.run(_test())