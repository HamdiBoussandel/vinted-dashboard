"""
Capture manuelle du HTML du formulaire "Vends ton article" de Vinted, pour
construire l'automatisation CDP de création de brouillon.

Usage : python vinted_form_capture_diag.py
Ouvre le navigateur sur ta session Dressing 2 (Edge) déjà connectée, navigue
toi-même dans le formulaire (catégorie, marque, taille, état...), et reviens
dans ce terminal pour capturer le HTML à chaque étape qui t'intéresse.
"""
import asyncio
import os
from datetime import datetime

from playwright.async_api import async_playwright

_BASE = os.path.dirname(os.path.abspath(__file__))
SESSION_EDGE = os.path.join(_BASE, "vinted_session_edge")
EXECUTABLE_EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

DOSSIER_CAPTURES = os.path.join(_BASE, "vinted_form_captures")

URL_NOUVELLE_ANNONCE = "https://www.vinted.fr/items/new"


async def main():
    os.makedirs(DOSSIER_CAPTURES, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            SESSION_EDGE,
            executable_path=EXECUTABLE_EDGE,
            headless=False,
            no_viewport=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=0,0",
                "--window-size=1920,1080",
            ],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"🌐 Ouverture de {URL_NOUVELLE_ANNONCE} ...")
        await page.goto(URL_NOUVELLE_ANNONCE, wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("Navigue MANUELLEMENT dans le navigateur qui vient de s'ouvrir.")
        print("Chaque fois que tu veux capturer l'état actuel du formulaire :")
        print("  1. Reviens dans ce terminal")
        print("  2. Donne un nom court à cette capture (ex: categorie_ouverte)")
        print("  3. Appuie sur Entrée")
        print("Tape 'q' à la place du nom pour quitter proprement.")
        print("=" * 60 + "\n")

        loop = asyncio.get_event_loop()
        compteur = 1

        while True:
            label = await loop.run_in_executor(
                None, input, f"[{compteur}] Nom de la capture (ou 'q' pour quitter) : "
            )
            label = label.strip()

            if label.lower() == "q":
                break

            label_propre = label.replace(" ", "_") or "capture"
            timestamp = datetime.now().strftime("%H%M%S")
            nom_fichier = f"{compteur:02d}_{label_propre}_{timestamp}.html"
            chemin = os.path.join(DOSSIER_CAPTURES, nom_fichier)

            html = await page.content()
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(html)

            print(f"✅ Capturé -> {chemin} ({len(html)} caractères)\n")
            compteur += 1

        await context.close()
        print(f"\n👋 Terminé. {compteur - 1} capture(s) enregistrée(s) dans {DOSSIER_CAPTURES}")


if __name__ == "__main__":
    asyncio.run(main())