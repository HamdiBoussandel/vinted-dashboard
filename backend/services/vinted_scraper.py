import asyncio
import re
import httpx
import logging
import random
import os
import traceback
from datetime import datetime
from playwright.async_api import async_playwright
import json
from database import app_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VintedScraper:
    def __init__(self):
        # Configuration Notion
        self.notion_token = "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"
        self.database_id = "2f54e1655c2280bb8bc4f70cf28c0eac"

        self.headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }

        # Chemins et fichiers
        self.user_data_dir = os.path.abspath("vinted_session")
        self.log_file = "error_log.txt"
        self.status_file = "cron_status.json" # Fichier pour le dashboard

    def update_cron_status(self, execution_time, success, created=0, updated=0):
        """Enregistre le statut pour l'affichage sur le dashboard."""
        status_data = {
            "last_execution": datetime.now().isoformat(), # Format complet avec date et heure
            "duration": f"{execution_time:.2f}s",
            "success": success,
            "nb_created": created,
            "nb_updated": updated
        }
        with open(self.status_file, "w") as f:
            json.dump(status_data, f)

    def write_log(self, message, details=None):
        """Journalise les erreurs pour le débogage."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
            if details:
                f.write(f"DETAILS: {details}\n")
            f.write("-" * 60 + "\n")

    async def run_full_sync(self):
        """Méthode principale : Enchaîne le Dressing 1 (Chrome) et le Dressing 2 (Mozilla)."""
        logger.info("🎬 Démarrage de la synchronisation multi-comptes...")
        app_state["status_TEST"] = "Scrapping en cours"
        
        # --- PHASE 1 : DRESSING 1 (CHROME) ---
        # Remplacez par votre ID de membre pour le compte Chrome
        articles_c1 = await self.scrap_vinted(
            browser_type="chromium", 
            session_path="vinted_session", 
            member_id="258359790",
            dressing_name="Dressing 1"
        )

        # --- PHASE 2 : DRESSING 2 (MOZILLA FIREFOX) ---
        # REMPLACER 'ID_SECOND_COMPTE' par l'identifiant numérique du second dressing
        articles_c2 = await self.scrap_vinted(
            session_path="clemz_session_edge", 
            member_id="3136979514",
            dressing_name="Dressing 2"
        )

        # Fusion des résultats des deux comptes
        tous_les_articles = articles_c1 + articles_c2
        
        if not tous_les_articles:
            logger.error("❌ Aucun article récupéré sur les deux dressings.")
            self.update_cron_status(datetime.now(), False)
            return
            
        logger.info(f"✅ Total : {len(tous_les_articles)} articles à synchroniser vers Notion.")
        
        app_state["status_TEST"] = "Mise à jour Notion"
        # Synchronisation globale vers votre base Inventaire Notion existante
        await self.sync_to_notion(tous_les_articles)
        app_state["status_TEST"] = "Synchronisation Réussie"
        logger.info("🏁 Processus complet terminé avec succès.")

    async def scrap_vinted(self, browser_type="chromium", session_path="vinted_session", member_id="",dressing_name=""):
        """Fonction de scrap universelle adaptable à Chrome ou Firefox."""
        async with async_playwright() as p:
            # Sélection dynamique du moteur (Chromium ou Firefox)
            browser_engine = p.chromium if browser_type == "chromium" else p.firefox

            logger.info(f"🚀 Lancement de {browser_type} pour le profil : {session_path}")

            # Gestion spécifique pour Opera (Dressing 2)
            exec_path = None
            if "edge" in session_path.lower():
                # Chemin standard de Microsoft Edge sur Windows
                exec_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

            # Utilisation d'un contexte persistant pour mémoriser les sessions de chaque compte
            context = await browser_engine.launch_persistent_context(
                os.path.abspath(session_path),
                executable_path=exec_path,
                headless=False,
                no_viewport=False, 
                viewport={'width': 1920, 'height': 1080},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox", # Utile pour la stabilité en environnement Cron
                    "--new-window",
                    "--remote-debugging-port=0"
                    ]
            )
            page = context.pages[0]
            scraped_articles = []
            
            try:
                # Navigation vers l'URL du membre spécifié
                url = f"https://www.vinted.fr/member/{member_id}"
                await page.goto(url, wait_until="domcontentloaded")

                # Attente et Scroll (Utilise votre logique scroll_progressif existante)
                await page.wait_for_selector(".feed-grid__item", timeout=15000)
                
                # --- ÉTAPE CRUCIALE : On charge tout le dressing ---
                await self.scroll_progressif(page)

                items = await page.query_selector_all(".feed-grid__item")
                logger.info(f"📦 Total articles détectés : {len(items)}")

                for i, item in enumerate(items):
                    # 1. Filtre produits indisponibles (content.js)
                    if await item.query_selector("div.web_ui__Cell__narrow"):
                        continue 

                    # Date du jour pour marquer le passage du scraper
                    today_str = datetime.now().strftime("%Y-%m-%d")

                    # 2. Sélecteurs identiques à content.js
                    title_el = await item.query_selector('a[title]')
                    raw_title = await title_el.get_attribute("title") if title_el else ""
                    titre = raw_title.split(',')[0].strip()
                    logger.info(f"📦 Total articles détectés : {titre}")

                    prix_el = await item.query_selector('p.web_ui__Text__muted')
                    raw_prix = await prix_el.inner_text() if prix_el else "0"
                    prix = float(re.search(r'\d+[.,]?\d*', raw_prix).group().replace(',', '.')) if re.search(r'\d+', raw_prix) else 0.0

                    vues_el = await item.query_selector('.u-justify-content-between p')
                    raw_vues = await vues_el.inner_text() if vues_el else "0"
                    vues = int(re.search(r'\d+', raw_vues).group()) if re.search(r'\d+', raw_vues) else 0

                    fav_el = await item.query_selector('.web_ui__Cell__body > div.new-item-box__description')
                    raw_fav = await fav_el.inner_text() if fav_el else "0"
                    favoris = int(re.search(r'\d+', raw_fav).group()) if re.search(r'\d+', raw_fav) else 0

                    print(f"📊 Stats counts : Vues : {vues} Favoris : {favoris}")


                    img_el = await item.query_selector("img")
                    img_url = await img_el.get_attribute("src") if img_el else None

                    link_el = await item.query_selector('a[href]')
                    href = await link_el.get_attribute("href") if link_el else ""

                    scraped_articles.append({
                        "nom": titre,
                        "position_dressing": i + 1,
                        "prix": prix,
                        "vues": vues,
                        "favoris": favoris,
                        "dressing": dressing_name,
                        "url_image": img_url,
                        "lien": href if href.startswith('http') else f"https://www.vinted.fr{href}",
                    })

                await context.close()
                return scraped_articles
            except Exception as e:
                logger.error(f"❌ Erreur lors du scrap {dressing_name} : {e}")
                return []
            finally:
                if 'context' in locals():
                    await context.close()
                    
            
    async def sync_to_notion(self, scraped_articles):
        """Synchronise les données avec logs détaillés."""
        # 1. Génération de la date du jour pour la synchronisation
        today_str = datetime.now().strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=60.0) as client:
            client.headers.update(self.headers)
            count_created = 0
            count_updated = 0
            count_desync = 0 # Compteur pour les produits supprimés
            
            try:
                notion_inventory = await self.get_notion_inventory(client)
                
                # Log de début d'envoi
                logger.info("📤 Préparation de l'envoi vers Notion...")

                for art in scraped_articles:
                    existing = notion_inventory.get(art["nom"])
                    
                    # LOGS DES DONNÉES BRUTES (Votre demande)
                    type_action = "MAJ" if existing else "NEW"
                    logger.info(f"👉 [{type_action}] {art['nom']} | {art['prix']}€ | Vues: {art['vues']} | Fav: {art['favoris']}")

                    props = {
                        "Prix": {"number": art["prix"]},
                        "Vues": {"number": art["vues"]},
                        "Favoris": {"number": art["favoris"]},
                        "Dressing": {"select": {"name": art["dressing"]}},
                        "Rang": {"number": art["position_dressing"]}, # Nouvelle colonne
                        "Sync": {"checkbox": True},
                        "Lien": {"url": art["lien"]},
                        "Dernier Scrap": {"date": {"start": today_str}}
                    }
                    if art.get("url_image"): 
                        props["url_image"] = {"url": art["url_image"]}

                    if existing:
                        await client.patch(f"https://api.notion.com/v1/pages/{existing['id']}", json={"properties": props})
                        count_updated += 1
                    else:
                        props["Nom"] = {"title": [{"text": {"content": art["nom"]}}]}
                        props["Traité"] = {"checkbox": False} # Uniquement pour les nouveaux

                        props["Date de publication"] = {"date": {"start": today_str}}

                        await client.post("https://api.notion.com/v1/pages", 
                                        json={"parent": {"database_id": self.database_id}, "properties": props})
                        count_created += 1
                    
                    await asyncio.sleep(0.35) # Protection contre le spam de l'API
                
                # --- PHASE 2 : Désynchronisation des absents ---
                logger.info("🧹 Vérification des produits supprimés ou vendus...")

                # Liste des noms trouvés lors du scrap actuel
                scraped_names = [art["nom"] for art in scraped_articles]
                
                for name, data in notion_inventory.items():
                    # Si le produit est marqué "Sync" dans Notion mais n'est PAS dans le scrap actuel
                    if name not in scraped_names and data.get("sync") is True:
                        logger.info(f"🗑️ Absent du dressing -> Décochage Sync : {name}")
                        try:
                            await client.patch(
                                f"https://api.notion.com/v1/pages/{data['id']}", 
                                json={"properties": {"Sync": {"checkbox": False}}}
                            )
                            count_desync += 1
                            await asyncio.sleep(0.35) 
                        except Exception as e:
                            logger.error(f"Erreur lors du décochage de {name}: {e}")

                self.update_cron_status(datetime.now(), True, count_created, count_updated)
                logger.info(f"🏁 Terminé ! Créés: {count_created} | MAJ: {count_updated} | Désynchronisés: {count_desync}")

            except Exception as e:
                self.update_cron_status(datetime.now(), False)
                app_state["status_TEST"] = "Erreur Système"
                logger.error(f"❌ Erreur Sync : {str(e)}")
                traceback.print_exc()

    async def scroll_progressif(self, page):
        """Réplique la logique de scrollProgressif de content.js."""
        logger.info("🖱️ Début du scroll progressif...")
        last_count = 0
        consecutive_same_count = 0
        
        while True:
            # On compte les articles actuellement chargés
            current_items = await page.query_selector_all(".feed-grid__item")
            current_count = len(current_items)

            # Scroll vers le bas de la page
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2) # Laisse le temps au lazy-loading de Vinted
            
            if current_count == last_count:
                # Petite attente pour être sûr que le contenu charge
                consecutive_same_count += 1
                # Si le compte ne change pas pendant 2 itérations, on a probablement tout
                if consecutive_same_count >= 2:
                    break
            else:
                consecutive_same_count = 0
                logger.info(f"⏳ Chargement... ({current_count} articles détectés)")
            last_count = current_count
        logger.info(f"✅ Chargement terminé : {last_count} articles au total.")
    
    async def get_notion_inventory(self, client):
        """Récupère l'état actuel de la base Notion pour comparaison."""
        inventory = {}
        cursor = None
        has_more = True
        
        while has_more:
            payload = {"start_cursor": cursor} if cursor else {}
            res = await client.post(f"https://api.notion.com/v1/databases/{self.database_id}/query", json=payload)
            data = res.json()
            
            for result in data.get("results", []):
                props = result["properties"]
                name_list = props.get("Nom", {}).get("title", [])
                if name_list:
                    name = name_list[0].get("plain_text")
                    inventory[name] = {
                        "id": result["id"],
                        "sync": props.get("Sync", {}).get("checkbox", False)
                    }
            
            has_more = data.get("has_more", False)
            cursor = data.get("next_cursor")
        return inventory