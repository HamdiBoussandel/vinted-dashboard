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

            auth_data = {"token": None, "session": None, "ua": self.user_agent, "csrf_token": None}

            # Intercepteur de requêtes pour capturer le Token Bearer ET le x-csrf-token
            # (svc-catalogue/items exige x-csrf-token, absent des cookies -- doit être
            # intercepté depuis une requête réseau réelle du navigateur)
            async def intercept_request(request):
                headers = request.headers
                if "authorization" in headers:
                    auth_data["token"] = headers["authorization"].replace("Bearer ", "")
                if "x-csrf-token" in headers and not auth_data.get("csrf_token"):
                    auth_data["csrf_token"] = headers["x-csrf-token"]

            page.on("request", intercept_request)

            try:
                search_url = "https://www.vinted.fr/catalog?catalog[]=5&order=newest_first"
                # On navigue sur Vinted pour générer les cookies de session
                await page.goto(search_url, wait_until="networkidle")
                
                # 2. Attendre un peu plus longtemps ou défiler
                await page.mouse.wheel(0, 500)
                await asyncio.sleep(5)

                # Extraction du jar complet (nécessaire : Datadome pose des cookies
                # anti-bot en plus de access_token_web / _vinted_fr_session, et ils
                # doivent tous être renvoyés ensemble sur les requêtes curl_cffi)
                cookies = await context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}

                auth_data["token"] = cookie_dict.get("access_token_web")
                auth_data["session"] = cookie_dict.get("_vinted_fr_session")
                auth_data["all_cookies"] = cookie_dict

                if not auth_data["token"]:
                    print("⚠️ access_token_web non trouvé dans les cookies, tentative via LocalStorage...")
                    # Backup : Parfois Vinted le stocke en LocalStorage au lieu des cookies
                    auth_data["token"] = await page.evaluate("window.localStorage.getItem('access_token_web')")

            except Exception as e:
                print(f"🚨 Erreur lors de la récupération Auth : {e}")
            finally:
                await browser.close()

        return auth_data