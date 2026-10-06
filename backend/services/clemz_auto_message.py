"""
Watchdog "Messages automatiques aux favoris" (Clemz).

Contrairement aux autres automatisations (republication, baisse de prix, partage
vues/favoris) qui sont des tâches PONCTUELLES déclenchées par le scheduler, ce
processus doit tourner EN CONTINU : Clemz vérifie les nouveaux favoris toutes les
2 min (non modifiable) et envoie un message avec l'offre configurée (-12%), mais
UNIQUEMENT si son onglet reste actif dans un navigateur ouvert (veille = arrêt).

Conflit structurel avec les autres automatisations : toutes utilisent les mêmes
dossiers de session (clemz_session_chrome_d1_reel pour D1 depuis le 08/09/2026,
clemz_session_chrome_reel pour D2 depuis le 05/09/2026) -> deux processus
Playwright ne peuvent jamais ouvrir le même profil en même temps. La solution
retenue est la PRÉEMPTION : avant de lancer une automatisation planifiée sur un
compte donné, on active un flag pour ce compte ; le watchdog le voit, ferme
proprement son navigateur, et attend que le flag retombe pour reprendre.

Séquence de démarrage/récupération (validée via diagnostic réel) :
  - Écran initial (#autoMessageStartButton visible) : cocher #autoMessageFavEnabled
    si besoin, cliquer sur le bouton, gérer la modale récap (#memoryKeep gardé,
    #continueAlertButton), puis poll jusqu'à #autoMessageStatus == "en cours".
  - Écran "Erreur" (#autoMessageStatus en text-danger) : cliquer sur
    #autoMessageStopButton pour revenir au panneau normal, puis rejouer toute la
    séquence de démarrage ci-dessus.
  - Écran "en cours" : rien à faire, on lit juste le timestamp du prochain cycle
    (data-auto-message-next-cycle-date sur #progressUiContainer, en millisecondes
    Unix) pour caler le prochain réveil du watchdog.
"""

import os
import asyncio
import logging
from datetime import datetime
from playwright.async_api import async_playwright

try:
    from clemz_cdp import snapshot_by_id, get_live_props, click_element, ensure_panel_open
    from session_manager import ensure_session
    from clemz_automation import _close_clemz_promo_tabs, _args_et_kwargs_navigateur
except ImportError:
    from services.clemz_cdp import snapshot_by_id, get_live_props, click_element, ensure_panel_open
    from services.session_manager import ensure_session
    from services.clemz_automation import _close_clemz_promo_tabs, _args_et_kwargs_navigateur

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

ACCOUNTS = [
    {
        # MIGRÉ (08/09/2026) : vrai Chrome + Clemz préchargée manuellement dans le
        # profil, même traitement que Dressing 2 -- coïncide avec le changement de
        # compte Vinted de Dressing 1.
        "name": "Chrome - Dressing 1",
        "executable": None,
        "extension": None,
        "session": os.path.join(_BASE, "clemz_session_chrome_d1_reel"),
        "url": "https://www.vinted.fr/member/287248160",
        "account_key": "chrome_clemz",
        "channel": "chrome",
    },
    {
        # MIGRÉ (05/09/2026) : vrai Chrome + Clemz préchargée manuellement dans
        # le profil -- voir session_manager.ACCOUNTS["edge_clemz"] pour le détail.
        "name": "Chrome - Dressing 2",
        "executable": None,
        "extension": None,
        "session": os.path.join(_BASE, "clemz_session_chrome_reel"),
        "url": "https://www.vinted.fr/member/3136979514",
        "account_key": "edge_clemz",
        "channel": "chrome",
    },
]

# --------------------------------------------------------------------------- #
# Coordination inter-automatisations (préemption)
# --------------------------------------------------------------------------- #
# État partagé en mémoire (même process FastAPI/APScheduler) -- pas besoin d'un
# vrai verrou système, juste une convention respectée par tous les modules qui
# utilisent les mêmes sessions Chrome/Edge.

_preemption_flags = {acc["account_key"]: False for acc in ACCOUNTS}
_preemption_lock = asyncio.Lock()

# Signale la fermeture RÉELLE du navigateur watchdog pour un compte donné.
# Initialisé "set" car au démarrage du serveur, aucun navigateur watchdog n'est
# encore ouvert.
_browser_closed_events = {acc["account_key"]: asyncio.Event() for acc in ACCOUNTS}
for _evt in _browser_closed_events.values():
    _evt.set()

