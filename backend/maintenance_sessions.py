import asyncio
import os
import shutil
import logging
from playwright.async_api import async_playwright

# Configuration simple pour voir les actions de nettoyage
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(self):
        # Chemins des sessions (Source de vérité)
        self.dir_chrome = os.path.join(os.getcwd(), "clemz_session_chrome")
        self.dir_edge = os.path.join(os.getcwd(), "clemz_session_edge")
        
        # Chemins des extensions
        self.ext_chrome = r"C:\Users\hamdi\AppData\Local\Google\Chrome\User Data\Default\Extensions\kjpggncklgopkhbfpohiaomjpljkbifg\1.25.2_0"
        self.ext_edge = r"C:\Users\hamdi\AppData\Local\Microsoft\Edge\User Data\Default\Extensions\kjpggncklgopkhbfpohiaomjpljkbifg\1.25.2_0"
        
        self.exe_edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

    def clear_all_data(self):
        """Option 0 : Supprime physiquement les dossiers de cookies"""
        print("\n" + "⚠️" * 20)
        print("NETTOYAGE RADICAL DES SESSIONS")
        print("⚠️" * 20)
        
        for folder in [self.dir_chrome, self.dir_edge]:
            if os.path.exists(folder):
                try:
                    shutil.rmtree(folder)
                    print(f"✅ Supprimé : {os.path.basename(folder)}")
                except Exception as e:
                    print(f"❌ Erreur sur {folder} : {e}")
            else:
                print(f"ℹ️ {os.path.basename(folder)} est déjà vide.")
        print("\nNettoyage terminé. Tu peux maintenant recréer des sessions propres.")

    async def open_browser(self, browser_type):
        async with async_playwright() as p:
            if browser_type == "CHROME":
                path, ext, exe = self.dir_chrome, self.ext_chrome, None
                name = "Google Chrome (Dressing 1)"
            else:
                path, ext, exe = self.dir_edge, self.ext_edge, self.exe_edge
                name = "Microsoft Edge (Dressing 2)"

            print(f"\n🚀 OUVERTURE : {name}")
            
            try:
                context = await p.chromium.launch_persistent_context(
                    path,
                    executable_path=exe,
                    headless=False,
                    no_viewport=True,
                    # 🛡️ AJOUT DE LA FURTIVITÉ POUR ÉVITER LE BLOCAGE IP
                    args=[
                        f"--disable-extensions-except={ext}",
                        f"--load-extension={ext}",
                        "--disable-blink-features=AutomationControlled",
                        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ]
                )
                
                page = context.pages[0]
                
                # NAVIGATION FURTIVE : On va sur l'accueil d'abord, pas le login
                await page.goto("https://www.vinted.fr/", wait_until="domcontentloaded")
                print("\n💡 CONSEIL : Navigue un peu sur le site (clique sur un article) avant de te connecter.")

                close_event = asyncio.Event()
                page.on("close", lambda _: close_event.set())
                await close_event.wait()
                
                print(f"✅ Session {browser_type} sauvegardée.\n")
                
            except Exception as e:
                print(f"❌ Erreur : {e}")

async def main():
    manager = SessionManager()
    
    while True:
        print("\n" + "="*40)
        print("   MENU MAINTENANCE VINTED / CLEMZ")
        print("="*40)
        print("0. 🔥 TOUT RÉINITIALISER (Supprimer cookies/cache)")
        print("1. Ouvrir CHROME (Dressing 1)")
        print("2. Ouvrir EDGE (Dressing 2)")
        print("3. Quitter")
        
        choix = input("\nOption choisie : ")
        
        if choix == "0":
            confirm = input("Es-tu sûr de vouloir supprimer tous les cookies ? (o/n) : ")
            if confirm.lower() == 'o':
                manager.clear_all_data()
        elif choix == "1":
            await manager.open_browser("CHROME")
        elif choix == "2":
            await manager.open_browser("EDGE")
        elif choix == "3":
            break

if __name__ == "__main__":
    asyncio.run(main())