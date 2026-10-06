"""
Script de diagnostic PONCTUEL -- compare l'empreinte TLS (JA4) du profil
clemz_session_chrome_reel piloté par Playwright à celle d'un vrai Chrome
manuel, pour confirmer que la migration channel="chrome" a bien corrigé
l'empreinte réseau.
"""
import asyncio
import json
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
        page = await context.new_page()
        await page.goto("https://tls.peet.ws/api/all")
        contenu = await page.inner_text("body")
        data = json.loads(contenu)
        print("\n🔎 JA4 (profil migré, vrai Chrome via Playwright) :")
        print(data.get("tls", {}).get("ja4"))

        input("\nAppuyez sur Entrée pour fermer...")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())