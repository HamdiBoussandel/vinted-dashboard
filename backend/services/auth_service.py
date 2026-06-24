import asyncio
from playwright.async_api import async_playwright

class AuthService:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async def fetch_vinted_auth(self):
        """
        Lance un navigateur éphémère pour aspirer les cookies et le token Bearer.
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.user_agent)
            page = await context.new_page()

            auth_data = {"token": None, "session": None, "ua": self.user_agent}

            # Intercepteur de requêtes pour capturer le Token Bearer
            async def intercept_request(request):
                if "api/v2/catalog/items" in request.url:
                    headers = request.headers
                    if "authorization" in headers:
                        # On extrait le token sans le préfixe 'Bearer '
                        auth_data["token"] = headers["authorization"].replace("Bearer ", "")

            page.on("request", intercept_request)

            try:
                search_url = "https://www.vinted.fr/catalog?catalog[]=5&order=newest_first"
                # On navigue sur Vinted pour générer les cookies de session
                await page.goto(search_url, wait_until="networkidle")
                
                # 2. Attendre un peu plus longtemps ou défiler
                await page.mouse.wheel(0, 500)
                await asyncio.sleep(5)

                # Extraction du cookie de session
                cookies = await context.cookies()
                for cookie in cookies:
                    # Récupération du TOKEN (le fameux eyJ...)
                    if cookie['name'] == 'access_token_web':
                        auth_data["token"] = cookie['value']

                    if cookie['name'] == '_vinted_fr_session':
                        auth_data["session"] = cookie['value']
                        break
                if not auth_data["token"]:
                    print("⚠️ access_token_web non trouvé dans les cookies, tentative via LocalStorage...")
                    # Backup : Parfois Vinted le stocke en LocalStorage au lieu des cookies
                    auth_data["token"] = await page.evaluate("window.localStorage.getItem('access_token_web')")

            except Exception as e:
                print(f"🚨 Erreur lors de la récupération Auth : {e}")
            finally:
                await browser.close()

        return auth_data