"""
Création UNIQUE d'une session Vinted cloud dédiée, pour UN dressing à la fois.
À lancer une fois par compte (dressing1 ET dressing2), via un tunnel VNC --
la connexion Google se fait ainsi directement depuis l'IP du VPS, jamais
mélangée avec la session locale de ton PC.

Usage :
    python3 create_cloud_session.py dressing1
    python3 create_cloud_session.py dressing2

    Depuis ton PC local, dans un AUTRE terminal, avant de lancer ce script :
        ssh -L 5900:localhost:5900 ubuntu@<IP_DU_VPS>

    Puis connecte un client VNC (ex: TightVNC, RealVNC) sur localhost:5900.
    Tu verras le bureau Fluxbox du VPS -- complète la connexion Google dans
    le navigateur qui s'ouvre, comme tu le ferais localement.
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session_manager_cloud import ACCOUNTS_CLOUD


async def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ACCOUNTS_CLOUD:
        print("Usage : python3 create_cloud_session.py [dressing1|dressing2]")
        sys.exit(1)

    account = ACCOUNTS_CLOUD[sys.argv[1]]
    os.environ.setdefault("DISPLAY", ":1")  # affichage Xvfb

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            account["session"],
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.vinted.fr/", wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print(f"Connexion pour : {account['name']}")
        print("Connecte-toi manuellement via le client VNC connecté à ce VPS.")
        print("Une fois connecté sur Vinted (via Google), reviens ici.")
        print("=" * 60)
        input("Appuie sur Entrée une fois la connexion confirmée dans le navigateur...")

        await page.goto(f"https://www.vinted.fr/member/{account['member_id']}", wait_until="domcontentloaded")
        login_button = await page.query_selector('[data-testid="header--login-button"]')
        if login_button:
            print("⚠️ Toujours déconnecté -- la session n'a pas été détectée. Relance le script.")
        else:
            print(f"✅ Session {account['name']} confirmée et sauvegardée.")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())