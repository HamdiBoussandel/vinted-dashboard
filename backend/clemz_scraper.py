import asyncio
import logging
import os
import re
import httpx
from datetime import datetime
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ClemzScraper:
    def __init__(self):
        # Configuration
        self.user_data_dir = os.path.abspath("vinted_session")
        self.base_url = "https://www.clemz.app/dashboard/activities"
        
        # Configuration Notion (Directe)
        self.notion_token = "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"
        self.database_id = "2f54e1655c2280bb8bc4f70cf28c0eac"
        self.notion_headers = {
            "Authorization": f"Bearer {self.notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }

    def parse_clemz_date(self, date_text):
        """Transforme '13/03 - 00h38' en '2026-03-13'."""
        try:
            match = re.search(r"(\d{2})/(\d{2})", date_text)
            if match:
                day, month = match.groups()
                year = datetime.now().year
                return f"{year}-{month}-{day}"
        except Exception as e:
            logger.error(f"Erreur parsing date '{date_text}': {e}")
        return None

    async def update_notion_date(self, client, item_name, new_date):
        """Recherche l'article par nom et met à jour sa date de republication."""
        query_url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        query_payload = {
            "filter": {
                "property": "Nom",
                "title": {"equals": item_name}
            }
        }
        
        try:
            # 1. Recherche de la page
            res = await client.post(query_url, json=query_payload)
            results = res.json().get("results", [])
            
            if not results:
                logger.warning(f"⚠️ Non trouvé dans Notion : {item_name}")
                return

            page_id = results[0]["id"]
            
            # 2. Mise à jour de la propriété "Date de republication"
            update_url = f"https://api.notion.com/v1/pages/{page_id}"
            update_payload = {
                "properties": {
                    "Date de republication": {"date": {"start": new_date}}
                }
            }
            await client.patch(update_url, json=update_payload)
            logger.info(f"   ✅ Notion mis à jour : {item_name} -> {new_date}")
            
        except Exception as e:
            logger.error(f"   ❌ Erreur Notion pour {item_name} : {e}")

    async def sync_republications(self):
        async with async_playwright() as p:
            logger.info("🚀 Lancement du navigateur...")
            context = await p.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=False,
                args=["--start-maximized"]
            )
            page = context.pages[0]
            
            async with httpx.AsyncClient(headers=self.notion_headers) as client:
                try:
                    logger.info(f"🌐 Accès à Clemz : {self.base_url}")
                    await page.goto(self.base_url, wait_until="networkidle")
                    await page.wait_for_selector("table")
                    
                    rows = await page.query_selector_all("tbody tr")
                    logger.info(f"📊 NOMBRE DE LIGNES TROUVÉES : {len(rows)}")
                    print("-" * 80)

                    for index, row in enumerate(rows):
                        # Extraction Date
                        date_cell = await row.query_selector("td:first-child")
                        raw_date = await date_cell.inner_text() if date_cell else "INCONNUE"
                        formatted_date = self.parse_clemz_date(raw_date)

                        # Clic Détail
                        detail_link = await row.query_selector("a:has-text('Détail')")
                        if detail_link:
                            await detail_link.click()
                            await page.wait_for_selector(".modal-content", timeout=5000)
                            
                            # Extraction des titres
                            titre_elements = await page.query_selector_all(".modal-content .list a.fw-semibold")
                            annonces = [ (await el.inner_text()).strip() for el in titre_elements ]
                            
                            # Logs console
                            logger.info(f"📍 LIGNE {index + 1}/{len(rows)}")
                            logger.info(f"   📅 Date : {raw_date} -> {formatted_date}")
                            logger.info(f"   📦 {len(annonces)} annonces trouvées")

                            # Mapping Notion Direct
                            if formatted_date:
                                for titre in annonces:
                                    await self.update_notion_date(client, titre, formatted_date)
                                    await asyncio.sleep(0.3) # Rate limit Notion
                            
                            # Fermeture par la croix
                            close_btn = await page.query_selector(".modal-content .btn-close")
                            if close_btn:
                                await close_btn.click()
                                await page.wait_for_selector(".modal-content", state="hidden")
                            
                            await asyncio.sleep(0.5)
                            print("-" * 80)

                    logger.info("🏁 Fin de la synchronisation.")

                except Exception as e:
                    logger.error(f"❌ Erreur : {e}")
                finally:
                    await context.close()

if __name__ == "__main__":
    scraper = ClemzScraper()
    asyncio.run(scraper.sync_republications())