"""
Diagnostic : remplissage automatique d'un brouillon Vinted à partir d'un lot
généré (status='generation_ok' dans brouillons_pending), sur la session
Dressing 2 (Edge) déjà connectée.

Usage : python vinted_draft_creation_diag.py [lot_id]
Sans argument, prend le premier lot 'generation_ok' trouvé.

Ne clique JAMAIS sur "Ajouter" (publier) -- uniquement sur "Sauvegarder le
brouillon", et seulement après confirmation manuelle. Chaque champ non résolu
(marque, catégorie, taille...) est simplement laissé vide plutôt que deviné.
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

from database import supabase
from services.vinted_draft_filler import (
    URL_NOUVELLE_ANNONCE,
    pause,
    clic_humain,
    remplir_formulaire_complet,
    forcer_fenetre_normale,
    fermer_modale_feedback_si_presente,
)

_BASE = os.path.dirname(os.path.abspath(__file__))
SESSION_EDGE = os.path.join(_BASE, "clemz_session_edge")
EXECUTABLE_EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


# ---------------------------------------------------------------------------
# Récupération du lot depuis Supabase
# ---------------------------------------------------------------------------

def recuperer_lot(lot_id=None):
    requete = supabase.table("brouillons_pending").select("*")
    if lot_id:
        requete = requete.eq("id", lot_id)
    else:
        requete = requete.eq("status", "generation_ok")
    reponse = requete.limit(1).execute()
    if not reponse.data:
        raise ValueError("Aucun lot trouvé (fournis un lot_id ou vérifie qu'un lot 'generation_ok' existe).")
    return reponse.data[0]


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

async def main():
    lot_id_arg = sys.argv[1] if len(sys.argv) > 1 else None
    lot = recuperer_lot(lot_id_arg)

    print(f"📦 Lot {lot['id']} — {lot['gemini_output']['raw']['titre']}")
    print(f"   {len(lot['photos'])} photo(s)")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            SESSION_EDGE,
            executable_path=EXECUTABLE_EDGE,
            headless=False,
            no_viewport=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await forcer_fenetre_normale(context, page)

        print(f"🌐 Ouverture de {URL_NOUVELLE_ANNONCE} ...")
        await page.goto(URL_NOUVELLE_ANNONCE, wait_until="domcontentloaded")
        await pause(1.5, 2.5)

        await remplir_formulaire_complet(page, lot)

        print("\n" + "=" * 60)
        print("🔎 Formulaire rempli. VÉRIFIE VISUELLEMENT dans le navigateur")
        print("   avant de continuer.")
        print("=" * 60)
        reponse = input("Cliquer sur 'Sauvegarder le brouillon' maintenant ? (o/n) : ").strip().lower()

        if reponse == "o":
            bouton = page.locator('[data-testid="upload-form-save-draft-button"]')
            await clic_humain(page, bouton)
            await pause(1.5, 2.5)
            await fermer_modale_feedback_si_presente(page)
            print("✅ Brouillon sauvegardé (vérifie dans ton dressing Vinted).")
        else:
            print("🚫 Sauvegarde annulée, le navigateur reste ouvert pour inspection.")

        input("\nAppuie sur Entrée pour fermer le navigateur...")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())