PREEMPTION_CHECK_INTERVAL_SECONDS = 15  # granularité de la surveillance du flag
NEXT_CYCLE_MARGIN_SECONDS = 15          # marge après le timestamp annoncé par Clemz
ERROR_RETRY_INTERVAL_SECONDS = 30       # nouvel essai après un échec de démarrage/lecture
PREEMPTION_WAIT_TIMEOUT_SECONDS = 90    # garde-fou anti-blocage infini d'une automatisation

# --------------------------------------------------------------------------- #
# Verrou de profil (mutuelle exclusion ENTRE AUTOMATISATIONS PLANIFIÉES)
# --------------------------------------------------------------------------- #
# La préemption ci-dessus ne protège que contre le WATCHDOG : elle garantit qu'un
# navigateur watchdog est bien fermé avant qu'une automatisation planifiée n'ouvre
# le sien. Elle ne protège PAS deux automatisations planifiées entre elles (ex.
# deux déclenchements manuels rapprochés, ou un cron qui chevauche un test manuel
# sur le même compte) -- rien n'empêche deux launch_persistent_context() sur le
# même profil dans ce cas. Ce verrou comble ce trou, en complément de la
# préemption (les deux mécanismes sont nécessaires, aucun ne remplace l'autre).
_profile_locks = {acc["account_key"]: asyncio.Lock() for acc in ACCOUNTS}


def get_profile_lock(account_key):
    return _profile_locks[account_key]


async def request_preemption(account_key):
    """
    À appeler par toute automatisation planifiée (repost, baisse de prix, partage
    vues/favoris) AVANT d'ouvrir un navigateur sur ce compte. Bloque jusqu'à
    confirmation RÉELLE de fermeture du navigateur watchdog (pas juste la pose du
    flag), pour éviter que deux launch_persistent_context() visent le même profil
    en même temps.
    """
    async with _preemption_lock:
        _preemption_flags[account_key] = True
    logger.info(f"⏸️  [WATCHDOG {account_key}] Préemption demandée — attente de fermeture réelle du navigateur...")

    try:
        await asyncio.wait_for(
            _browser_closed_events[account_key].wait(),
            timeout=PREEMPTION_WAIT_TIMEOUT_SECONDS,
        )
        logger.info(f"✅ [WATCHDOG {account_key}] Navigateur watchdog confirmé fermé.")
    except asyncio.TimeoutError:
        # Sécurité : on ne bloque jamais indéfiniment une automatisation planifiée à cause
        # du watchdog. On log fort pour investigation, mais on laisse l'appelant continuer
        # (au pire il retombera sur un conflit de profil visible dans les logs Playwright).
        logger.error(
            f"❌ [WATCHDOG {account_key}] Timeout ({PREEMPTION_WAIT_TIMEOUT_SECONDS}s) en attente "
            f"de fermeture du navigateur watchdog après préemption."
        )


async def release_preemption(account_key):
    """À appeler dans le bloc finally de l'automatisation planifiée, une fois terminée."""
    async with _preemption_lock:
        _preemption_flags[account_key] = False
    logger.info(f"▶️  [WATCHDOG {account_key}] Préemption levée — le watchdog peut reprendre.")


async def request_preemption_all():
    """Préempte les DEUX comptes d'un coup (utilisé dès l'entrée de run(), avant tout navigateur)."""
    await asyncio.gather(*[request_preemption(acc["account_key"]) for acc in ACCOUNTS])


async def release_preemption_all():
    await asyncio.gather(*[release_preemption(acc["account_key"]) for acc in ACCOUNTS])


def is_preempted(account_key):
    return _preemption_flags.get(account_key, False)


# --------------------------------------------------------------------------- #
# Séquence Clemz (démarrage / récupération / lecture du prochain cycle)
# --------------------------------------------------------------------------- #

async def _navigate_to_auto_message_tab(cdp, page):
    await ensure_panel_open(cdp, page)
    by_id = await snapshot_by_id(cdp)
    if "autoMessageTabLink" not in by_id:
        return {"status": "failed", "reason": "Onglet 'Messages autos' (autoMessageTabLink) introuvable"}
    await click_element(cdp, by_id["autoMessageTabLink"]["node_id"])
    await asyncio.sleep(0.5)
    await ensure_panel_open(cdp, page)
    return {"status": "success"}


