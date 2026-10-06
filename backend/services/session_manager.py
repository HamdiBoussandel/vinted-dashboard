import asyncio
import logging
import os
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# Timeout max d'attente de reconnexion manuelle (3 minutes)
RECONNECT_TIMEOUT_SECONDS = 180
# Intervalle de vérification de reconnexion
RECONNECT_POLL_INTERVAL = 3

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# NOUVEAU (11/08/2026) : suppression de "chrome_scraper"/"edge_scraper" qui
# pointaient vers des dossiers de session (vinted_session_chrome/edge) jamais
# réellement utilisés par scrap_vinted() — source de confusion et de faux
# diagnostics. Un seul jeu de sessions par navigateur désormais (clemz_session_*),
# partagé entre scraping et automatisation Clemz (jamais exécutés en même temps).
ACCOUNTS = {
    # MIGRÉ (08/09/2026) : vrai Chrome (channel="chrome") + Clemz préchargée
    # manuellement dans le profil via chrome://extensions (Charger l'extension
    # non empaquetée), même traitement que Dressing 2 le 05/09 -- corrige
    # l'empreinte TLS/JA3-JA4 du Chromium embarqué. Plus de --load-extension :
    # l'extension vit désormais DANS le profil clemz_session_chrome_d1_reel
    # (voir _options_lancement ci-dessous). Coïncide avec le changement de
    # compte Vinted de Dressing 1 (nouveau member_id 287248160).
    "chrome_clemz": {
        "name": "Dressing 1 - Chrome",
        "member_id": "287248160",
        "session": os.path.join(_BASE, "clemz_session_chrome_d1_reel"),
        "executable": None,
        "extension": None,
        "channel": "chrome",
    },
    # MIGRÉ (05/09/2026) : vrai Chrome (channel="chrome") + Clemz préchargée
    # manuellement dans le profil via chrome://extensions (Charger l'extension
    # non empaquetée), pour corriger l'empreinte TLS/JA3-JA4 du Chromium
    # embarqué -- identifiée comme la faille anti-détection la plus prioritaire.
    # Plus de --load-extension : l'extension vit désormais DANS le profil
    # clemz_session_chrome_reel (voir _options_lancement ci-dessous).
    "edge_clemz": {
        "name": "Dressing 2 - Chrome",
        "member_id": "3136979514",
        "session": os.path.join(_BASE, "clemz_session_chrome_reel"),
        "executable": None,
        "extension": None,
        "channel": "chrome",
    },
    # Dressing 3 (brouillons, Brave, member_id 287248160) a fusionné dans
    # Dressing 1 le 08/09/2026 -- même compte Vinted, même profil désormais
    # (voir vinted_draft_creation_worker.py). Rôle "brave_brouillons" supprimé.
}

def _send_notification(title, message):
    """
    Envoie une notification Windows native.
    Utilise plyer si disponible, sinon un fallback via PowerShell.
    """
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="VintedPro",
            timeout=30,
        )
    except Exception:
        # Fallback PowerShell si plyer n'est pas installé
        try:
            import subprocess
            ps_cmd = (
                f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
                f'ContentType = WindowsRuntime] | Out-Null; '
                f'$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02; '
                f'$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template); '
                f'$xml.GetElementsByTagName("text")[0].AppendChild($xml.CreateTextNode("{title}")) | Out-Null; '
                f'$xml.GetElementsByTagName("text")[1].AppendChild($xml.CreateTextNode("{message}")) | Out-Null; '
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); '
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("VintedPro").Show($toast);'
            )
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
        except Exception as e:
            logger.warning(f"⚠️ Notification impossible : {e}")


def _options_lancement(account, args, headless):
    """
    Construit les kwargs de launch_persistent_context pour un compte donné.
    Cas normal (Chromium embarqué + --load-extension) : inchangé. Cas d'un
    compte migré vers un vrai navigateur avec l'extension déjà installée
    manuellement dans le profil (account["channel"] défini, ex: "chrome") :
    pas de --load-extension, juste ignore_default_args pour neutraliser le
    --disable-extensions que Playwright ajoute par défaut et qui désactiverait
    sinon l'extension déjà présente dans le profil.
    """
    options = {
        "executable_path": account["executable"],
        "headless": headless,
        "no_viewport": True,
        "args": args,
    }
    if account.get("channel"):
        options["channel"] = account["channel"]
        options["ignore_default_args"] = [
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
        ]
    return options


async def _page_unique(context):
    """
    Retourne un unique onglet exploitable pour ce contexte, en fermant tous les
    autres déjà ouverts. Le profil persistant peut avoir restauré la session
    précédente avec plusieurs onglets (accueil Vinted, page membre visitée lors
    d'une vérification antérieure, etc.) -- sans ce nettoyage, l'utilisateur se
    retrouve face à 2-3 onglets Vinted à l'ouverture du navigateur.
    """
    pages = context.pages
    page = pages[0] if pages else await context.new_page()
    for onglet in pages[1:]:
        try:
            await onglet.close()
        except Exception:
            pass
    return page


