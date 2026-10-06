"""
Script de capture interactif — identification des IDs du panneau Clemz.

Usage :
    1. Lance ce script (Chrome, Dressing 1, session clemz_session_chrome_d1_reel).
    2. Le panneau Clemz s'ouvre automatiquement.
    3. Dans le navigateur : navigue MANUELLEMENT jusqu'au panneau/bouton
       "Baisse de prix" (ou toute autre fonctionnalité à cartographier),
       clique dessus.
    4. Reviens dans le terminal, appuie sur [Entrée] : le script affiche
       tous les éléments avec un id qui sont APPARUS depuis le dernier
       snapshot (donc probablement liés à l'action que tu viens de faire),
       avec leur className / innerText / tagName pour les identifier.
    5. Répète à chaque étape (ouverture du panneau prix, saisie du %,
       bouton de confirmation, modale de confirmation, message de fin...).
    6. Tape 'q' + [Entrée] pour quitter et fermer proprement le navigateur.

Ne modifie ni clemz_automation.py ni clemz_cdp.py — script de diagnostic
autonome, à jeter une fois les IDs identifiés et reportés dans le code.
"""

import os
import asyncio
import logging
from playwright.async_api import async_playwright

from clemz_cdp import snapshot_by_id, get_live_props, ensure_panel_open
from session_manager import ensure_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Compte utilisé pour la capture — Dressing 1 / Chrome, comme dans clemz_automation.py.
# MIGRÉ (08/09/2026) : vrai Chrome + Clemz préchargée manuellement dans le profil,
# même traitement que Dressing 2 -- plus de --load-extension (voir main() ci-dessous).
ACCOUNT = {
    "name": "Chrome - Dressing 1",
    "executable": None,
    "extension": None,
    "session": os.path.join(_BASE, "clemz_session_chrome_d1_reel"),
    "url": "https://www.vinted.fr/member/287248160",
    "channel": "chrome",
}


async def dump_new_ids(cdp, previous_ids):
    """Affiche les éléments dont l'id est apparu depuis le snapshot précédent."""
    by_id = await snapshot_by_id(cdp)
    current_ids = set(by_id.keys())
    new_ids = current_ids - previous_ids

    if not new_ids:
        print("   (aucun nouvel élément avec id détecté)")
    else:
        print(f"   🆕 {len(new_ids)} nouvel(aux) élément(s) détecté(s) :")
        for _id in sorted(new_ids):
            node_id = by_id[_id]["node_id"]
            try:
                props = await get_live_props(
                    cdp, node_id, props=("tagName", "className", "innerText", "type")
                )
            except Exception as e:
                props = {"erreur": str(e)}
            # innerText tronqué pour la lisibilité terminal
            if "innerText" in props and props["innerText"]:
                props["innerText"] = props["innerText"][:80].replace("\n", " ")
            print(f"      #{_id}  ->  {props}")

    return current_ids


async def main():
    print("🔍 Vérification de la session Clemz (Chrome, Dressing 1)...")
    session_ok = await ensure_session("chrome_clemz")
    if not session_ok:
        print("❌ Session invalide/expirée — reconnecte-toi manuellement puis relance ce script.")
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            ACCOUNT["session"],
            channel=ACCOUNT["channel"],
            headless=False,
            ignore_default_args=[
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,800",
                "--window-position=0,0",
            ],
        )

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(ACCOUNT["url"], wait_until="networkidle")

        cdp = await context.new_cdp_session(page)
        await cdp.send("DOM.enable")

        by_id = await snapshot_by_id(cdp)
        if "miniVinz" not in by_id:
            print("❌ #miniVinz introuvable — extension Clemz non injectée sur cette page.")
            await context.close()
            return

        await ensure_panel_open(cdp, page)
        await asyncio.sleep(0.5)

        previous_ids = set((await snapshot_by_id(cdp)).keys())

        print("=" * 70)
        print("CAPTURE INTERACTIVE — panneau Clemz ouvert.")
        print(f"📸 Snapshot initial : {len(previous_ids)} éléments avec id.")
        print()
        print("-> Navigue dans le navigateur jusqu'au panneau 'Baisse de prix',")
        print("   clique/avance d'UNE étape à la fois, puis reviens ici.")
        print("[Entrée] = nouveau snapshot   |   [q] + Entrée = quitter")
        print("=" * 70)

        while True:
            cmd = input("\n> ").strip().lower()
            if cmd == "q":
                break
            print(f"\n⏱️  Snapshot à {asyncio.get_event_loop().time():.1f}s :")
            previous_ids = await dump_new_ids(cdp, previous_ids)

        await context.close()
        print("🔒 Navigateur fermé.")


if __name__ == "__main__":
    asyncio.run(main())