async def _ensure_fav_enabled_checked(cdp):
    by_id = await snapshot_by_id(cdp)
    if "autoMessageFavEnabled" not in by_id:
        return {"status": "failed", "reason": "Toggle autoMessageFavEnabled introuvable"}

    props = await get_live_props(cdp, by_id["autoMessageFavEnabled"]["node_id"], props=("checked",))
    if not props.get("checked", False):
        await click_element(cdp, by_id["autoMessageFavEnabled"]["node_id"])
        await asyncio.sleep(0.3)
    return {"status": "success"}


async def _click_start_auto_message(cdp, page):
    await ensure_panel_open(cdp, page)
    by_id = await snapshot_by_id(cdp)
    if "autoMessageStartButton" not in by_id:
        return {"status": "failed", "reason": "Bouton autoMessageStartButton introuvable"}
    await click_element(cdp, by_id["autoMessageStartButton"]["node_id"])
    await asyncio.sleep(1.0)
    return {"status": "success"}


async def _handle_recap_modal_if_present(cdp):
    by_id = await snapshot_by_id(cdp)
    if "continueAlertButton" not in by_id:
        return {"status": "skipped"}
    await click_element(cdp, by_id["continueAlertButton"]["node_id"])
    await asyncio.sleep(1.0)
    return {"status": "success"}


async def _poll_until_running(cdp, max_wait_seconds=30, poll_interval=2):
    elapsed = 0
    while elapsed < max_wait_seconds:
        by_id = await snapshot_by_id(cdp)
        if "autoMessageStatus" in by_id:
            props = await get_live_props(cdp, by_id["autoMessageStatus"]["node_id"], props=("innerText",))
            if "en cours" in props.get("innerText", "").lower():
                return {"status": "success", "elapsed": elapsed}
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return {"status": "failed", "reason": f"Timeout ({max_wait_seconds}s) : jamais passé à 'en cours'"}


async def _start_sequence(cdp, page):
    """Séquence complète de démarrage depuis l'écran initial (#autoMessageStartButton visible)."""
    check_result = await _ensure_fav_enabled_checked(cdp)
    if check_result["status"] != "success":
        return check_result

    start_result = await _click_start_auto_message(cdp, page)
    if start_result["status"] != "success":
        return start_result

    await _handle_recap_modal_if_present(cdp)
    return await _poll_until_running(cdp)


async def _recover_from_error_and_restart(cdp, page):
    """Cas 'Erreur' : clic ARRÊT pour revenir au panneau normal, puis relance complète."""
    by_id = await snapshot_by_id(cdp)
    if "autoMessageStopButton" not in by_id:
        return {"status": "failed", "reason": "autoMessageStopButton introuvable en état Erreur"}

    logger.info("🛑 Sortie de l'état 'Erreur' (clic ARRÊT)...")
    await click_element(cdp, by_id["autoMessageStopButton"]["node_id"])
    await asyncio.sleep(1.0)
    await ensure_panel_open(cdp, page)

    nav_result = await _navigate_to_auto_message_tab(cdp, page)
    if nav_result["status"] != "success":
        return nav_result

    logger.info("🔁 Relance de la séquence de démarrage après erreur...")
    return await _start_sequence(cdp, page)


async def read_next_cycle_timestamp(cdp):
    """
    Retourne {"status": "success", "next_cycle_ts_seconds": float} si en cours,
    ou {"status": "not_running"/"failed", "reason": ...} sinon. Le timestamp est
    en SECONDES Unix (converti depuis les millisecondes de l'attribut HTML).
    """
    by_id = await snapshot_by_id(cdp)

    if "autoMessageStatus" not in by_id:
        return {"status": "not_running", "reason": "autoMessageStatus absent (écran initial)"}

    props = await get_live_props(cdp, by_id["autoMessageStatus"]["node_id"], props=("innerText",))
    status_text = props.get("innerText", "")
    if "en cours" not in status_text.lower():
        return {"status": "not_running", "reason": f"autoMessageStatus = {status_text!r}"}

    if "progressUiContainer" not in by_id:
        return {"status": "failed", "reason": "progressUiContainer introuvable alors que 'en cours'"}

    raw_ts = by_id["progressUiContainer"]["attrs"].get("data-auto-message-next-cycle-date")
    if not raw_ts:
        return {"status": "failed", "reason": "data-auto-message-next-cycle-date absent"}

    try:
        ts_seconds = int(raw_ts) / 1000
    except ValueError:
        return {"status": "failed", "reason": f"Format de timestamp inattendu : {raw_ts!r}"}

    return {"status": "success", "next_cycle_ts_seconds": ts_seconds}