async def _is_session_valid(page, member_id):
    """
    Navigue vers le profil Vinted et vérifie si la session est réellement active.

    IMPORTANT : la page /member/{id} est PUBLIQUE — elle ne redirige jamais vers
    /login, connecté ou non. Vérifier uniquement l'URL était donc un faux positif
    permanent. On vérifie à la place la présence du bouton "Se connecter" dans le
    header (data-testid="header--login-button") : s'il est présent, on est en
    navigation anonyme, donc PAS connecté.

    Retourne True si réellement connecté, False sinon.
    """
    try:
        url = f"https://www.vinted.fr/member/{member_id}"
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        current_url = page.url
        if "/login" in current_url or "/auth" in current_url:
            logger.info(f"🔒 Session expirée — redirigé vers : {current_url}")
            return False

        # --- Vérification du vrai signal de connexion ---
        login_button = await page.query_selector('[data-testid="header--login-button"]')
        if login_button:
            logger.info(f"🔒 Session invalide — bouton 'Se connecter' détecté dans le header (navigation anonyme).")
            return False

        logger.info(f"✅ Session active — URL : {current_url}")
        return True

    except Exception as e:
        logger.error(f"❌ Erreur vérification session : {e}")
        return False


async def _wait_for_reconnect(page, member_id, account_name):
    """
    Attend que l'utilisateur se reconnecte manuellement.
    Poll toutes les RECONNECT_POLL_INTERVAL secondes en vérifiant l'ABSENCE du
    bouton "Se connecter" dans le header (même signal fiable que _is_session_valid) —
    on ne peut plus se fier à l'URL puisqu'on part de l'accueil, pas de /login.
    Timeout après RECONNECT_TIMEOUT_SECONDS secondes.
    Retourne True si reconnexion détectée, False si timeout.
    """
    logger.info(f"⏳ En attente de reconnexion manuelle pour {account_name}...")
    elapsed = 0

    while elapsed < RECONNECT_TIMEOUT_SECONDS:
        await asyncio.sleep(RECONNECT_POLL_INTERVAL)
        elapsed += RECONNECT_POLL_INTERVAL

        try:
            login_button = await page.query_selector('[data-testid="header--login-button"]')
            if login_button is None:
                logger.info(f"✅ Reconnexion détectée pour {account_name} !")
                return True
        except Exception as e:
            # La page peut être en pleine navigation pendant le flux Google —
            # on ignore l'erreur ponctuelle et on retente au prochain poll.
            logger.debug(f"⏳ Vérification ignorée (page en transition) : {e}")

        remaining = RECONNECT_TIMEOUT_SECONDS - elapsed
        if elapsed % 30 == 0:  # Log toutes les 30s pour ne pas spammer
            logger.info(f"⏳ Toujours en attente... ({remaining}s restantes)")

    logger.error(f"❌ Timeout reconnexion {account_name} — abandon après {RECONNECT_TIMEOUT_SECONDS}s.")
    return False

async def check_session_only(account_key):
    """
    Vérifie l'état d'une session en mode invisible, SANS déclencher le flux de
    reconnexion (pas de fenêtre visible, pas de notification Windows).
    Utilisée pour un diagnostic rapide, à la différence de ensure_session()
    qui ouvre un navigateur visible si la session est expirée.

    :return: True si réellement connecté, False sinon (ou en cas d'erreur)
    """
    account = ACCOUNTS.get(account_key)
    if not account:
        logger.error(f"❌ Compte inconnu : {account_key}")
        return False

    args = [
        "--disable-blink-features=AutomationControlled",
        "--window-size=1024,768",
        "--window-position=2000,2000",
    ]
    if account.get("extension"):
        args += [
            f"--disable-extensions-except={account['extension']}",
            f"--load-extension={account['extension']}",
        ]

    async with async_playwright() as p:
        context = None
        try:
            context = await p.chromium.launch_persistent_context(
                account["session"],
                **_options_lancement(account, args, headless=True),
            )
            page = await _page_unique(context)
            return await _is_session_valid(page, account["member_id"])
        except Exception as e:
            logger.error(f"❌ Erreur check_session_only ({account['name']}) : {e}")
            return False
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass


async def check_all_sessions():
    """
    Vérifie tous les comptes déclarés dans ACCOUNTS en parallèle, mode invisible,
    sans reconnexion. Retourne {account_key: bool}.
    """
    keys = list(ACCOUNTS.keys())
    results = await asyncio.gather(*[check_session_only(k) for k in keys])
    return dict(zip(keys, results))


