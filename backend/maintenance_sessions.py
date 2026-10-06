import asyncio
import os
import shutil
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionManager:

    def __init__(self):
        # Le script étant dans backend, _base est directement le dossier courant
        _base = os.path.dirname(os.path.realpath(__file__))
        
        # Sécurité : Si le script de maintenance est dans un sous-dossier (ex: services), remonte d'un cran
        if os.path.basename(_base) == "services":
            _base = os.path.dirname(_base)

        self.sessions = {
            "vinted_chrome": {
                "name": "Scraping D1 (Chrome - sans Clemz)",
                "path": os.path.join(_base, "vinted_session_chrome"),
                "exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "ext": None,
            },
            "vinted_edge": {
                "name": "Scraping D2 (Edge - sans Clemz)",
                "path": os.path.join(_base, "vinted_session_edge"),
                "exe": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                "ext": None,
            },
            "clemz_chrome": {
                # MIGRÉ (08/09/2026) : vrai Chrome + Clemz préchargée manuellement
                # dans le profil, même traitement que Dressing 2 -- voir
                # services/maintenance_service.py et services/session_manager.py.
                "name": "Automation D1 (Chrome + Clemz)",
                "path": os.path.join(_base, "clemz_session_chrome_d1_reel"),
                "exe": None,
                "ext": None,
                "channel": "chrome",
            },
            "clemz_edge": {
                # MIGRÉ (05/09/2026) : vrai Chrome + Clemz préchargée manuellement
                # dans le profil -- voir services/maintenance_service.py et
                # services/session_manager.py pour le détail.
                "name": "Automation D2 (Chrome + Clemz)",
                "path": os.path.join(_base, "clemz_session_chrome_reel"),
                "exe": None,
                "ext": None,
                "channel": "chrome",
            },
        }

    def clear_all_data(self):
        for key, s in self.sessions.items():
            if os.path.exists(s["path"]):
                try:
                    shutil.rmtree(s["path"])
                    print(f"✅ Supprimé : {s['name']}")
                except Exception as e:
                    print(f"❌ Erreur sur {s['name']} : {e}")
            else:
                print(f"ℹ️ Déjà vide : {s['name']}")

    async def open_browser(self, key):
        s = self.sessions[key]
        print(f"\n🚀 OUVERTURE : {s['name']}")

        # Forcer la taille et le positionnement pour éviter les fenêtres invisibles ou bloquées
        args = [
            "--disable-blink-features=AutomationControlled",
            "--window-size=1280,800",
            "--window-position=0,0"
        ]
        
        launch_options = {
            "headless": False,
            # no_viewport retiré pour assurer une ouverture normale au premier plan
            "args": args,
        }
        if s["exe"]:
            launch_options["executable_path"] = s["exe"]

        if s.get("channel"):
            # Vrai navigateur avec l'extension déjà installée manuellement dans
            # le profil -- pas de --load-extension, juste neutraliser le
            # --disable-extensions par défaut de Playwright.
            launch_options["channel"] = s["channel"]
            launch_options["ignore_default_args"] = [
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ]
        elif s["ext"]:
            args += [
                f"--disable-extensions-except={s['ext']}",
                f"--load-extension={s['ext']}",
            ]

        async with async_playwright() as p:
            try:
                context = await p.chromium.launch_persistent_context(s["path"], **launch_options)
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://www.vinted.fr/", wait_until="domcontentloaded")
                print(f"\n💡 Connecte-toi à Vinted{' et à Clemz' if s['ext'] else ''}.")
                print("Ferme le navigateur manuellement quand c'est bon pour sauvegarder.")

                close_event = asyncio.Event()
                page.on("close", lambda _: close_event.set())
                await close_event.wait()

                print(f"✅ Session sauvegardée : {s['name']}\n")
            except Exception as e:
                print(f"❌ Erreur : {e}")


    def update_extension(self):
        """Option 5 : Met à jour la copie locale de l'extension Clemz"""
        import json
        import glob

        _base = os.path.dirname(os.path.abspath(__file__))
        if os.path.basename(_base) == "services":
            _base = os.path.dirname(_base)

        ext_base = r"C:\Users\hamdi\AppData\Local\Google\Chrome\User Data\Default\Extensions\kjpggncklgopkhbfpohiaomjpljkbifg"
        versions = glob.glob(os.path.join(ext_base, "*"))
        
        if not versions:
            print("❌ Extension Clemz introuvable dans Chrome.")
            return
        
        # Prend la version la plus récente
        latest = max(versions, key=os.path.getmtime)
        _base = os.path.dirname(os.path.realpath(__file__))
        dest = os.path.join(_base, "clemz_extension")
        
        print(f"📦 Version détectée : {os.path.basename(latest)}")
        print(f"📂 Copie vers : {dest}")
        
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(latest, dest)
        
        manifest_path = os.path.join(dest, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        
        manifest.pop("update_url", None)
        manifest.pop("key", None)
        
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=3, ensure_ascii=False)
        
        print(f"✅ Extension mise à jour vers {os.path.basename(latest)} avec succès.")
    

async def main():
    manager = SessionManager()

    while True:
        print("\n" + "="*40)
        print("   MENU MAINTENANCE SESSIONS")
        print("="*40)
        print("0. 🔥 Supprimer TOUTES les sessions")
        print("1. Ouvrir Scraping D1     (Chrome, sans Clemz)")
        print("2. Ouvrir Scraping D2     (Edge,   sans Clemz)")
        print("3. Ouvrir Automation D1   (Chrome, avec Clemz)")
        print("4. Ouvrir Automation D2   (Edge,   avec Clemz)")
        print("5. 🔄 Mettre à jour l'extension Clemz (depuis Chrome)")
        print("6. Quitter")

        choix = input("\nOption choisie : ")

        mapping = {
            "1": "vinted_chrome",
            "2": "vinted_edge",
            "3": "clemz_chrome",
            "4": "clemz_edge",
        }

        if choix == "0":
            confirm = input("Supprimer tous les dossiers de session ? (o/n) : ")
            if confirm.lower() == "o":
                manager.clear_all_data()
        elif choix in mapping:
            await manager.open_browser(mapping[choix])
        elif choix == "5":
            manager.update_extension()
        elif choix == "6":
            break


if __name__ == "__main__":
    asyncio.run(main())