async def ensure_auto_message_running(cdp, page):
    """
    Point d'entrée unique : quel que soit l'état actuel (en cours / erreur / écran
    initial jamais lancé), s'assure que le processus tourne à la sortie. Retourne
    le résultat de read_next_cycle_timestamp() une fois confirmé "en cours".
    """
    status = await read_next_cycle_timestamp(cdp)
    if status["status"] == "success":
        return status

    if status["status"] == "not_running" and "erreur" in status["reason"].lower():
        recovery = await _recover_from_error_and_restart(cdp, page)
    else:
        # Écran initial jamais lancé -> on y navigue si nécessaire puis on démarre.
        by_id = await snapshot_by_id(cdp)
        if "autoMessageStartButton" not in by_id:
            nav_result = await _navigate_to_auto_message_tab(cdp, page)
            if nav_result["status"] != "success":
                return nav_result
        recovery = await _start_sequence(cdp, page)

    if recovery["status"] != "success":
        return recovery

    return await read_next_cycle_timestamp(cdp)


# --------------------------------------------------------------------------- #
# Boucle watchdog par compte
# --------------------------------------------------------------------------- #

class ClemzAutoMessageWatchdog:
    def __init__(self):
        self.accounts = ACCOUNTS

    async def _run_account_loop(self, acc):
        """
        Boucle infinie pour UN compte : tant que non préempté, garde un navigateur
        ouvert et actif, s'assure que le processus tourne, et se réveille au bon
        moment (aligné sur le timestamp du prochain cycle) pour revérifier.
        """
        account_key = acc["account_key"]

        while True:
            # Vérification ET clear() de l'event faits SOUS LE MÊME VERROU que
            # request_preemption() (qui pose le flag) : sans cela, une préemption
            # qui arrive juste entre "is_preempted() -> False" et le clear()
            # ci-dessous verrait l'event encore "set" et laisserait
            # l'automatisation appelante croire le profil libre, alors
            # qu'ensure_session() est sur le point de l'ouvrir -> conflit de
            # profil malgré la préemption.
            async with _preemption_lock:
                if _preemption_flags[account_key]:
                    should_wait = True
                else:
                    # IMPORTANT : ensure_session() ouvre LUI AUSSI un navigateur sur ce
                    # même profil (voir session_manager.py) -- il faut donc verrouiller
                    # l'event DÈS CET APPEL, pas seulement autour du navigateur "longue
                    # durée" plus bas.
                    _browser_closed_events[account_key].clear()
                    should_wait = False

            if should_wait:
                await asyncio.sleep(PREEMPTION_CHECK_INTERVAL_SECONDS)
                continue

            context = None
            try:
                try:
                    session_ok = await ensure_session(account_key)
                except Exception as e:
                    # Sécurité : ensure_session() n'était protégé par aucun try/except ici.
                    # Une exception non catchée à ce niveau tuerait TOUTE la task watchdog
                    # (Chrome + Edge d'un coup, via asyncio.gather dans run_forever) sans
                    # que personne ne soit prévenu. On log et on retente, comme les autres
                    # erreurs de cette boucle.
                    logger.error(f"❌ [WATCHDOG {acc['name']}] Erreur inattendue dans ensure_session : {e}")
                    await asyncio.sleep(ERROR_RETRY_INTERVAL_SECONDS)
                    continue

                if not session_ok:
                    logger.error(f"❌ [WATCHDOG {acc['name']}] Session Vinted expirée — nouvel essai dans {ERROR_RETRY_INTERVAL_SECONDS}s.")
                    await asyncio.sleep(ERROR_RETRY_INTERVAL_SECONDS)
                    continue

                async with async_playwright() as p:
                    logger.info(f"🚀 [WATCHDOG {acc['name']}] Ouverture du navigateur...")

                    # Mode visible piloté exclusivement par le toggle Maintenance
                    # ("Navigateur visible (mode test)"), au lieu d'un headless=False
                    # codé en dur -- même pattern que vinted_scraper.py.
                    from services.maintenance_service import maintenance_service
                    mode_visible = maintenance_service.get_clemz_visible_mode().get("visible", False)

                    args, extra_kwargs = _args_et_kwargs_navigateur(acc, [
                        "--disable-blink-features=AutomationControlled",
                        "--window-size=1280,800",
                        "--window-position=2000,2000",
                    ])
                    context = await p.chromium.launch_persistent_context(
                        acc["session"],
                        executable_path=acc["executable"],
                        headless=not mode_visible,
                        args=args,
                        **extra_kwargs,
                    )
                    # Le profil est maintenant VERROUILLÉ par ce navigateur -- on le
                    # signale immédiatement (avant même la suite des étapes), pour
                    # qu'un request_preemption() concurrent attende bien la vraie fermeture.
                    page = context.pages[0] if context.pages else await context.new_page()
                    await _close_clemz_promo_tabs(context, page)
                    await page.goto(acc["url"], wait_until="networkidle")

                    cdp = await context.new_cdp_session(page)
                    await cdp.send("DOM.enable")

                    by_id = await snapshot_by_id(cdp)
                    if "miniVinz" not in by_id:
                        logger.error(f"❌ [WATCHDOG {acc['name']}] #miniVinz introuvable — extension non injectée.")
                        await context.close()
                        context = None
                        await asyncio.sleep(ERROR_RETRY_INTERVAL_SECONDS)
                        continue

                    await ensure_panel_open(cdp, page)

                    # Boucle interne : tant que ce navigateur reste pertinent
                    # (pas préempté), on garde CE MÊME contexte ouvert -- Clemz a
                    # besoin du navigateur actif en continu, pas d'ouvertures/
                    # fermetures répétées.
                    while not is_preempted(account_key):
                        result = await ensure_auto_message_running(cdp, page)

                        if result["status"] != "success":
                            logger.warning(f"⚠️ [WATCHDOG {acc['name']}] {result.get('reason')} — nouvel essai dans {ERROR_RETRY_INTERVAL_SECONDS}s.")
                            await asyncio.sleep(ERROR_RETRY_INTERVAL_SECONDS)
                            continue

                        next_cycle_ts = result["next_cycle_ts_seconds"]
                        sleep_seconds = max(5, next_cycle_ts - datetime.now().timestamp() + NEXT_CYCLE_MARGIN_SECONDS)
                        logger.info(f"😴 [WATCHDOG {acc['name']}] En cours — prochain contrôle dans {sleep_seconds:.0f}s.")

                        # On découpe l'attente pour rester réactif à une préemption
                        # qui surviendrait pendant le sommeil.
                        slept = 0
                        while slept < sleep_seconds:
                            if is_preempted(account_key):
                                break
                            chunk = min(PREEMPTION_CHECK_INTERVAL_SECONDS, sleep_seconds - slept)
                            await asyncio.sleep(chunk)
                            slept += chunk

                    logger.info(f"⏸️  [WATCHDOG {acc['name']}] Préemption détectée — fermeture du navigateur.")

            except Exception as e:
                logger.error(f"❌ [WATCHDOG {acc['name']}] Erreur inattendue : {e}")

            finally:
                if context:
                    try:
                        await context.close()
                        logger.info(f"🔒 [WATCHDOG {acc['name']}] Navigateur fermé proprement.")
                    except Exception:
                        pass
                # INCONDITIONNEL : que le contexte ait été fermé ici, fermé plus tôt
                # manuellement (cas 'miniVinz introuvable'), ou jamais ouvert (échec de
                # launch_persistent_context), on signale toujours "profil libre" --
                # sinon un seul passage par un chemin d'erreur bloque l'event pour
                # toujours, et tout request_preemption() futur timeout inutilement.
                _browser_closed_events[account_key].set()

            # Petite pause avant de rouvrir (évite une boucle trop agressive en
            # cas d'erreurs répétées ou de préemption qui vient de se lever).
            await asyncio.sleep(2)

    async def run_forever(self):
        """Lance les deux comptes en parallèle, chacun dans sa propre boucle infinie."""
        tasks = [self._run_account_loop(acc) for acc in self.accounts]
        await asyncio.gather(*tasks)


def start_watchdog_background_task():
    """
    À appeler UNE FOIS au démarrage du serveur (ex. dans main.py, événement
    startup FastAPI) : lance le watchdog en tâche de fond, sans bloquer le reste
    de l'application.
    """
    watchdog = ClemzAutoMessageWatchdog()
    return asyncio.create_task(watchdog.run_forever())