async def ensure_session(account_key):
    """
    Vérifie que la session Vinted est active pour un compte donné.
    La vérification se fait en mode INVISIBLE (headless) par défaut.
    Si la session est expirée, un second navigateur, VISIBLE cette fois,
    est ouvert uniquement pour permettre la reconnexion manuelle.

    :param account_key: "chrome" ou "edge"
    :return: True si session valide ou restaurée, False si échec/timeout
    """
    account = ACCOUNTS.get(account_key)
    if not account:
        logger.error(f"❌ Compte inconnu : {account_key}")
        return False

    # Args pour la vérification invisible (position hors-écran sans conséquence,
    # jamais montrée à l'utilisateur)
    args_headless = [
        "--disable-blink-features=AutomationControlled",
        "--window-size=1024,768",
        "--window-position=2000,2000",
    ]
    # Args pour la reconnexion VISIBLE — repositionnée sur l'écran réel, sinon
    # la fenêtre reste invisible même en cliquant sur la barre des tâches.
    args_visible = [
        "--disable-blink-features=AutomationControlled",
        "--window-size=1024,768",
        "--window-position=100,100",
    ]
    if account.get("extension"):
        for args in (args_headless, args_visible):
            args += [
                f"--disable-extensions-except={account['extension']}",
                f"--load-extension={account['extension']}",
            ]

    async with async_playwright() as p:
        context = None
        try:
            # --- Étape 1 : vérification INVISIBLE ---
            logger.info(f"🔍 Vérification session (invisible) : {account['name']}...")

            context = await p.chromium.launch_persistent_context(
                account["session"],
                **_options_lancement(account, args_headless, headless=True),
            )
            page = await _page_unique(context)

            is_valid = await _is_session_valid(page, account["member_id"])

            if is_valid:
                logger.info(f"✅ {account['name']} — session valide, rien à faire.")
                return True

            # --- Session expirée : on referme le contexte invisible ---
            logger.warning(f"⚠️ {account['name']} — session expirée, reconnexion nécessaire.")
            await context.close()
            context = None

            # --- Étape 2 : réouverture VISIBLE (sur l'écran), pour la reco manuelle ---
            context = await p.chromium.launch_persistent_context(
                account["session"],
                **_options_lancement(account, args_visible, headless=False),
            )
            page = await _page_unique(context)

            # Naviguer vers l'accueil Vinted (le lien direct /login renvoie une 404)
            await page.goto(
                "https://www.vinted.fr/",
                wait_until="domcontentloaded"
            )
            await page.bring_to_front()

            # Notification Windows -- inutile si personne n'est devant l'écran (cf.
            # échange du 02/10/2026, PC-VM destiné à tourner sans surveillance). Un
            # email est envoyé EN PLUS, pour que la personne puisse se connecter à
            # distance (bureau à distance) et se reconnecter dans la fenêtre de 3 min,
            # plutôt que l'échec passe inaperçu jusqu'à la prochaine vérification
            # manuelle du dashboard.
            _send_notification(
                title=f"⚠️ VintedPro — Session expirée",
                message=f"{account['name']} : reconnecte-toi via Google dans le navigateur ouvert. Tu as 3 minutes."
            )
            from services.email_notifier import envoyer_notification_echec
            envoyer_notification_echec(
                f"Session {account['name']} expirée — reconnexion nécessaire",
                f"La session Vinted de {account['name']} a expiré. Un navigateur visible a été "
                f"ouvert sur le PC pour permettre une reconnexion manuelle (Google), avec une "
                f"fenêtre de {RECONNECT_TIMEOUT_SECONDS}s. Connecte-toi à distance au PC pour "
                f"terminer la reconnexion avant l'expiration de ce délai, sans quoi la tâche en "
                f"cours sera annulée."
            )

            logger.info(f"🔔 Notification envoyée — en attente de reconnexion manuelle...")

            # --- Attente reconnexion ---
            reconnected = await _wait_for_reconnect(
                page,
                account["member_id"],
                account["name"]
            )

            if reconnected:
                logger.info(f"🎉 {account['name']} — session restaurée avec succès.")
                return True
            else:
                logger.error(f"❌ {account['name']} — reconnexion échouée (timeout).")
                _send_notification(
                    title="❌ VintedPro — Reconnexion échouée",
                    message=f"{account['name']} : délai dépassé. La tâche a été annulée."
                )
                envoyer_notification_echec(
                    f"Reconnexion {account['name']} échouée — tâche annulée",
                    f"Le délai de {RECONNECT_TIMEOUT_SECONDS}s pour reconnecter manuellement "
                    f"{account['name']} a expiré. La tâche en cours a été annulée. La session "
                    f"reste à reconnecter pour que les prochaines tâches automatisées fonctionnent."
                )
                return False

        except Exception as e:
            logger.error(f"❌ Erreur ensure_session ({account['name']}) : {e}")
            return False

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass