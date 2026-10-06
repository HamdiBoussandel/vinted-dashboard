"""
Script de diagnostic PONCTUEL -- vérifie que le profil clemz_session_chrome_reel
(créé manuellement, avec Clemz chargée via 'Charger l'extension non empaquetée'
et session Vinted Dressing 2 déjà connectée) est bien retrouvé automatiquement
par Playwright piloté en vrai Chrome (channel="chrome"), SANS jamais utiliser
--load-extension.

Ne touche à aucun fichier de production -- à supprimer une fois le test validé.
"""
import asyncio
import os

from playwright.async_api import async_playwright

_BASE = os.path.dirname(os.path.abspath(__file__))
PROFIL_TEST = os.path.join(_BASE, "clemz_session_chrome_reel")


async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            PROFIL_TEST,
            channel="chrome",
            headless=False,
            no_viewport=True,
            ignore_default_args=["--disable-extensions", "--disable-component-extensions-with-background-pages"],
        )

        # --- Vérification 1 : Clemz est-elle bien listée et active ? ---
        page_ext = await context.new_page()
        await page_ext.goto("chrome://extensions")
        await asyncio.sleep(2)
        print("\n📋 Page chrome://extensions ouverte -- vérifiez visuellement que Clemz apparaît, activée.")
        input("Appuyez sur Entrée une fois vérifié visuellement...")

        # --- Vérification 2 : la session Vinted Dressing 2 est-elle toujours active ? ---
        page_vinted = await context.new_page()
        await page_vinted.goto("https://www.vinted.fr/member/dashboard", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        bouton_login = page_vinted.locator('[data-testid="header--login-button"]')
        est_connecte = await bouton_login.count() == 0

        if est_connecte:
            print("✅ Session Vinted Dressing 2 retrouvée automatiquement -- toujours connecté.")
        else:
            print("⚠️  Session perdue -- le bouton de connexion est visible, il faudrait se reconnecter.")

        print("\n🔎 Vérifiez maintenant VISUELLEMENT sur la page Vinted ouverte si l'icône/panneau Clemz apparaît normalement sur une fiche produit.")
        input("Appuyez sur Entrée pour fermer...")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())