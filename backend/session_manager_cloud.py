"""
Version cloud de session_manager.py -- vérification de session UNIQUEMENT
(jamais de reconnexion interactive), pour les DEUX dressings. Contrairement à
la version locale (utilisée pour Clemz sur Windows), il n'y a ici ni
navigateur visible, ni notification Windows, ni attente humaine : impossible
sur un VPS sans écran. En cas de session expirée, on abandonne immédiatement
et on notifie par email -- la reconnexion se fait alors manuellement via VNC
(create_cloud_session.py), à l'initiative de l'utilisateur.
"""
import logging
import os

from playwright.async_api import async_playwright

from services.email_notifier import envoyer_notification_echec

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))

ACCOUNTS_CLOUD = {
    "dressing1": {
        "name": "Dressing 1",
        "member_id": "287248160",
        "session": os.path.join(_BASE, "vinted_session_cloud_d1"),
    },
    "dressing2": {
        "name": "Dressing 2",
        "member_id": "3136979514",
        "session": os.path.join(_BASE, "vinted_session_cloud_d2"),
    },
}


async def _is_session_valid(page, member_id):
    """Même logique de détection que session_manager.py (bouton 'Se connecter' = déconnecté)."""
    try:
        url = f"https://www.vinted.fr/member/{member_id}"
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        login_button = await page.query_selector('[data-testid="header--login-button"]')
        if login_button:
            logger.warning("🔒 Session cloud invalide — bouton 'Se connecter' détecté.")
            return False

        logger.info(f"✅ Session cloud active — URL : {page.url}")
        return True

    except Exception as e:
        logger.error(f"❌ Erreur vérification session cloud : {e}")
        return False


async def ensure_session_cloud(account_key):
    """
    Vérifie la session Vinted dédiée au cloud pour UN compte (dressing1 ou
    dressing2), en headless STRICT. Retourne True si valide, False sinon (et
    notifie par email dans ce cas -- aucune tentative de réparation auto).
    """
    account = ACCOUNTS_CLOUD.get(account_key)
    if not account:
        logger.error(f"❌ Compte cloud inconnu : {account_key}")
        return False

    if not os.path.isdir(account["session"]):
        message = (
            f"Aucune session cloud trouvée pour {account['name']} "
            f"({os.path.basename(account['session'])}/ absent).\n"
            f"Lance : python3 infra/create_cloud_session.py {account_key}"
        )
        logger.error(f"❌ {message}")
        envoyer_notification_echec(f"Session cloud jamais initialisée — {account['name']}", message)
        return False

    args = ["--disable-blink-features=AutomationControlled"]

    async with async_playwright() as p:
        context = None
        try:
            context = await p.chromium.launch_persistent_context(
                account["session"],
                headless=True,
                no_viewport=True,
                args=args,
            )
            page = context.pages[0] if context.pages else await context.new_page()

            is_valid = await _is_session_valid(page, account["member_id"])

            if not is_valid:
                message = (
                    f"La session Vinted cloud de {account['name']} a expiré.\n\n"
                    "Reconnecte-toi manuellement :\n"
                    "1. ssh -L 5900:localhost:5900 ubuntu@<IP_DU_VPS>\n"
                    "2. Xvfb :1 -screen 0 1280x800x24 &\n"
                    "   DISPLAY=:1 fluxbox &\n"
                    "   x11vnc -display :1 -nopw -listen localhost -xkb &\n"
                    "3. Connecte un client VNC sur localhost:5900\n"
                    f"4. python3 infra/create_cloud_session.py {account_key}"
                )
                envoyer_notification_echec(f"Session cloud expirée — {account['name']}", message)

            return is_valid

        except Exception as e:
            message = f"Erreur lors de la vérification de la session cloud ({account['name']}) :\n\n{e}"
            logger.error(f"❌ {message}")
            envoyer_notification_echec(f"Erreur vérification session cloud — {account['name']}", message)
            return False

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass


async def ensure_both_sessions_cloud():
    """
    Vérifie les DEUX dressings. Retourne (ok_d1: bool, ok_d2: bool) -- les deux
    vérifications se font même si la première échoue, pour avoir l'état complet
    en un seul passage (même logique que run_full_sync en local).
    """
    ok_d1 = await ensure_session_cloud("dressing1")
    ok_d2 = await ensure_session_cloud("dressing2")
    return ok_d1, ok_d2