"""
Automatisation "Partage des vues et des favoris" (Clemz).

Consolidation du script diagnostic run_test_echange_vues_favoris.py, validé pas à
pas sur les deux comptes. Contrairement à clemz_automation.py (republication,
baisse de prix), ce module ne travaille pas sur une liste de produits mais sur
l'onglet "Échanger vues et favoris" (#exchangeTabLink) du panneau Clemz.

Séquence par compte, dans l'ordre :
  1. Vues   : ouverture modale (#requestViewsButton) -> 3 états possibles
              (start-send-views / ready-to-create / already-created) -> si besoin,
              envoi (#sendViewsButton, navigateur actif requis pendant tout
              l'envoi) avec la checkbox #createRequestWhenFinished cochée en amont
              pour enchaîner automatiquement sur la création de la demande.
  2. Favoris: ouverture modale (#requestFavsButton) -> soit toast d'erreur "déjà
              fait aujourd'hui" (rien à faire), soit modale simple
              (#confirmSendFavs, donner + recevoir en une seule étape). Poll basé
              sur l'écran de confirmation ("Demande créée"), PAS sur
              #lastRequestFavsDate qui se met à jour dès le clic, bien avant la
              fin réelle de l'envoi.

Apprentissage clé (validé en test réel) : Clemz peut rafraîchir la page tout seul
pendant l'envoi de vues/favoris, ce qui casse le suivi des écrans internes basés
sur un état volatile -> tous les signaux de fin retenus ici sont des textes
observables après un éventuel reload, jamais une simple disparition d'élément.
"""

import os
import asyncio
import random

import logging
import re
from datetime import datetime
from playwright.async_api import async_playwright

try:
    from clemz_cdp import (
        snapshot_by_id,
        get_live_props,
        click_element,
        ensure_panel_open,
        force_panel_visible,
    )
    from session_manager import ensure_session
    from clemz_automation import _close_clemz_promo_tabs, _args_et_kwargs_navigateur
except ImportError:
    from services.clemz_cdp import (
        snapshot_by_id,
        get_live_props,
        click_element,
        ensure_panel_open,
        force_panel_visible,
    )
    from services.session_manager import ensure_session
    from services.clemz_automation import _close_clemz_promo_tabs, _args_et_kwargs_navigateur

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_WAIT_SECONDS = 70 * 60   # garde-fou : ~60 min annoncées par Clemz + marge
POLL_INTERVAL_SECONDS = 60   # on ne guette qu'un changement de texte, pas une animation

# --- Mode pas-à-pas : passe à False pour désactiver toutes les pauses d'un coup ---
# Même pattern que clemz_automation.py -- pause manuelle de vérification à chaque
# étape clé, sans effet quand STEP_BY_STEP=False (comportement normal en prod).
# ⚠️ Réservé aux tests manuels via `python clemz_partage.py <account_key>` en
# terminal interactif -- run() (dashboard/scheduler) refuse de démarrer si True
# (cf. garde-fou dans run()), pour éviter qu'un input() bloquant ne gèle tout
# l'event loop FastAPI.
STEP_BY_STEP = False


def _pause(message):
    """Pause manuelle de vérification. N'a aucun effet si STEP_BY_STEP=False."""
    if STEP_BY_STEP:
        input(f"\n⏸️  {message} (Entrée pour continuer)...")


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
# Parsing / classification (partagés vues et favoris)
# --------------------------------------------------------------------------- #

def parse_percentage(text):
    """Extrait un pourcentage du type '15h50 => 100% 👁️'. Retourne un int ou None."""
    if not text:
        return None
    match = re.search(r"(\d{1,3})\s*%", text)
    return int(match.group(1)) if match else None


