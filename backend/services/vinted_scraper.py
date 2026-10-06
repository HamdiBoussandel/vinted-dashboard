import asyncio
import re
import logging
import os
import json
import traceback
from datetime import datetime
from playwright.async_api import async_playwright
from database import app_state, supabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class VintedScraper:
    def __init__(self):
        self.db = supabase
        self.log_file = "error_log.txt"
        self.status_file = "cron_status.json"
        # Brouillons repérés (statut "Brouillon") pendant le dernier scrap_vinted() --
        # permet un nettoyage ciblé côté Supabase sans avoir à deviner via la désync.
        self.brouillons_ignores = []

    def update_cron_status(self, start_time, success, created=0, updated=0):
        """
        Enregistre le statut pour l'affichage sur le dashboard.
        CORRIGÉ : calcul de durée depuis start_time (datetime) au lieu d'un float.
        """
        duration = (datetime.now() - start_time).total_seconds()
        status_data = {
            "last_execution": datetime.now().isoformat(),
            "duration": f"{duration:.2f}s",
            "success": success,
            "nb_created": created,
            "nb_updated": updated,
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

    def _verifier_anomalie_session_perdue(self, task_id, dressing_id, dressing_label, articles):
        """
        Détecte l'anomalie "session Vinted perdue EN COURS de scraping" -- distincte
        d'une session invalide dès le départ (déjà détectée par ensure_session avant
        même de scraper). Signal repéré empiriquement : le scraping se déroule sans
        aucune erreur technique (articles bien récupérés, structure de page normale),
        mais TOUS les articles reviennent avec 0 vue -- Vinted a probablement servi
        une page déconnectée/générique sans que le scraper ne puisse s'en apercevoir
        autrement.

        Marque le résultat de ce dressing dans l'historique (succès/échec + raison) et
        journalise l'anomalie si détectée. Retourne True si une anomalie a été détectée
        (pour exclure ce dressing de la synchronisation Supabase -- évite d'écraser les
        vraies données avec des 0 partout).
        """
        from services.automation_scheduler import update_task_result, add_task_global_anomaly

        if not articles:
            update_task_result(task_id, dressing_id, status="failed", reason="Aucun article récupéré")
            add_task_global_anomaly(
                task_id, label=f"Scraping {dressing_label} : aucun article récupéré",
                dressing=dressing_label,
                reason="0 article retourné — à vérifier manuellement (session, structure de page...)"
            )
            return True

        if all(a.get("vues", 0) == 0 for a in articles):
            logger.error(
                f"🔬 [ANOMALIE] {dressing_label} : {len(articles)} article(s), TOUS à 0 vues -- "
                f"session probablement perdue en cours de scraping."
            )
            update_task_result(
                task_id, dressing_id, status="failed",
                reason=f"{len(articles)} article(s) récupéré(s) mais TOUS à 0 vues — "
                       f"session Vinted probablement perdue en cours de route"
            )
            add_task_global_anomaly(
                task_id, label=f"⚠️ Anomalie {dressing_label} : session probablement perdue",
                dressing=dressing_label,
                reason=f"{len(articles)} article(s) scrapés mais 0 vue partout — signal typique "
                       f"d'une session Vinted déconnectée pendant le scraping. Données EXCLUES "
                       f"de la synchronisation Supabase ce run-ci pour ne pas écraser les vraies valeurs."
            )
            return True

        update_task_result(task_id, dressing_id, status="success")
        return False

    async def run_full_sync(self):
        from services.session_manager import ensure_session
        from services.automation_scheduler import start_task_run, update_task_result, finish_task_run

        start_time = datetime.now()
        logger.info("🎬 Démarrage de la synchronisation multi-comptes...")
        app_state["status_TEST"] = "Vérification sessions..."
        app_state["has_error"] = False

        dressings_items = [
            {"id": "dressing1", "nom": "Dressing 1", "dressing": "Dressing 1"},
            {"id": "dressing2", "nom": "Dressing 2", "dressing": "Dressing 2"},
        ]
        task_id = start_task_run("scraping", dressings_items)

        # --- On teste les DEUX sessions avant de décider d'abandonner ---
        session_ok_d1 = await ensure_session("chrome_clemz")
        app_state["session_status"]["dressing1"] = session_ok_d1

        session_ok_d2 = await ensure_session("edge_clemz")
        app_state["session_status"]["dressing2"] = session_ok_d2

        # Pause délibérée avant de rouvrir un navigateur sur ces mêmes profils --
        # ensure_session() vient de FERMER son propre contexte sur clemz_session_chrome_d1_reel/
        # edge quelques lignes plus haut ; scrap_vinted() va en rouvrir un tout de
        # suite après. Cet enchaînement rapproché (fermeture puis réouverture quasi
        # immédiate du même profil) semble déclencher la page /session-refresh de
        # Vinted (probable mesure anti-fraude sur les ouvertures de session
        # rapprochées) -- constaté le 26/08/2026, bloquant le scraping.
        await asyncio.sleep(5)

        if not session_ok_d1 or not session_ok_d2:
            comptes_ko = []
            if not session_ok_d1:
                comptes_ko.append("Dressing 1")
                update_task_result(task_id, "dressing1", status="failed", reason="Session Vinted invalide/expirée")
            else:
                update_task_result(task_id, "dressing1", status="success")
            if not session_ok_d2:
                comptes_ko.append("Dressing 2")
                update_task_result(task_id, "dressing2", status="failed", reason="Session Vinted invalide/expirée")
            else:
                update_task_result(task_id, "dressing2", status="success")

            if len(comptes_ko) == 2:
                message = "Erreur : AUCUNE connexion Vinted détectée (D1 et D2)"
            else:
                message = f"Erreur : session expirée ({comptes_ko[0]})"

            logger.error(f"❌ Session(s) invalide(s) — scraping annulé : {', '.join(comptes_ko)}")
            app_state["status_TEST"] = message
            app_state["has_error"] = True
            self.update_cron_status(start_time, False)
            finish_task_run(task_id)
            return

        app_state["status_TEST"] = "Scrapping en cours"

        # --- DRESSING 1 (migré 08/09/2026 : vrai Chrome + Clemz préchargée
        # manuellement dans le profil clemz_session_chrome_d1_reel -- même
        # traitement que Dressing 2, coïncide avec le changement de compte
        # Vinted. Voir session_manager.ACCOUNTS["chrome_clemz"].) ---
        articles_d1 = await self.scrap_vinted(
            browser_type="chromium",
            session_path="clemz_session_chrome_d1_reel",
            member_id="287248160",
            dressing_name="Dressing 1",
            channel="chrome",
        )
        anomalie_d1 = self._verifier_anomalie_session_perdue(task_id, "dressing1", "Dressing 1", articles_d1)

        # --- DRESSING 2 (migré 05/09/2026 : vrai Chrome + Clemz préchargée
        # manuellement dans le profil clemz_session_chrome_reel -- corrige
        # l'empreinte TLS/JA3-JA4 du Chromium embarqué. Voir
        # session_manager.ACCOUNTS["edge_clemz"] pour le détail.) ---
        articles_d2 = await self.scrap_vinted(
            browser_type="chromium",
            session_path="clemz_session_chrome_reel",
            member_id="3136979514",
            dressing_name="Dressing 2",
            channel="chrome",
        )
        anomalie_d2 = self._verifier_anomalie_session_perdue(task_id, "dressing2", "Dressing 2", articles_d2)

        # Les dressings anomaliques sont exclus de la synchro -- ne pas écraser les
        # vraies vues/favoris déjà en base avec des 0 partout.
        tous_les_articles = []
        if not anomalie_d1:
            tous_les_articles += articles_d1
        if not anomalie_d2:
            tous_les_articles += articles_d2

        if not tous_les_articles:
            logger.error("❌ Aucun article valide à synchroniser (anomalie sur les deux dressings, ou aucun article).")
            self.update_cron_status(start_time, False)
            app_state["status_TEST"] = "Erreur : Aucun article valide"
            app_state["has_error"] = True
            finish_task_run(task_id)
            return

        logger.info(f"✅ Total : {len(tous_les_articles)} articles à synchroniser vers Supabase.")
        app_state["status_TEST"] = "Synchronisation Supabase"

        dressings_scrapes_avec_succes = []
        if not anomalie_d1:
            dressings_scrapes_avec_succes.append("Dressing 1")
        if not anomalie_d2:
            dressings_scrapes_avec_succes.append("Dressing 2")

        created, updated, par_dressing = await self.sync_to_supabase(tous_les_articles, dressings_scrapes_avec_succes)

        # Enrichit le résultat de chaque dressing (déjà marqué success/failed par
        # _verifier_anomalie_session_perdue) avec le détail scrapé/synchronisé,
        # affiché dans le détail dépliable de TaskHistory. Les dressings exclus
        # pour anomalie gardent leur raison d'échec d'origine, on n'y touche pas.
        if not anomalie_d1:
            d1_counts = par_dressing.get("Dressing 1", {"created": 0, "updated": 0})
            update_task_result(
                task_id, "dressing1", status="success",
                reason=f"{len(articles_d1)} scrapé(s) · {d1_counts['updated']} mis à jour · {d1_counts['created']} nouveau(x)"
            )
        if not anomalie_d2:
            d2_counts = par_dressing.get("Dressing 2", {"created": 0, "updated": 0})
            update_task_result(
                task_id, "dressing2", status="success",
                reason=f"{len(articles_d2)} scrapé(s) · {d2_counts['updated']} mis à jour · {d2_counts['created']} nouveau(x)"
            )

        # Succès global si au moins un dressing s'est bien passé -- même en cas
        # d'anomalie sur l'autre, on ne veut pas perdre la synchro de celui qui va bien.
        self.update_cron_status(start_time, True, created, updated)
        app_state["status_TEST"] = "Synchronisation Réussie" if not (anomalie_d1 or anomalie_d2) else "Synchronisation partielle (anomalie détectée)"
        app_state["has_error"] = anomalie_d1 or anomalie_d2
        logger.info("🏁 Processus complet terminé.")
        finish_task_run(task_id)

    async def scrap_vinted(self, browser_type="chromium", session_path="vinted_session", member_id="", dressing_name="", channel=None):
        """Fonction de scrap universelle adaptable à Chrome ou Firefox."""
        async with async_playwright() as p:
            browser_engine = p.chromium if browser_type == "chromium" else p.firefox

            logger.info(f"🚀 Lancement de {browser_type} pour le profil : {session_path}")

            # Utilisation d'un contexte persistant pour mémoriser les sessions de chaque compte
            # (même dossier que clemz_automation.py, ancré sur l'emplacement du fichier et non le CWD)
            #
            # Mode visible piloté par le toggle Maintenance ("Navigateur visible
            # (mode test)") -- câblé UNIQUEMENT sur le scraping, pas sur les
            # automatisations Clemz (clemz_partage.py reste headless=False en dur,
            # volontairement laissé tel quel).
            from services.maintenance_service import maintenance_service
            mode_visible = maintenance_service.get_clemz_visible_mode().get("visible", False)

            launch_kwargs = {
                "executable_path": None,
                "headless": not mode_visible,
                "viewport": {"width": 1280, "height": 900},
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            }
            if channel:
                # Profil migré vers un vrai navigateur avec Clemz préchargée
                # manuellement dans le profil (cf. session_manager.ACCOUNTS) --
                # neutralise le --disable-extensions par défaut de Playwright,
                # qui désactiverait sinon l'extension déjà présente.
                launch_kwargs["channel"] = channel
                launch_kwargs["ignore_default_args"] = [
                    "--disable-extensions",
                    "--disable-component-extensions-with-background-pages",
                ]

            context = await browser_engine.launch_persistent_context(
                os.path.join(_BASE, session_path),
                **launch_kwargs,
            )
            page = context.pages[0]
            scraped_articles = []
            
            try:
                # Navigation vers l'URL du membre spécifié
                url = f"https://www.vinted.fr/member/{member_id}"
                await page.goto(url, wait_until="domcontentloaded")

                # Vinted peut rediriger transitoirement vers /session-refresh (rotation
                # du token de session, surtout juste après la fermeture/réouverture
                # rapprochée d'un contexte sur le même profil -- cf. ensure_session()
                # qui ferme son propre contexte juste avant que scrap_vinted() en
                # rouvre un nouveau) avant de revenir sur la vraie page. Sans attendre
                # cette redirection, on reste bloqué sur la page intermédiaire et
                # grid-item n'apparaît jamais -- constaté le 26/08/2026.
                if "session-refresh" in page.url:
                    logger.info(f"🔄 [{dressing_name}] Redirection session-refresh détectée, attente de la page finale...")
                    try:
                        await page.wait_for_url(f"**/member/{member_id}**", timeout=30000)
                    except Exception:
                        logger.warning(f"⚠️ [{dressing_name}] Toujours sur session-refresh après 30s : {page.url} — nouvelle tentative de navigation directe.")
                        # Fallback : le token a peut-être déjà été renouvelé côté serveur
                        # sans que le client n'ait redirigé -- une navigation directe
                        # peut suffire à repartir sur la bonne page.
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                            if "session-refresh" in page.url:
                                logger.error(f"❌ [{dressing_name}] Toujours bloqué sur session-refresh après nouvelle tentative : {page.url}")
                        except Exception as retry_err:
                            logger.error(f"❌ [{dressing_name}] Échec de la nouvelle tentative de navigation : {retry_err}")

                # Attente et Scroll (Utilise votre logique scroll_progressif existante)
                try:
                    await page.wait_for_selector('[data-testid="grid-item"]', timeout=15000)
                except Exception:
                    # --- DIAGNOSTIC : on capture l'état de la page pour comprendre le blocage ---
                    debug_dir = os.path.join(_BASE, "logs", "scrap_debug")
                    os.makedirs(debug_dir, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    screenshot_path = os.path.join(debug_dir, f"{dressing_name}_{ts}.png")
                    html_path = os.path.join(debug_dir, f"{dressing_name}_{ts}.html")
                    try:
                        await page.screenshot(path=screenshot_path, full_page=True)
                        html_content = await page.content()
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        logger.error(
                            f"❌ [{dressing_name}] [data-testid='grid-item'] introuvable — "
                            f"URL actuelle : {page.url} — capture : {screenshot_path}"
                        )
                    except Exception as debug_err:
                        logger.error(f"❌ [{dressing_name}] Échec capture diagnostic : {debug_err}")
                    raise
                
                # --- ÉTAPE CRUCIALE : On charge tout le dressing ---
                await self.scroll_progressif(page)

                # NOUVEAU (11/08/2026) : Vinted a migré vers des CSS Modules hashés
                # (ex: "Grid-module-scss-module__HmDNda__feed-grid__item"), le sélecteur
                # de classe .feed-grid__item ne matche donc plus rien. On utilise
                # désormais l'attribut data-testid, stable et non-hashé.
                items = await page.query_selector_all('[data-testid="grid-item"]')
                logger.info(f"📦 Total articles détectés : {len(items)}")

                for i, item in enumerate(items):
                    # Titre : toujours porté par l'attribut title de l'overlay-link --
                    # lu en premier pour pouvoir l'utiliser aussi bien dans le suivi
                    # des brouillons ignorés (self.brouillons_ignores) que pour
                    # l'article final s'il n'est pas filtré.
                    title_el = await item.query_selector('a[title]')
                    raw_title = await title_el.get_attribute("title") if title_el else ""
                    titre = raw_title.split(',')[0].strip()

                    # 1. Filtre produits indisponibles (Vendu / Réservé) et brouillons
                    # Nouveau marqueur confirmé (11/08/2026) : data-testid se terminant
                    # par "--status-text", contenant le texte "Vendu" (ou "Réservé").
                    # Les brouillons portent le même type de bandeau (gris, texte
                    # "Brouillon", confirmé sur data-testid$="--status-text" le
                    # 10/09/2026) -- on les exclut ici pour ne jamais les traiter
                    # comme des annonces publiées, et on les mémorise à part pour
                    # permettre un nettoyage ciblé côté Supabase.
                    status_el = await item.query_selector('[data-testid$="--status-text"]')
                    if status_el:
                        status_text = (await status_el.inner_text()).strip().lower()
                        if status_text == "brouillon":
                            self.brouillons_ignores.append({"dressing": dressing_name, "nom": titre})
                            logger.info(f"⏭️ [{dressing_name}] Article ignoré (brouillon) : {titre}")
                            continue
                        if status_text in ("vendu", "réservé", "reserve"):
                            logger.info(f"⏭️ [{dressing_name}] Article ignoré (statut: {status_text})")
                            continue

                    # Date du jour pour marquer le passage du scraper
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    logger.info(f"🔍 [{dressing_name}] Article : {titre}")

                    # 3. Prix vendeur via data-testid stable
                    prix_el = await item.query_selector('[data-testid$="--price-text"]')
                    raw_prix = await prix_el.inner_text() if prix_el else "0"
                    prix = float(re.search(r'\d+[.,]?\d*', raw_prix).group().replace(',', '.')) if re.search(r'\d+', raw_prix) else 0.0

                    # 4-5. Vues + Favoris : en vue propriétaire connecté, Vinted affiche
                    # ces deux infos sous forme de texte brut ("29 vues" / "19 favoris")
                    # dans les blocs --description-title / --description-subtitle. On se
                    # base sur le mot-clé plutôt que sur l'ordre des deux blocs, au cas
                    # où Vinted les permute.
                    vues = 0
                    favoris = 0
                    desc_title_el = await item.query_selector('[data-testid$="--description-title"]')
                    desc_subtitle_el = await item.query_selector('[data-testid$="--description-subtitle"]')

                    for desc_el in (desc_title_el, desc_subtitle_el):
                        if not desc_el:
                            continue
                        raw_text = await desc_el.inner_text()
                        match = re.search(r'(\d+)', raw_text)
                        if not match:
                            continue
                        number = int(match.group(1))
                        if 'vue' in raw_text.lower():
                            vues = number
                        elif 'favori' in raw_text.lower():
                            favoris = number

                    logger.info(f"📊 [{dressing_name}] Vues : {vues} | Favoris : {favoris}")


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

                return scraped_articles
            except Exception as e:
                logger.error(f"❌ Erreur lors du scrap {dressing_name} : {e}")
                return []
            finally:
                if 'context' in locals():
                    try:
                        await context.close()
                    except Exception:
                        pass
                    
            
    async def sync_to_supabase(self, scraped_articles, dressings_scrapes_avec_succes=None):
        """
        Synchronise les articles scrapés vers Supabase.
        - Upsert sur les articles existants (mise à jour prix/vues/favoris/rang)
        - Insert pour les nouveaux articles
        - Désynchronisation des articles absents du dressing (est_vendu = True)

        dressings_scrapes_avec_succes : liste des noms de dressing réellement
        scrapés avec succès CE run (ex: ["Dressing 1", "Dressing 2"], ou un seul
        si l'autre a été exclu pour anomalie). CRITIQUE pour l'étape C : sans ça,
        un dressing en anomalie (donc absent de scraped_articles) ferait croire
        que TOUS ses articles ont disparu, et les marquerait vendus en masse --
        bug constaté le 26/08/2026, source de la désynchronisation qui a caché
        la quasi-totalité de l'inventaire du dashboard.

        Retourne (count_created, count_updated, par_dressing) où par_dressing est un
        dict {nom_dressing: {"created": int, "updated": int}} -- permet d'afficher le
        détail par dressing (scrapé/synchronisé) dans TaskHistory, en plus des totaux
        globaux déjà utilisés par update_cron_status.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        count_created = 0
        count_updated = 0
        count_desync  = 0
        par_dressing = {}
        dressings_scrapes_avec_succes = dressings_scrapes_avec_succes or []

        try:
            # --- ÉTAPE A : Récupération de l'inventaire Supabase actuel ---
            existing_response = self.db.table("articles") \
                .select("id, nom, est_vendu, erreur_clemz, dressing") \
                .execute()

            # Dictionnaire nom_lower → {id, est_vendu, erreur_clemz, dressing} pour lookup rapide
            supabase_inventory = {
                row["nom"].lower(): {
                    "id": row["id"],
                    "est_vendu": row["est_vendu"],
                    "erreur_clemz": row.get("erreur_clemz", False),
                    "dressing": row.get("dressing"),
                }
                for row in existing_response.data
            }

            scraped_names_lower = [a["nom"].lower() for a in scraped_articles]

            # --- ÉTAPE B : Upsert des articles scrapés ---
            for art in scraped_articles:
                nom_lower = art["nom"].lower()
                existing  = supabase_inventory.get(nom_lower)

                if existing:
                    # Article déjà connu → mise à jour des stats. est_vendu: False
                    # -- réinitialisation volontaire : retrouver l'article dans un
                    # scrape réussi est la preuve définitive qu'il est toujours en
                    # ligne, même s'il avait été faussement marqué vendu par un bug
                    # de désync sur un run précédent (auto-guérison, cf. docstring
                    # de sync_to_supabase).
                    self.db.table("articles").update({
                        "prix_vente":  art["prix"],
                        "vues":        art["vues"],
                        "favoris":     art["favoris"],
                        "rang":        art["position_dressing"],
                        "url_image":   art.get("url_image"),
                        "dressing":    art["dressing"],
                        "est_vendu":   False,
                        "updated_at":  datetime.now().isoformat(),
                    }).eq("id", existing["id"]).execute()

                    count_updated += 1
                    par_dressing.setdefault(art["dressing"], {"created": 0, "updated": 0})
                    par_dressing[art["dressing"]]["updated"] += 1
                    logger.info(f"🔄 MAJ : {art['nom']} | {art['prix']}€ | "
                                f"Vues: {art['vues']} | Fav: {art['favoris']}")

                    # Auto-résolution : l'article réapparaît dans le dressing (nouvelle
                    # annonce recréée manuellement, ou situation débloquée) -- on efface
                    # le drapeau d'erreur Clemz, plus la peine de le signaler.
                    if existing.get("erreur_clemz"):
                        self.db.table("articles").update({
                            "erreur_clemz": False,
                            "erreur_clemz_reason": None,
                            "erreur_clemz_date": None,
                        }).eq("id", existing["id"]).execute()
                        logger.info(f"✅ '{art['nom']}' réapparu dans le dressing — drapeau erreur Clemz effacé.")
                else:
                    # Nouvel article → insertion complète
                    self.db.table("articles").insert({
                        "nom":               art["nom"],
                        "prix_vente":        art["prix"],
                        "vues":              art["vues"],
                        "favoris":           art["favoris"],
                        "rang":              art["position_dressing"],
                        "url_image":         art.get("url_image"),
                        "dressing":          art["dressing"],
                        "date_publication":  today_str,
                        "est_vendu":         False,
                        "est_traite":        False,
                    }).execute()

                    count_created += 1
                    par_dressing.setdefault(art["dressing"], {"created": 0, "updated": 0})
                    par_dressing[art["dressing"]]["created"] += 1
                    logger.info(f"✨ NEW : {art['nom']} | {art['prix']}€")

            # --- ÉTAPE C : Désynchronisation des absents ---
            # Un article présent dans Supabase mais absent du scrap = vendu ou retiré
            logger.info("🧹 Vérification des articles absents du dressing...")

            for nom_lower, data in supabase_inventory.items():
                if data["dressing"] not in dressings_scrapes_avec_succes:
                    # Ce dressing n'a pas été scrapé avec succès ce run (anomalie ou
                    # skip) -- on ne peut rien conclure sur la présence/absence réelle
                    # de ses articles, donc on ne touche à rien pour lui cette fois.
                    continue
                if nom_lower not in scraped_names_lower and not data["est_vendu"]:
                    if data.get("erreur_clemz"):
                        # Absent du dressing car son ancienne annonce a été supprimée
                        # suite à une erreur Clemz jamais résolue -- PAS une vraie vente.
                        # On ne touche pas est_vendu, pour ne pas perdre le suivi de cet
                        # article tant que l'erreur n'est pas réglée manuellement.
                        logger.warning(f"🛑 Absent du dressing mais en erreur Clemz (non résolue) → non marqué vendu : {nom_lower}")
                        continue

                    self.db.table("articles").update({
                        "est_vendu":  True,
                        "nb_republications_sans_vente": 0,
                        "updated_at": datetime.now().isoformat(),
                    }).eq("id", data["id"]).execute()

                    count_desync += 1
                    logger.info(f"🗑️ Absent du dressing → marqué vendu : {nom_lower}")


            logger.info(
                f"🏁 Sync terminée — "
                f"Créés: {count_created} | MAJ: {count_updated} | Vendus: {count_desync}"
            )
            return count_created, count_updated, par_dressing

        except Exception as e:
            app_state["status_TEST"] = "Erreur Système"
            logger.error(f"❌ Erreur sync Supabase : {str(e)}")
            self.write_log("Erreur sync_to_supabase", details=str(e))
            traceback.print_exc()
            return 0, 0, {}

    async def scroll_progressif(self, page):
        """Réplique la logique de scrollProgressif de content.js."""
        logger.info("🖱️ Début du scroll progressif...")
        last_count = 0
        consecutive_same_count = 0
        iteration = 0
        # NOUVEAU : tolérance augmentée (3 vérifications au lieu de 2, 3s au lieu
        # de 2s) — évite d'arrêter le scroll trop tôt si le lazy-loading Vinted
        # met un peu plus de temps à répondre (notamment en headless).
        MAX_CONSECUTIVE_SAME = 3
        SLEEP_BETWEEN_SCROLLS = 3
        
        while True:
            iteration += 1
            current_items = await page.query_selector_all('[data-testid="grid-item"]')
            current_count = len(current_items)

            scroll_info = await page.evaluate("""() => ({
                scrollY: window.scrollY,
                scrollHeight: document.body.scrollHeight,
                innerHeight: window.innerHeight
            })""")
            logger.info(
                f"🔍 [DIAG scroll #{iteration}] {current_count} articles détectés — "
                f"scrollY={scroll_info['scrollY']} / scrollHeight={scroll_info['scrollHeight']} "
                f"/ viewport={scroll_info['innerHeight']} / stable_depuis={consecutive_same_count}"
            )

            # Scroll ciblé sur le sentinel "infinite-scroll" (IntersectionObserver
            # côté Vinted) plutôt qu'un scrollTo global — plus fiable pour déclencher
            # le chargement de la page suivante que de viser document.body.scrollHeight.
            scrolled_to_sentinel = await page.evaluate("""() => {
                const sentinel = document.querySelector('[data-testid="infinite-scroll"]');
                if (sentinel) {
                    sentinel.scrollIntoView({ behavior: 'instant', block: 'center' });
                    return true;
                }
                window.scrollTo(0, document.body.scrollHeight);
                return false;
            }""")
            if iteration == 1:
                logger.info(f"🔍 [DIAG scroll] Sentinel 'infinite-scroll' trouvé : {scrolled_to_sentinel}")
            await asyncio.sleep(SLEEP_BETWEEN_SCROLLS)
            
            if current_count == last_count:
                consecutive_same_count += 1
                if consecutive_same_count >= MAX_CONSECUTIVE_SAME:
                    logger.info(f"🛑 [DIAG scroll] Arrêt après {iteration} itérations — compte stable à {current_count}.")
                    break
            else:
                consecutive_same_count = 0
                logger.info(f"⏳ Chargement... ({current_count} articles détectés)")
            last_count = current_count
        logger.info(f"✅ Chargement terminé : {last_count} articles au total.")
