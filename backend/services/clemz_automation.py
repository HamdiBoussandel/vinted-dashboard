import os
import asyncio
import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REPOST_TIMEOUT_PER_ITEM_SECONDS = 90
REPOST_TIMEOUT_MIN_SECONDS = 300

class ClemzAutomation:
    def __init__(self, produits_d1, produits_d2):
        self.clean_d1 = [p.strip() for p in produits_d1 if p]
        self.clean_d2 = [p.strip() for p in produits_d2 if p]

        _base = os.path.dirname(os.path.abspath(__file__))

        self.accounts = [
            {
                "name": "Chrome - Dressing 1",
                "produits": self.clean_d1,
                "executable": None, 
                "extension" : r"C:\Users\hamdi\AppData\Local\Google\Chrome\User Data\Default\Extensions\kjpggncklgopkhbfpohiaomjpljkbifg\1.25.2_0",
                "session": os.path.join(os.getcwd(), "clemz_session_chrome"),
                "url": "https://www.vinted.fr/member/258359790"
            },
            {
                "name": "Edge - Dressing 2",
                "produits": self.clean_d2,
                "executable": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                "extension": r"C:\Users\hamdi\AppData\Local\Microsoft\Edge\User Data\Default\Extensions\kjpggncklgopkhbfpohiaomjpljkbifg\1.25.2_0",
                "session": os.path.join(os.getcwd(), "clemz_session_edge"),
                "url": "https://www.vinted.fr/member/3136979514"
            }
        ]   
    
    async def _build_list(self, page, acc):
        """
        Recherche chaque produit et l'ajoute à la liste Clemz.
        Retourne une liste de résultats par produit :
        [{"nom": ..., "status": "success"/"failed", "reason": ...}]
        """
        results = []

        await page.wait_for_selector("#toolBody", state="attached", timeout=20000)

        await page.evaluate("""() => {
            const menu = document.getElementById('toolBody');
            if (menu) {
                menu.style.setProperty('display', 'block', 'important');
                menu.style.setProperty('visibility', 'visible', 'important');
                menu.style.setProperty('opacity', '1', 'important');
            }
            document.getElementById('myDressingTabLink')?.click();
            setTimeout(() => {
                const radioAll = document.getElementById('myDressingTargetAll');
                if (radioAll) {
                    radioAll.click();
                    radioAll.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }, 300);
        }""")
        await asyncio.sleep(1.5)

        await page.evaluate("() => { document.getElementById('myDressingButton')?.click(); }")
        await asyncio.sleep(5)

        await page.evaluate("""() => {
            document.getElementById('progressMinimizeButton')?.click();
            setTimeout(() => {
                document.getElementById('miniVinzLogo')?.click();
                document.getElementById('listsTabLink')?.click();
            }, 500);
        }""")

        await page.wait_for_selector("#selectItemsButton", state="visible", timeout=15000)
        await asyncio.sleep(1)

        for index, produit in enumerate(acc["produits"]):
            logger.info(f"🔍 [{acc['name']}] {index + 1}/{len(acc['produits'])} : {produit}")

            if "member" not in page.url:
                await page.goto(acc["url"])
                await asyncio.sleep(2)

            resultat_etape = await page.evaluate(
                """([nomDuProduit, isFirst]) => {
                    return new Promise((resolve) => {
                        const btn = document.getElementById('selectItemsButton');
                        if (!btn) return resolve({ status: "failed", reason: "Bouton Sélection introuvable" });
                        btn.click();

                        const delaySearch = isFirst ? 2500 : 500;
                        const delayClick  = isFirst ? 3500 : 1600;

                        setTimeout(() => {
                            const searchForm  = document.getElementById('searchWordForm');
                            const searchInput = searchForm?.querySelector('input[type="text"]');
                            if (!searchInput) return resolve({ status: "failed", reason: "Champ recherche introuvable" });

                            searchInput.value = nomDuProduit;
                            searchInput.dispatchEvent(new Event('input', { bubbles: true }));
                            searchForm.querySelector('button[type="submit"]')?.click();

                            setTimeout(() => {
                                const product = document.querySelector('.new-item-box__overlay') ||
                                                document.querySelector('.feed-grid__item')       ||
                                                document.querySelector('.item-box');
                                if (!product) return resolve({ status: "failed", reason: "Produit non trouvé dans la grille" });

                                product.removeAttribute('href');
                                product.removeAttribute('target');
                                product.click();

                                setTimeout(() => {
                                    const addBtn = document.getElementById('addToListButton');
                                    if (!addBtn) return resolve({ status: "failed", reason: "Bouton 'Ajouter à la liste' absent" });
                                    addBtn.click();

                                    setTimeout(() => {
                                        document.getElementById('progressCloseButton')?.click();
                                        resolve({ status: "success" });
                                    }, 1200);
                                }, 1000);
                            }, delayClick);
                        }, delaySearch);
                    });
                }""",
                [produit, index == 0],
            )

            results.append({"nom": produit, **resultat_etape})
            logger.info(f"   📊 {produit} -> {resultat_etape}")
            await asyncio.sleep(1)

        return results

    async def _trigger_repost(self, page, acc, expected_count):
        """
        Déclenche la republication de la liste Clemz constituée à l'étape précédente.
        Séquence : clic Republier -> sélection mode -> Go Clemz -> confirmation -> attente fin.

        Retourne {"status": "success"/"failed", "reason": ...}
        """
        try:
            # 1. Clic sur le bouton Republier
            await page.wait_for_selector("#repost-shortcut", state="visible", timeout=15000)
            await page.click("#repost-shortcut")
            await asyncio.sleep(1)

            # 2. Vérification du mode + clic Go Clemz
            await page.wait_for_selector("#repostSelectAction", state="visible", timeout=10000)
            await page.select_option("#repostSelectAction", "1")  # (re)Publier en annonce, jamais brouillon

            await page.wait_for_selector("#repostButton", state="visible", timeout=10000)
            await page.click("#repostButton")
            logger.info(f"♻️  [{acc['name']}] Go Clemz cliqué — {expected_count} article(s) en cours.")

            # 3. Modal de confirmation -> clic Confirmer
            await page.wait_for_selector("#continueAlertButton", state="visible", timeout=15000)
            await page.click("#continueAlertButton")
            logger.info(f"✅ [{acc['name']}] Confirmation envoyée, Clemz prend le relais...")

            # 4. Attente du message de fin.
            # Timeout calculé dynamiquement : 90s de marge par article, plancher à 5 minutes.
            # NOTE : on remplacera get_by_text par un sélecteur précis dès que tu as inspecté l'élément.
            timeout_ms = max(
                expected_count * REPOST_TIMEOUT_PER_ITEM_SECONDS * 1000,
                REPOST_TIMEOUT_MIN_SECONDS * 1000,
            )
            try:
                await page.get_by_text("Republications terminé", exact=False).wait_for(
                    state="visible", timeout=timeout_ms
                )
            except PlaywrightTimeoutError:
                return {
                    "status": "failed",
                    "reason": (
                        f"Timeout ({timeout_ms // 1000}s) : message de fin non détecté. "
                        f"La republication a peut-être échoué ou pris plus de temps que prévu."
                    ),
                }

            logger.info(f"🎉 [{acc['name']}] Republication terminée avec succès.")
            return {"status": "success"}

        except PlaywrightTimeoutError as e:
            return {"status": "failed", "reason": f"Élément introuvable ou timeout : {str(e)[:120]}"}
        except Exception as e:
            return {"status": "failed", "reason": f"Erreur inattendue : {str(e)[:120]}"}

    async def _run_single_automation(self, playwright, acc):
        """
        Orchestre l'automatisation complète pour un compte (Chrome ou Edge) :
        ouverture discrète du navigateur, constitution de la liste, déclenchement
        de la republication, fermeture propre.

        Retourne un dict :
        {
            "account": str,
            "selection_results": [...],  # résultat par produit (étape _build_list)
            "repost_result": {...},       # résultat du déclenchement (étape _trigger_repost)
        }
        """
        if not acc["produits"]:
            logger.info(f"⏭️  [{acc['name']}] Aucun produit à traiter, on passe.")
            return {
                "account": acc["name"],
                "selection_results": [],
                "repost_result": None,
            }

        context = None
        try:
            logger.info(f"🚀 [{acc['name']}] Ouverture du navigateur...")
            context = await playwright.chromium.launch_persistent_context(
                acc["session"],
                executable_path=acc["executable"],
                headless=False,
                no_viewport=True,
                args=[
                    f"--disable-extensions-except={acc['extension']}",
                    f"--load-extension={acc['extension']}",
                    "--window-size=1024,768",
                    # Positionné largement hors zone visible (écran 1920x1080)
                    # pour ne pas encombrer pendant un usage parallèle du PC.
                    # À ajuster si tu as un écran plus grand ou un second moniteur.
                    "--window-position=2000,2000",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(acc["url"], wait_until="networkidle")

            # Fermeture des éventuelles modales de bienvenue/sync Clemz
            await page.evaluate("""() => {
                document.querySelectorAll(
                    '.toolModal .close, .toolModal .btn-secondary, #syncSalesModal .close'
                ).forEach(btn => btn.click());
            }""")
            await asyncio.sleep(1)

            # --- Étape A : Constitution de la liste ---
            selection_results = await self._build_list(page, acc)

            success_count = sum(1 for r in selection_results if r["status"] == "success")
            logger.info(
                f"📋 [{acc['name']}] Sélection terminée : "
                f"{success_count}/{len(acc['produits'])} produit(s) ajouté(s) avec succès."
            )

            # Si aucun produit n'a pu être ajouté, inutile de tenter la republication
            if success_count == 0:
                logger.warning(
                    f"⚠️  [{acc['name']}] Aucun produit sélectionné — republication annulée."
                )
                return {
                    "account": acc["name"],
                    "selection_results": selection_results,
                    "repost_result": {
                        "status": "failed",
                        "reason": "Aucun produit n'a pu être ajouté à la liste Clemz.",
                    },
                }

            # --- Étape B : Déclenchement de la republication ---
            repost_result = await self._trigger_repost(
                page, acc, expected_count=success_count
            )

            return {
                "account": acc["name"],
                "selection_results": selection_results,
                "repost_result": repost_result,
            }

        except Exception as e:
            logger.error(f"❌ [{acc['name']}] Erreur fatale : {e}")
            return {
                "account": acc["name"],
                "selection_results": [],
                "repost_result": {
                    "status": "failed",
                    "reason": f"Erreur fatale lors de l'ouverture ou du pilotage : {str(e)[:120]}",
                },
            }

        finally:
            # Fermeture propre dans tous les cas (succès, échec, exception)
            if context:
                try:
                    await context.close()
                    logger.info(f"🔒 [{acc['name']}] Navigateur fermé proprement.")
                except Exception:
                    pass

    async def run(self):
        """
        Lance l'automatisation sur les deux comptes (Chrome/Dressing 1 et Edge/Dressing 2)
        en parallèle et retourne la liste des résultats structurés, un par compte.

        Retour :
        [
            {"account": "Chrome - Dressing 1", "selection_results": [...], "repost_result": {...}},
            {"account": "Edge - Dressing 2",   "selection_results": [...], "repost_result": {...}},
        ]

        Se termine toujours proprement (pas de blocage infini) pour permettre
        l'enchaînement avec le reporting dans automation_scheduler.py.
        """
        async with async_playwright() as p:
            tasks = [
                self._run_single_automation(p, acc)
                for acc in self.accounts
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Normalisation : si asyncio.gather a capturé une exception sur un compte,
        # on la transforme en résultat structuré plutôt que de la laisser remonter brute
        normalized = []
        for acc, result in zip(self.accounts, results):
            if isinstance(result, Exception):
                normalized.append({
                    "account": acc["name"],
                    "selection_results": [],
                    "repost_result": {
                        "status": "failed",
                        "reason": f"Exception non interceptée : {str(result)[:120]}",
                    },
                })
            else:
                normalized.append(result)

        return normalized


if __name__ == "__main__":
    async def _test():
        automation = ClemzAutomation(
            produits_d1=["Produit Test D1"],
            produits_d2=["Produit Test D2"],
        )
        results = await automation.run()
        for r in results:
            print(r)

    asyncio.run(_test())