def parse_seconds_remaining(text):
    """Extrait le nombre de secondes depuis '10 sec restantes'. Retourne un int ou None."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*sec", text)
    return int(match.group(1)) if match else None


def classify_request_status(text):
    """
    Classifie #lastRequestViewsDate / #lastRequestFavsDate en 3 états :
      - "Aucun(e)"     -> aucune demande n'a jamais été faite
      - "Hier => X%"   -> dernière demande date d'hier
      - "18h50 => X%"  -> demande faite AUJOURD'HUI (même à 0%, la présence d'un
                          horodatage du jour suffit à considérer qu'il ne faut
                          pas en relancer une nouvelle)
    """
    if not text or "aucun" in text.lower():
        return {"state": "none", "percentage": None, "raw_text": text, "need_new_request": True}

    percentage = parse_percentage(text)

    if "hier" in text.lower():
        return {"state": "yesterday", "percentage": percentage, "raw_text": text, "need_new_request": True}

    return {"state": "today", "percentage": percentage, "raw_text": text, "need_new_request": False}


async def _read_last_request_status(cdp, node_id_key):
    by_id = await snapshot_by_id(cdp)
    if node_id_key not in by_id:
        return classify_request_status("")
    props = await get_live_props(cdp, by_id[node_id_key]["node_id"], props=("innerText",))
    return classify_request_status(props.get("innerText", ""))


async def _check_temporization(cdp):
    """
    Détecte la temporisation anti-blocages volontaire de Clemz (côté favoris) :
    #articleTitle = "Temporisation (anti-blocages)", #processStage = compte à
    rebours en secondes. État NORMAL, PAS un blocage.
    """
    by_id = await snapshot_by_id(cdp)

    title_text = ""
    if "articleTitle" in by_id:
        props = await get_live_props(cdp, by_id["articleTitle"]["node_id"], props=("innerText",))
        title_text = props.get("innerText", "")

    if "temporisation" not in title_text.lower():
        return {"active": False, "seconds_left": None}

    seconds_left = None
    if "processStage" in by_id:
        props = await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))
        seconds_left = parse_seconds_remaining(props.get("innerText", ""))

    return {"active": True, "seconds_left": seconds_left}


async def _check_global_error_message(cdp):
    """
    Détecte le toast d'erreur générique de Clemz (#globalMessage/#globalMessageContent),
    ex. "Erreur : tu as déjà fait une demande aujourd'hui" — pas de modale du tout
    dans ce cas.
    """
    by_id = await snapshot_by_id(cdp)
    if "globalMessage" not in by_id:
        return {"visible": False, "text": ""}

    props = await get_live_props(cdp, by_id["globalMessage"]["node_id"], props=("className",))
    visible = "invisible" not in props.get("className", "")

    text = ""
    if visible and "globalMessageContent" in by_id:
        content_props = await get_live_props(cdp, by_id["globalMessageContent"]["node_id"], props=("innerText",))
        text = content_props.get("innerText", "")

    return {"visible": visible, "text": text}


async def _close_global_error_message(cdp):
    by_id = await snapshot_by_id(cdp)
    if "global-message-close" not in by_id:
        return False
    await click_element(cdp, by_id["global-message-close"]["node_id"])
    await asyncio.sleep(0.3)
    return True


async def _check_views_send_in_progress(cdp):
    """
    Détecte l'écran "envoi en cours" des vues (#currentInterface, avec
    data-interface-name="sendViews") -- état NORMAL pendant tout l'envoi (jusqu'à
    ~60 min annoncées par Clemz), PAS un blocage. Sert uniquement à distinguer ce
    cas (attendu) d'un vrai panneau perdu dans les logs -- le signal de fin réel
    reste #lastRequestViewsDate, jamais cet écran (cf. docstring de
    _poll_views_until_today).

    Note : #articleTitle n'est PAS un id sur cet écran (juste une classe CSS sur le
    span du titre) -- on lit donc #sendViewsTimeLeft (texte complet, ex. "Temps
    restant: ~33 min") et #minutes-left (juste le nombre, ex. "33") à la place.
    """
    by_id = await snapshot_by_id(cdp)
    if "currentInterface" not in by_id:
        return {"in_progress": False}

    text = ""
    if "sendViewsTimeLeft" in by_id:
        props = await get_live_props(cdp, by_id["sendViewsTimeLeft"]["node_id"], props=("innerText",))
        text = props.get("innerText", "")

    minutes_left = None
    if "minutes-left" in by_id:
        ml_props = await get_live_props(cdp, by_id["minutes-left"]["node_id"], props=("innerText",))
        raw = (ml_props.get("innerText", "") or "").strip()
        minutes_left = int(raw) if raw.isdigit() else None

    return {"in_progress": True, "text": text, "minutes_left": minutes_left}


class ClemzPartageVuesFavoris:
    def __init__(self, dressing: str = None):
        """
        dressing : "Dressing 1", "Dressing 2", ou None pour les deux (comportement
        historique). Filtrer sur un seul dressing permet un déclenchement manuel
        indépendant par compte, cohérent avec la décision anti-détection de ne
        jamais lancer les deux comptes en même temps.
        """
        if dressing:
            self.accounts = [acc for acc in ACCOUNTS if dressing in acc["name"]]
        else:
            self.accounts = ACCOUNTS

    # ----------------------------------------------------------------- #
    # Navigation
    # ----------------------------------------------------------------- #

    async def _navigate_to_exchange_tab(self, cdp, page, acc):
        # 1. Attente de stabilisation initiale du DOM sous Edge
        await asyncio.sleep(1.5)
        await ensure_panel_open(cdp, page)

        by_id = {}
        for i in range(15):  # Attente active jusqu'à ~7.5s max
            by_id = await snapshot_by_id(cdp)

            if "exchangeTabLink" in by_id:
                break

            # Si au bout de 2 secondes les éléments Clemz ne sont toujours pas là,
            # on force l'affichage du panneau pour déclencher son rendu interne
            if i == 4:
                logger.info(f"🔄 [{acc['name']}] Déclenchement du rendu interne Clemz...")
                await force_panel_visible(cdp)

            await asyncio.sleep(0.5)
            await ensure_panel_open(cdp, page)

        # Diagnostic si toujours introuvable
        if "exchangeTabLink" not in by_id:
            current_url = page.url
            diag_reason = (
                f"Onglet 'Échanger vues et favoris' (exchangeTabLink) introuvable après poll — "
                f"url={current_url!r}, toolBody={'toolBody' in by_id}, miniVinz={'miniVinz' in by_id}, "
                f"nb_ids={len(by_id)}, ids_presents={sorted(by_id.keys())[:20]}"
            )
            logger.error(f"🔬 [{acc['name']}] [DIAG PANNEAU PERDU] {diag_reason}")
            return {"status": "failed", "reason": diag_reason}

        _pause(f"[{acc['name']}] Onglet 'exchangeTabLink' détecté dans le DOM")

        # 2. Clic sur l'onglet
        await click_element(cdp, by_id["exchangeTabLink"]["node_id"])
        await asyncio.sleep(0.8)
        await ensure_panel_open(cdp, page)

        # 3. Vérification de la présence des boutons d'action
        by_id = await snapshot_by_id(cdp)
        if "requestViewsButton" not in by_id or "requestFavsButton" not in by_id:
            return {"status": "failed", "reason": "Onglet Échanges non atteint après navigation"}

        _pause(f"[{acc['name']}] Clic sur l'onglet 'Échanges' effectué — vérifie visuellement l'onglet actif")

        return {"status": "success"}

    # ----------------------------------------------------------------- #
    # Flux VUES
    # ----------------------------------------------------------------- #

    async def _open_request_views_modal(self, cdp, page):
        await ensure_panel_open(cdp, page)
        by_id = await snapshot_by_id(cdp)
        if "requestViewsButton" not in by_id:
            return {"status": "failed", "reason": "Bouton requestViewsButton introuvable"}
        logger.info("👀 Clic sur 'Donner puis recevoir des vues'...")
        await click_element(cdp, by_id["requestViewsButton"]["node_id"])
        await asyncio.sleep(0.8)
        _pause("Modale 'Donner puis recevoir des vues' ouverte — vérifie visuellement")
        return {"status": "success"}

    async def _get_visible_views_modal_block(self, cdp):
        """
        Retourne 'start-send-views' | 'ready-to-create' | 'already-created' | None.

        Poll actif (au lieu d'un instantané unique) : confirmé le 24/08/2026 --
        après le clic sur #requestViewsButton, les 3 blocs peuvent être encore
        totalement absents du DOM pendant quelques centaines de ms/secondes
        (rendu interne Clemz pas terminé), avant d'apparaître -- un seul essai
        immédiat les rate parfois, provoquant un échec "modale non reconnue"
        alors qu'elle finit par se charger normalement juste après.

        En cas de None après le poll complet, logue un diagnostic détaillé
        (présence de chaque ID + sa classe si présent) sur la DERNIÈRE tentative
        -- utile si le blocage est cette fois réel (pas juste un délai de rendu).
        """
        by_id = {}
        for _ in range(10):  # ~2s max
            by_id = await snapshot_by_id(cdp)
            for node_key in ("start-send-views", "ready-to-create", "already-created"):
                if node_key not in by_id:
                    continue
                props = await get_live_props(cdp, by_id[node_key]["node_id"], props=("className",))
                if "d-none" not in props.get("className", ""):
                    return node_key
            await asyncio.sleep(0.2)

        diag = {}
        for node_key in ("start-send-views", "ready-to-create", "already-created"):
            if node_key not in by_id:
                diag[node_key] = "absent du DOM"
                continue
            props = await get_live_props(cdp, by_id[node_key]["node_id"], props=("className",))
            diag[node_key] = f"présent, className={props.get('className', '')!r}"
        logger.error(f"🔬 [DIAG MODALE VUES] Aucun bloc visible détecté après poll (~2s) -- {diag}")
        return None

    async def _close_views_modal(self, cdp):
        by_id = await snapshot_by_id(cdp)
        if "closeViewInfoModalButton" not in by_id:
            return False
        await click_element(cdp, by_id["closeViewInfoModalButton"]["node_id"])
        await asyncio.sleep(0.3)
        return True

    async def _ensure_create_request_when_finished_checked(self, cdp):
        """
        Coche #createRequestWhenFinished AVANT de cliquer sur #sendViewsButton, pour
        que Clemz enchaîne automatiquement sur la création de la demande de vues dès
        la fin de l'envoi — évite une seconde session active pour un second clic.

        Poll actif (au lieu d'une vérification unique) : constaté le 22/08/2026,
        cette checkbox peut ne pas être encore rendue dans le DOM juste après
        l'ouverture de la modale (course de timing sur l'animation d'affichage du
        bloc start-send-views), même si le bloc lui-même est déjà visible.
        """
        by_id = {}
        for _ in range(10):  # ~2s max
            by_id = await snapshot_by_id(cdp)
            if "createRequestWhenFinished" in by_id:
                break
            await asyncio.sleep(0.2)

        if "createRequestWhenFinished" not in by_id:
            return {"status": "failed", "reason": "Checkbox createRequestWhenFinished introuvable après poll"}

        props = await get_live_props(cdp, by_id["createRequestWhenFinished"]["node_id"], props=("checked",))
        if not props.get("checked"):
            await click_element(cdp, by_id["createRequestWhenFinished"]["node_id"])
            await asyncio.sleep(0.3)
        _pause(f"Checkbox 'createRequestWhenFinished' vérifiée/cochée (état avant clic : {props.get('checked')})")
        return {"status": "success"}

    async def _click_send_views_button(self, cdp, page):
        """
        ACTION LONGUE : le navigateur DOIT rester ouvert et actif pendant tout
        l'envoi (contrainte confirmée par Clemz — pas de veille, pas de fermeture).
        """
        await ensure_panel_open(cdp, page)
        by_id = await snapshot_by_id(cdp)
        if "sendViewsButton" not in by_id:
            return {"status": "failed", "reason": "Bouton sendViewsButton introuvable"}
        logger.info("👉 Clic sur 'Envoyer des vues'...")
        await click_element(cdp, by_id["sendViewsButton"]["node_id"])
        await asyncio.sleep(1.0)
        _pause("Clic sur 'Envoyer des vues' effectué — l'envoi a démarré, garde le navigateur actif")
        return {"status": "success"}

    async def _click_create_request_views(self, cdp, page):
        await ensure_panel_open(cdp, page)
        by_id = await snapshot_by_id(cdp)
        if "createRequestViews" not in by_id:
            return {"status": "failed", "reason": "Bouton createRequestViews introuvable"}
        logger.info("👀 Clic sur 'Créer demande de vues'...")
        await click_element(cdp, by_id["createRequestViews"]["node_id"])
        await asyncio.sleep(1.0)
        _pause("Clic sur 'Créer demande de vues' effectué")
        return {"status": "success"}

    async def _check_views_confirmation_screen(self, cdp):
        """
        Détecte l'écran de confirmation de fin d'envoi des VUES (#articleTitle =
        "Demander des vues", #processStage contient "créée") -- même template
        générique que celui des favoris (#progressCloseButton etc.), confirmé le
        22/08/2026. Contrairement au cas favoris, ce n'est PAS le signal de fin
        réel (celui-ci reste #lastRequestViewsDate) mais il faut fermer cet écran
        pour libérer le panneau avant de pouvoir re-naviguer vers l'onglet
        Échanges et lire ce signal.
        """
        by_id = await snapshot_by_id(cdp)

        title_text = ""
        if "articleTitle" in by_id:
            props = await get_live_props(cdp, by_id["articleTitle"]["node_id"], props=("innerText",))
            title_text = props.get("innerText", "")

        if "demander des vues" not in title_text.lower():
            return False

        stage_text = ""
        if "processStage" in by_id:
            props = await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))
            stage_text = props.get("innerText", "")

        return "créée" in stage_text.lower()

    async def _get_fresh_cdp_session(self, page):
        """
        Recrée une session CDP sur la même page (le paramètre `cdp` original
        devient inutilisable après une coupure -- "Connection closed while
        reading from the driver", constaté le 23/08/2026 lors d'un envoi de vues
        de ~5 min). Ne fonctionne que si la page/le navigateur sont eux-mêmes
        toujours vivants -- sinon lève une exception que l'appelant doit gérer.
        """
        new_cdp = await page.context.new_cdp_session(page)
        await new_cdp.send("DOM.enable")
        return new_cdp

    async def _poll_views_until_today(self, cdp, page, acc):
        """
        Poll ACTIF pendant l'envoi de vues. On ne se fie PAS à l'écran interne
        d'envoi (#currentInterface/#minutes-left) car Clemz rafraîchit la page
        tout seul pendant cette phase. Signal de fin : #lastRequestViewsDate passe
        de "none"/"yesterday" à "today" (grâce à la checkbox cochée en amont).
        """
        elapsed = 0
        cdp_failures_consecutifs = 0
        MAX_CDP_FAILURES_CONSECUTIFS = 3

        while elapsed < MAX_WAIT_SECONDS:
            try:
                await ensure_panel_open(cdp, page)

                if await self._check_views_confirmation_screen(cdp):
                    closed = await self._close_favs_progress_screen(cdp)
                    logger.info(f"🔘 [{acc['name']}] [{elapsed}s] Écran 'Demande créée' (vues) détecté, fermeture : {closed}")
                    await ensure_panel_open(cdp, page)

                nav_result = await self._navigate_to_exchange_tab(cdp, page, acc)
                cdp_failures_consecutifs = 0  # une itération a réussi jusqu'ici, on remet à zéro

                if nav_result["status"] != "success":
                    send_progress = await _check_views_send_in_progress(cdp)
                    if send_progress["in_progress"]:
                        minutes_left = send_progress.get("minutes_left")
                        suffix = f", ~{minutes_left} min restantes" if minutes_left is not None else ""
                        logger.info(
                            f"⏳ [{acc['name']}] [{elapsed}s] Envoi des vues toujours en cours "
                            f"({send_progress['text']!r}{suffix}) -- normal, pas d'anomalie, on continue d'attendre."
                        )
                    else:
                        # Pas d'écran "envoi en cours" détecté : probablement l'écran générique de
                        # fin (même template #progressCloseButton/#progressRefreshButton/etc. que
                        # côté favoris, cf. _close_favs_progress_screen) resté ouvert et bloquant la
                        # re-navigation. On tente de le fermer avant d'abandonner ce cycle.
                        closed = await self._close_favs_progress_screen(cdp)
                        if closed:
                            logger.info(f"🔘 [{acc['name']}] [{elapsed}s] Écran de fin (vues) fermé automatiquement — nouvelle tentative immédiate.")
                            await asyncio.sleep(0.5)
                            continue
                        logger.warning(f"⚠️ [{acc['name']}] [{elapsed}s] Re-navigation échouée : {nav_result['reason']}")
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    elapsed += POLL_INTERVAL_SECONDS
                    continue

            except Exception as e:
                # Coupure CDP transitoire (ex: "Connection closed while reading from
                # the driver") -- constaté le 23/08/2026 après ~5 min d'attente,
                # probable mise en veille de la machine. On tente de reconnecter la
                # session CDP sur la même page tant que celle-ci est encore vivante ;
                # abandon propre après quelques échecs consécutifs plutôt que de
                # boucler indéfiniment sur un navigateur réellement mort.
                cdp_failures_consecutifs += 1
                logger.warning(
                    f"⚠️ [{acc['name']}] [{elapsed}s] Erreur CDP transitoire ({cdp_failures_consecutifs}/{MAX_CDP_FAILURES_CONSECUTIFS}) : {e}"
                )

                if page.is_closed():
                    return {"status": "failed", "reason": f"Navigateur fermé de façon inattendue pendant l'attente : {e}"}

                if cdp_failures_consecutifs >= MAX_CDP_FAILURES_CONSECUTIFS:
                    return {"status": "failed", "reason": f"Coupures CDP répétées ({MAX_CDP_FAILURES_CONSECUTIFS}x) : {e}"}

                try:
                    cdp = await self._get_fresh_cdp_session(page)
                    logger.info(f"🔄 [{acc['name']}] [{elapsed}s] Session CDP recréée avec succès, nouvelle tentative.")
                except Exception as reconnect_error:
                    logger.warning(f"⚠️ [{acc['name']}] [{elapsed}s] Échec de reconnexion CDP : {reconnect_error}")

                await asyncio.sleep(5)
                continue

            status = await _read_last_request_status(cdp, "lastRequestViewsDate")
            logger.info(f"⏳ [{acc['name']}] [{elapsed}s] État lastRequestViewsDate : {status}")

            if not status["need_new_request"]:
                logger.info(f"✅ [{acc['name']}] [{elapsed}s] Demande de vues du jour détectée — envoi terminé.")
                return {"status": "success", "elapsed": elapsed, "views_status": status}

            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS

        return {"status": "failed", "reason": f"Timeout ({MAX_WAIT_SECONDS}s) : demande de vues du jour jamais détectée"}

    async def _check_views_instant_completion(self, cdp, acc):
        """
        Cas "envoi instantané" (constaté le 22/08/2026, à côté du cas "lent" avec
        barre de progression) : juste après le clic sur Envoyer/Créer demande, la
        modale #requestViewsButton peut basculer directement sur son état
        "already-created" (#show-request-views-date affiche déjà un horodatage du
        jour) SANS jamais afficher l'écran de progression #currentInterface. Si on
        laisse cette modale ouverte et qu'on tente de re-naviguer vers l'onglet
        Échanges, elle bloque #exchangeTabLink -> DIAG PANNEAU PERDU.

        Retourne un dict de résultat si l'instantané est détecté (à retourner
        directement, poll inutile), ou None si c'est le cas "lent" normal (laisser
        le poll habituel prendre le relai).
        """
        for _ in range(6):  # ~1.8s -- détection rapide, ne doit pas retarder le cas "lent"
            visible_block = await self._get_visible_views_modal_block(cdp)
            if visible_block == "already-created":
                by_id = await snapshot_by_id(cdp)
                status = classify_request_status("")
                if "show-request-views-date" in by_id:
                    props = await get_live_props(cdp, by_id["show-request-views-date"]["node_id"], props=("innerText",))
                    status = classify_request_status(props.get("innerText", ""))
                logger.info(f"⚡ [{acc['name']}] Envoi des vues instantané détecté : {status}")
                await self._close_views_modal(cdp)
                return {"status": "success", "views_status": status}
            await asyncio.sleep(0.3)
        return None

    async def _run_views_exchange(self, cdp, page, acc):
        """
        Résultat : {"status": "success"/"skipped"/"failed", "reason": ..., "views_status": ...}

        IMPORTANT : le bouton "Fermer" (#closeViewInfoModalButton) de cette
        modale ne doit être cliqué QUE dans le cas "already-created" (demande
        déjà faite aujourd'hui). Pour "start-send-views"/"ready-to-create", il
        ne faut PAS cliquer Fermer -- on clique "Envoyer des vues" ou "Créer
        demande de vues" pour LANCER le processus. Un ancien `finally` fermait
        systématiquement la modale même après ces clics de lancement (un
        `finally` s'exécute même après un `return` dans le `try`), risquant de
        cliquer Fermer juste après avoir lancé l'envoi -- bug corrigé le
        22/08/2026, repéré via retour utilisateur sur le HTML de la modale.
        """
        modal_result = await self._open_request_views_modal(cdp, page)
        if modal_result["status"] != "success":
            return {"status": "failed", "reason": modal_result["reason"]}

        visible_block = await self._get_visible_views_modal_block(cdp)
        _pause(f"[{acc['name']}] Bloc de modale vues détecté : {visible_block!r}")

        if visible_block == "start-send-views":
            # ORDRE CORRIGÉ le 23/08/2026 : #createRequestWhenFinished fait partie de
            # l'écran "envoi en cours" (#currentInterface, data-interface-name=
            # "sendViews"), PAS de la modale initiale "start-send-views" -- confirmé
            # via le HTML envoyé par l'utilisateur (la modale ne contient que le
            # paragraphe + #sendViewsButton, aucune checkbox). Il faut donc cliquer
            # "Envoyer des vues" D'ABORD, puis chercher/cocher la checkbox sur
            # l'écran qui apparaît ensuite -- l'inverse (tenté précédemment) échoue
            # systématiquement car la checkbox n'existe pas encore à ce moment.
            send_result = await self._click_send_views_button(cdp, page)
            if send_result["status"] != "success":
                await self._close_views_modal(cdp)
                return {"status": "failed", "reason": send_result["reason"]}

            check_result = await self._ensure_create_request_when_finished_checked(cdp)
            if check_result["status"] != "success":
                # Non bloquant : l'envoi est déjà lancé, on continue sans cocher --
                # il faudra juste recliquer manuellement "Créer demande de vues"
                # après l'envoi si la case n'a pas pu être cochée à temps.
                logger.warning(f"⚠️ [{acc['name']}] {check_result['reason']} -- envoi déjà lancé, on continue sans.")

            instant_result = await self._check_views_instant_completion(cdp, acc)
            if instant_result is not None:
                return instant_result

            _pause(f"[{acc['name']}] Le poll de progression VUES va démarrer maintenant")
            poll_result = await self._poll_views_until_today(cdp, page, acc)
            if poll_result["status"] != "success":
                return poll_result
            return {"status": "success", "views_status": poll_result["views_status"]}

        elif visible_block == "ready-to-create":
            create_result = await self._click_create_request_views(cdp, page)
            if create_result["status"] != "success":
                await self._close_views_modal(cdp)
                return {"status": "failed", "reason": create_result["reason"]}

            instant_result = await self._check_views_instant_completion(cdp, acc)
            if instant_result is not None:
                return instant_result

            _pause(f"[{acc['name']}] Le poll de progression VUES va démarrer maintenant")
            poll_result = await self._poll_views_until_today(cdp, page, acc)
            if poll_result["status"] != "success":
                return poll_result
            return {"status": "success", "views_status": poll_result["views_status"]}

        elif visible_block == "already-created":
            await self._close_views_modal(cdp)
            status = await _read_last_request_status(cdp, "lastRequestViewsDate")
            return {"status": "skipped", "reason": "Demande de vues déjà en cours/terminée pour aujourd'hui", "views_status": status}

        else:
            # Modale non reconnue (aucun des 3 blocs start-send-views/ready-to-create/
            # already-created détecté comme visible) -- bug constaté le 24/08/2026 :
            # confondu par erreur avec "already-created", provoquant un skip silencieux
            # alors que views_status montrait 'yesterday'/need_new_request=True (preuve
            # qu'aucune vraie demande n'avait été faite aujourd'hui). Ne JAMAIS traiter
            # ce cas comme un succès/skip -- c'est un vrai échec de détection.
            await self._close_views_modal(cdp)
            logger.error(f"❌ [{acc['name']}] Bloc de modale vues non reconnu (visible_block=None) -- ni start-send-views, ni ready-to-create, ni already-created.")
            return {"status": "failed", "reason": "Bloc de modale vues non reconnu après ouverture (ni start-send-views, ni ready-to-create, ni already-created)"}

    # ----------------------------------------------------------------- #
    # Flux FAVORIS
    # ----------------------------------------------------------------- #

    async def _open_request_favs_modal(self, cdp, page):
        await ensure_panel_open(cdp, page)
        by_id = await snapshot_by_id(cdp)
        if "requestFavsButton" not in by_id:
            return {"status": "failed", "reason": "Bouton requestFavsButton introuvable"}
        logger.info("💞 Clic sur 'Donner puis recevoir des favoris'...")
        await click_element(cdp, by_id["requestFavsButton"]["node_id"])
        await asyncio.sleep(0.8)
        _pause("Modale/toast favoris ouvert(e) — vérifie s'il s'agit d'une modale ou d'un toast d'erreur")
        return {"status": "success"}

    async def _click_confirm_send_favs(self, cdp, page):
        """
        ACTION RÉELLE (donner + recevoir en une seule étape) : Clemz distribue
        plusieurs centaines de favoris, remplit la liste de favoris personnelle
        (impact sur les achats), et génère des notifications.

        Poll actif (au lieu d'une vérification unique) : constaté le 28/08/2026 --
        #confirmSendFavs peut ne pas être encore rendu au moment du premier
        snapshot, même si la modale est bien ouverte (bouton confirmé présent en
        HTML une fois complètement chargée), provoquant un échec "introuvable" à
        tort.
        """
        await ensure_panel_open(cdp, page)

        by_id = {}
        for _ in range(10):  # ~2s max
            by_id = await snapshot_by_id(cdp)
            if "confirmSendFavs" in by_id:
                break
            await asyncio.sleep(0.2)

        if "confirmSendFavs" not in by_id:
            return {"status": "failed", "reason": "Bouton confirmSendFavs introuvable après poll"}

        logger.info("💘 Clic sur 'Donner des favs'...")
        _pause("⚠️ ACTION RÉELLE sur le point d'être lancée : envoi de plusieurs centaines de favoris — dernière vérification avant clic")
        await click_element(cdp, by_id["confirmSendFavs"]["node_id"])
        await asyncio.sleep(1.0)
        _pause("Clic sur 'Donner des favs' effectué")
        return {"status": "success"}

    async def _check_favs_send_confirmation(self, cdp):
        """
        Détecte l'écran de confirmation de FIN D'ENVOI : #articleTitle = "Demander
        des favoris", #processStage contient "créée". C'est le signal fiable de
        fin d'envoi — PAS #lastRequestFavsDate, qui se met à jour dès le clic sur
        #confirmSendFavs, bien avant la fin réelle de l'envoi.
        """
        by_id = await snapshot_by_id(cdp)

        title_text = ""
        if "articleTitle" in by_id:
            props = await get_live_props(cdp, by_id["articleTitle"]["node_id"], props=("innerText",))
            title_text = props.get("innerText", "")

        if "demander des favoris" not in title_text.lower():
            return {"confirmed": False}

        stage_text = ""
        if "processStage" in by_id:
            props = await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))
            stage_text = props.get("innerText", "")

        return {"confirmed": "créée" in stage_text.lower(), "stage_text": stage_text}

    async def _close_favs_progress_screen(self, cdp):
        """
        Ferme l'écran de fin d'envoi des favoris ("Envoi des favoris confirmé
        terminé") en cliquant sur le premier bouton visible parmi Fermer /
        Rafraîchir / Mon dressing -- ces boutons sont cachés (display: none)
        pendant l'envoi et apparaissent seulement à la fin (constaté le
        20/08/2026). Sans ce clic, le panneau reste bloqué sur cet écran et
        #exchangeTabLink est introuvable lors de la re-navigation qui suit.
        """
        by_id = await snapshot_by_id(cdp)
        for button_id in ("progressCloseButton", "progressRefreshButton", "progressDressingButton"):
            if button_id not in by_id:
                continue
            props = await get_live_props(cdp, by_id[button_id]["node_id"], props=("style",))
            if "display: none" in (props.get("style", "") or ""):
                continue
            logger.info(f"🔘 Clic sur le bouton de fin d'envoi favoris détecté : #{button_id}")
            await click_element(cdp, by_id[button_id]["node_id"])
            await asyncio.sleep(0.8)
            return True
        return False

    async def _poll_favs_until_confirmed(self, cdp, page, acc):
        """
        Poll ACTIF pendant l'envoi de favoris. Logs informatifs : pourcentage
        d'envoi (#processStage) et temporisation anti-blocages (état normal).
        Signal de fin : écran de confirmation "Demande créée". Une fois détecté,
        on re-navigue vers l'onglet Échanges pour lire le vrai
        #lastRequestFavsDate (absent de l'écran de confirmation lui-même).
        """
        elapsed = 0
        cdp_failures_consecutifs = 0
        MAX_CDP_FAILURES_CONSECUTIFS = 3

        while elapsed < MAX_WAIT_SECONDS:
            try:
                by_id = await snapshot_by_id(cdp)
                cdp_failures_consecutifs = 0  # une itération a réussi jusqu'ici, on remet à zéro

                if "processStage" in by_id:
                    props = await get_live_props(cdp, by_id["processStage"]["node_id"], props=("innerText",))
                    match = re.search(r"\((\d{1,3})\s*%\)", props.get("innerText", ""))
                    if match:
                        logger.info(f"📊 [{acc['name']}] [{elapsed}s] Progression envoi favoris : {match.group(1)}%")

                temporization = await _check_temporization(cdp)
                if temporization["active"]:
                    logger.info(f"⏸️  [{acc['name']}] [{elapsed}s] Temporisation anti-blocages — {temporization['seconds_left']} sec restantes (normal)")

                confirmation = await self._check_favs_send_confirmation(cdp)
                if confirmation["confirmed"]:
                    logger.info(f"✅ [{acc['name']}] [{elapsed}s] Envoi des favoris confirmé terminé.")
                    closed = await self._close_favs_progress_screen(cdp)
                    logger.info(f"🔘 [{acc['name']}] Fermeture écran fin d'envoi favoris : {closed}")
                    await ensure_panel_open(cdp, page)
                    nav_result = await self._navigate_to_exchange_tab(cdp, page, acc)
                    if nav_result["status"] != "success":
                        return {"status": "success", "elapsed": elapsed, "favs_status": None}
                    status = await _read_last_request_status(cdp, "lastRequestFavsDate")
                    return {"status": "success", "elapsed": elapsed, "favs_status": status}

                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                elapsed += POLL_INTERVAL_SECONDS

            except Exception as e:
                # Même logique de reconnexion transitoire que _poll_views_until_today
                # -- cf. commentaire là-bas pour le contexte complet.
                cdp_failures_consecutifs += 1
                logger.warning(
                    f"⚠️ [{acc['name']}] [{elapsed}s] Erreur CDP transitoire ({cdp_failures_consecutifs}/{MAX_CDP_FAILURES_CONSECUTIFS}) : {e}"
                )

                if page.is_closed():
                    return {"status": "failed", "reason": f"Navigateur fermé de façon inattendue pendant l'attente : {e}"}

                if cdp_failures_consecutifs >= MAX_CDP_FAILURES_CONSECUTIFS:
                    return {"status": "failed", "reason": f"Coupures CDP répétées ({MAX_CDP_FAILURES_CONSECUTIFS}x) : {e}"}

                try:
                    cdp = await self._get_fresh_cdp_session(page)
                    logger.info(f"🔄 [{acc['name']}] [{elapsed}s] Session CDP recréée avec succès, nouvelle tentative.")
                except Exception as reconnect_error:
                    logger.warning(f"⚠️ [{acc['name']}] [{elapsed}s] Échec de reconnexion CDP : {reconnect_error}")

                await asyncio.sleep(5)

        return {"status": "failed", "reason": f"Timeout ({MAX_WAIT_SECONDS}s) : confirmation de fin d'envoi des favoris jamais détectée"}

    async def _run_favs_exchange(self, cdp, page, acc):
        """Résultat : {"status": "success"/"skipped"/"failed", "reason": ..., "favs_status": ...}"""
        modal_result = await self._open_request_favs_modal(cdp, page)
        if modal_result["status"] != "success":
            return {"status": "failed", "reason": modal_result["reason"]}

        # Cas particulier : PAS de modale mais un toast d'erreur si une demande a
        # déjà été faite aujourd'hui.
        error_message = await _check_global_error_message(cdp)
        if error_message["visible"]:
            logger.info(f"ℹ️  [{acc['name']}] Demande de favoris déjà faite aujourd'hui : {error_message['text']!r}")
            _pause(f"[{acc['name']}] Toast d'erreur détecté : {error_message['text']!r} — rien à envoyer aujourd'hui")
            await _close_global_error_message(cdp)
            status = await _read_last_request_status(cdp, "lastRequestFavsDate")
            return {"status": "skipped", "reason": "Demande de favoris déjà faite aujourd'hui", "favs_status": status}

        confirm_result = await self._click_confirm_send_favs(cdp, page)
        if confirm_result["status"] != "success":
            return {"status": "failed", "reason": confirm_result["reason"]}

        _pause(f"[{acc['name']}] Le poll de progression FAVORIS va démarrer maintenant")
        poll_result = await self._poll_favs_until_confirmed(cdp, page, acc)
        if poll_result["status"] != "success":
            return poll_result

        return {"status": "success", "favs_status": poll_result["favs_status"]}

    # ----------------------------------------------------------------- #
    # Orchestration par compte / globale
    # ----------------------------------------------------------------- #

    async def _run_single_account(self, playwright, acc):
        context = None
        try:
            session_ok = await ensure_session(acc["account_key"])
            if not session_ok:
                logger.error(f"❌ [{acc['name']}] Session expirée — automatisation annulée.")
                return {
                    "account": acc["name"],
                    "vues": {"status": "failed", "reason": "Session Vinted expirée"},
                    "favoris": {"status": "failed", "reason": "Session Vinted expirée"},
                }

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
            await _close_clemz_promo_tabs(context, page)

            await page.goto(acc["url"], wait_until="networkidle")

            cdp = await context.new_cdp_session(page)
            await cdp.send("DOM.enable")

            # Poll actif (au lieu d'une vérification unique) : l'extension Clemz s'injecte
            # de façon asynchrone après le chargement de la page Vinted -- un délai
            # supplémentaire est possible juste après un reset de l'extension (panneau
            # Maintenance), constaté le 22/08/2026 (échec immédiat "#miniVinz introuvable"
            # alors que l'extension finissait par s'injecter quelques secondes plus tard).
            by_id = {}
            for _ in range(10):  # ~3s max
                by_id = await snapshot_by_id(cdp)
                if "miniVinz" in by_id:
                    break
                await asyncio.sleep(0.3)

            if "miniVinz" not in by_id:
                logger.error(f"❌ [{acc['name']}] #miniVinz introuvable après poll — extension non injectée.")
                return {
                    "account": acc["name"],
                    "vues": {"status": "failed", "reason": "Bouton Clemz introuvable au chargement (après poll)"},
                    "favoris": {"status": "failed", "reason": "Bouton Clemz introuvable au chargement (après poll)"},
                }

            await ensure_panel_open(cdp, page)
            _pause(f"[{acc['name']}] Navigateur ouvert et panneau Clemz visible — vérifie visuellement avant de continuer")

            navigation_result = await self._navigate_to_exchange_tab(cdp, page, acc)
            if navigation_result["status"] != "success":
                logger.error(f"❌ [{acc['name']}] {navigation_result['reason']}")
                return {
                    "account": acc["name"],
                    "vues": navigation_result,
                    "favoris": navigation_result,
                }

            logger.info(f"👀 [{acc['name']}] Début du flux VUES...")
            vues_result = await self._run_views_exchange(cdp, page, acc)
            logger.info(f"👀 [{acc['name']}] Résultat vues : {vues_result}")
            _pause(f"[{acc['name']}] Flux VUES terminé : {vues_result}")

            logger.info(f"💞 [{acc['name']}] Début du flux FAVORIS...")
            favoris_result = await self._run_favs_exchange(cdp, page, acc)
            logger.info(f"💞 [{acc['name']}] Résultat favoris : {favoris_result}")
            _pause(f"[{acc['name']}] Flux FAVORIS terminé : {favoris_result}")

            return {"account": acc["name"], "vues": vues_result, "favoris": favoris_result}

        except Exception as e:
            logger.error(f"❌ [{acc['name']}] Erreur fatale : {e}")
            reason = {"status": "failed", "reason": f"Erreur fatale : {str(e)[:150]}"}
            return {"account": acc["name"], "vues": reason, "favoris": reason}

        finally:
            if context:
                try:
                    await context.close()
                    logger.info(f"🔒 [{acc['name']}] Navigateur fermé proprement.")
                except Exception:
                    pass

    async def run_one(self, account_key):
        """
        Exécute UN SEUL compte (même logique que run(), mais sans lancer l'autre
        en parallèle) -- pratique pour tester en mode pas-à-pas (STEP_BY_STEP=True)
        sans entrelacer les pauses des deux comptes dans le même terminal.
        """
        from services.clemz_auto_message import (
            request_preemption,
            release_preemption,
            get_profile_lock,
        )

        acc = next((a for a in self.accounts if a["account_key"] == account_key), None)
        if acc is None:
            raise ValueError(
                f"Compte inconnu : {account_key!r} (attendu : "
                f"{', '.join(a['account_key'] for a in self.accounts)})"
            )

        profile_lock = get_profile_lock(account_key)

        await request_preemption(account_key)
        await profile_lock.acquire()
        try:
            async with async_playwright() as p:
                result = await self._run_single_account(p, acc)
        finally:
            profile_lock.release()
            await release_preemption(account_key)

        return [result]

    async def run(self):
        """
        Exécute les comptes sélectionnés SÉQUENTIELLEMENT (jamais en parallèle),
        avec un délai aléatoire entre deux comptes réels -- même logique
        anti-détection que ClemzAutomation.run(). Si self.accounts ne contient
        qu'un seul compte (déclenchement par dressing), le délai ne s'applique
        simplement pas.
        """
        from services.clemz_auto_message import (
            request_preemption_all,
            release_preemption_all,
            get_profile_lock,
            ACCOUNTS as WATCHDOG_ACCOUNTS,
        )

        profile_locks = [get_profile_lock(acc["account_key"]) for acc in WATCHDOG_ACCOUNTS]

        await request_preemption_all()
        for lock in profile_locks:
            await lock.acquire()
        try:
            async with async_playwright() as p:
                results = []
                for i, acc in enumerate(self.accounts):
                    if i > 0:
                        delay = random.uniform(120, 300)
                        await asyncio.sleep(delay)
                    try:
                        result = await self._run_single_account(p, acc)
                    except Exception as e:
                        reason = {"status": "failed", "reason": f"Exception non interceptée : {str(e)[:150]}"}
                        result = {"account": acc["name"], "vues": reason, "favoris": reason}
                    results.append(result)
        finally:
            for lock in profile_locks:
                lock.release()
            await release_preemption_all()

        return results


if __name__ == "__main__":
    import sys

    async def _test():
        automation = ClemzPartageVuesFavoris()
        account_key = sys.argv[1] if len(sys.argv) > 1 else None
        if account_key:
            results = await automation.run_one(account_key)
        else:
            results = await automation.run()
        print(results)

    asyncio.run(_test())