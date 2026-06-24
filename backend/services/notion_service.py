import httpx
import math
import unicodedata
from datetime import datetime, timedelta
from babel.dates import format_date
import json
import os
from database import NOTION_TOKEN,SALES_DB_ID, DATABASE_ID, FILTERS_DB_ID, PURCHASE_DB_ID, GOALS_DB_ID

class NotionService:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }

        self.timeout = httpx.Timeout(10.0, read=120.0) # 30s pour se connecter, 60s pour lire
        self.client = httpx.AsyncClient(timeout=self.timeout)

        # --- NOUVEAUX PARAMÈTRES DE STRATÉGIE ---
        self.DELAI_LIQUIDATION = 45
        self.DELAI_SORTIE_FINALE = 90          # Jours max avant sacrifice du prix
        self.SCORE_PERF_EXCELLENTE = 15.0    # Seuil du "Cycle Réussi"
        self.COEFF_PLANCHER_STANDARD = 1.6   # Ton multiplicateur actuel
        self.COEFF_PLANCHER_PETIT_PRIX = 2.0 # Pour protéger la marge sur achats < 5€

        # Paramètres de calcul internes
        self.CLEMZ_BOOST_VUES_MAX = 30 # Plafond total vues
        self.CLEMZ_BOOST_FAVORIS_FIXE = 25     # Plafond total favoris
        self.RANG_MAX_BOOST_VUES = 40     # Concerne les 40 premiers
        self.RANG_MAX_BOOST_FAVORIS = 50  # Concerne les 50 premiers

        # Paramètres Catégorie PÉPITE
        self.SEUIL_PEPITE = 15.0
        self.MIN_V_REEL_PEPITE = 25   
        self.DELAI_MIN_PEPITE = 4

        # Paramètres Catégorie MAUVAISE PERFORMANCE
        self.SEUIL_LOW_PERF = 5.0
        self.DELAI_MIN_LOW_PERF = 3
        self.DELAI_MIN_LOW_PERF_HIGH = 7
        self.DELAI_FORCE_REPUBLISH = 10

        # Paramètres d'Invisibilité (Shadow Ban)
        self.SEUIL_INVISIBLE_LOW = 2   # Moins de 2 vues pour les petits prix
        self.SEUIL_INVISIBLE_HIGH = 10 # Moins de 10 vues pour le High Ticket

        # Seuils de prix
        self.LIMITE_LOW_TICKET = 50.0 
        self.DELAI_LOW_TICKET = 15
        self.DELAI_HIGH_TICKET = 21

        # --- AJOUT PARAMÈTRE STATUT QUO ---
        self.MIN_V_REEL_STATUT_QUO = 50
    
    def strip_accents(self, s):
        return ''.join(c for c in unicodedata.normalize('NFD', s)
                  if unicodedata.category(c) != 'Mn')
    
    async def get_processed_inventory(self):
        # 1. Fetch data
        raw_inventory_data = await self._fetch_notion_data()
        # 2. On récupère d'abord tous les prix d'achat
        purchase_map = await self._get_purchase_prices()
        
        articles = []
        today = datetime.now()

        for item in raw_inventory_data:
            props = item["properties"]

            # Récupération du nom de l'article pour faire le lien
            item_name = item["properties"].get("Nom", {}).get("title", [{}])[0].get("plain_text", "")

            # JOINTURE : on cherche le prix d'achat correspondant
            # .get(item_name, 0.0) renvoie 0.0 si le nom n'existe pas dans la base achat
            prix_achat = purchase_map.get(item_name, 0.00)
            prix_vente = item["properties"].get("Prix", {}).get("number", 0.0)

            # --- CALCUL DU PLANCHER DYNAMIQUE ---
            coeff = self.COEFF_PLANCHER_PETIT_PRIX if 0 < prix_achat < 5.0 else self.COEFF_PLANCHER_STANDARD
            prix_plancher = round(prix_achat * coeff, 2) if prix_achat > 0 else 0
            prix_optimiste = round(prix_achat * 2.6, 2) if prix_achat > 0 else 0
            # Marge réelle restante avant de perdre de l'argent
            marge_dispo = prix_vente - prix_plancher

            # --- Extraction sécurisée de la Date de Publication ---
            prop_pub = props.get("Date de publication", {}).get("date")
            date_pub = prop_pub.get("start") if prop_pub else None

            # --- Extraction sécurisée de la Date de Republication ---
            prop_repub = props.get("Date de republication", {}).get("date")
            date_repub = prop_repub.get("start") if prop_repub else None

            #print(f"📊 Date_republication : {date_repub}")


            # --- Logique is_new ---
            # Si la propriété 'date' est absente ou si 'start' est vide
            is_new = date_repub is None 

            # --- Conversion en objets Python (Maths) ---
            # On utilise 'today' comme filet de sécurité si la date est manquante
            date_pub_obj = datetime.strptime(date_pub, "%Y-%m-%d") if date_pub else today
            date_repub_obj = datetime.strptime(date_repub, "%Y-%m-%d") if date_repub else today

            if date_repub:
                date_repub_obj = datetime.strptime(date_repub, "%Y-%m-%d")
                jours_en_ligne = max(1, (today - date_repub_obj).days)
            else:
                # Si jamais republié, jours_en_ligne = jours_en_vente
                # OU tu peux mettre 0 si tu préfères que ça affiche 0j
                date_repub_obj = date_pub_obj
                jours_en_ligne = (today - date_pub_obj).days

            # Calcul de la "vie" du produit (jours_en_vente)
            jours_en_vente = max(1, (today - date_pub_obj).days)

            
            # Calcul de l'ancienneté (jours_en_ligne)
            jours_en_ligne = max(1, (today - date_repub_obj).days)

            # On extrait la valeur de la clé 'url'
            photo_url = item["properties"].get("url_image", {}).get("url", "")

            # Calcul du profit réel
            #profit_reel = max(0, prix_vente - prix_achat)

            # 2. Extraction de l'image (si c'est un champ URL ou Files)
            # On vérifie d'abord si c'est un champ 'Files' ou une simple 'URL'
            #photo_url = ""

            rang = props.get("Rang", {}).get("number", 100) # 100 par défaut si vide
            # On garde ta logique de calcul de score ici
            v_tot = props.get("Vues", {}).get("number", 0) or 0
            f_tot = props.get("Favoris", {}).get("number", 0) or 0

            #print(f"📊 Données brute notions vues : {item_name} | Vues : {item.get("properties", {}).get("Vues", {}).get("number", 0)} | Favoris : {f_tot} ")
            #print(f"📊 Données brute notions : {item_name} | Jours: {jours_en_ligne} | Vues : {v_tot} | Favoris : {f_tot} "
            v_reel = max(1, v_tot - self.CLEMZ_BOOST_VUES_MAX)
            f_reel = max(0, f_tot - self.CLEMZ_BOOST_FAVORIS_FIXE)

            
            # Calcul du score d'attirance organique
            score = round(min(100.0, (f_reel / v_reel) * 100), 2) if v_reel >= 5 else 0.0

            # --- CHOIX DU DÉLAI ET DU SEUIL SELON LE PRIX (Low vs High Ticket) ---
            if prix_vente < self.LIMITE_LOW_TICKET:
                delai_observation = self.DELAI_MIN_LOW_PERF # 3 jours
                seuil_invisible = self.SEUIL_INVISIBLE_LOW  # 2 vues
                delai_max = self.DELAI_LOW_TICKET
            else:
                delai_observation = self.DELAI_MIN_LOW_PERF_HIGH # 7 jours
                seuil_invisible = self.SEUIL_INVISIBLE_HIGH # 10 vues
                delai_max = self.DELAI_HIGH_TICKET

            # 1. Détection PÉPITE (Ancien "Bloqué")
            is_pepite = (prix_vente > 10.0) and (score >= self.SEUIL_PEPITE) and \
                         (v_reel >= self.MIN_V_REEL_PEPITE) and \
                         (jours_en_ligne >= self.DELAI_MIN_PEPITE)
            
            # 4. Détection STATUT QUO (Tiède avec beaucoup de vues)
            # Score entre 5% et 15% avec plus de 50 vues réelles
            is_statut_quo = (prix_vente > 10.0) and (score >= self.SEUIL_LOW_PERF) and \
                            (score < self.SEUIL_PEPITE) and \
                            (v_reel >= self.MIN_V_REEL_STATUT_QUO)
            
            
            # Debug console pour vérifier la précision
            #print(f"📊 {item_name} | Jours: {jours_en_ligne} | Vues Réelles: {v_reel} | Favoris Réelles: {f_reel} | Score: {score}%")

            # 2. Détection INVISIBLE (Shadow Ban) avec seuil dynamique
            is_invisible = (jours_en_ligne >= delai_observation) and (v_reel < seuil_invisible)
            
            # 3. Détection MAUVAISE PERFORMANCE
            is_low_perf = (prix_vente > 10.0) and (jours_en_ligne >= delai_observation) and \
                          (score < self.SEUIL_LOW_PERF) and (not is_invisible)

            is_very_old = jours_en_vente >= self.DELAI_SORTIE_FINALE
            # 1. On définit la liquidation d'abord
            is_liquidation = jours_en_vente >= self.DELAI_LIQUIDATION
            needs_time_republish = jours_en_ligne >= delai_max

            # Force la republication après 10j de stagnation SEULEMENT pour les petits prix (Protège le High Ticket)
            should_force_republish = (is_low_perf and jours_en_ligne >= self.DELAI_FORCE_REPUBLISH and prix_vente < self.LIMITE_LOW_TICKET)

            # --- DIAGNOSTIC AUTOMATIQUE ---
            diagnostic_cycle = ""
            if jours_en_ligne >= delai_max:
                if score >= self.SCORE_PERF_EXCELLENTE:
                    diagnostic_cycle = "🔥 CYCLE RÉUSSI (Garder prix)"
                elif score >= self.SEUIL_LOW_PERF:
                    diagnostic_cycle = "🌤️ CYCLE TIÈDE (Baisse légère)"
                else:
                    diagnostic_cycle = "⚠️ CYCLE ÉCHOUÉ (Baisse + Photos)"
            else:
                diagnostic_cycle = f"⏳ Bilan dans {delai_max - jours_en_ligne}j"

            # Initialisation des indicateurs pour le Front-end
            display_pepite = False
            display_low_perf = False
            is_critical = False
            display_statut_quo = False

            # 1. On sécurise la récupération (au cas où la case soit vide dans Notion)
            dressing_prop = props.get("Dressing", {}).get("select")

            # 2. On extrait le nom, ou on met une valeur par défaut si c'est vide
            dressing_value = dressing_prop.get("name") if dressing_prop else "Inconnu"

            

            # 1. SHADOW BAN (Priorité absolue)
            if is_invisible:
                action_label = "♻️ REPUBLIER (Shadow Ban)"
                is_critical = True
            
            # 🟢 2. SORTIE FINALE (Priorité sur tout le reste après 90j)
            elif is_very_old:
                action_label = f"🚨 SORTIE : Prix Achat ({prix_achat}€)"
                # On force is_critical car c'est une urgence de libérer le capital
                is_critical = True

            # 3. FIN DE CYCLE (Republication stratégique)    
            elif needs_time_republish or should_force_republish:
                is_critical = True

                if is_pepite:
                    taux = 0.90 if jours_en_ligne > 7 else 0.95
                    prix_suggere = max(prix_plancher, round(prix_vente * taux, 2))
                    action_label = f"💎 PÉPITE : Baisse -{int((1-taux)*100)}% ({prix_suggere}€)"
                    display_pepite = True

                # 🟢 Sous-condition : Est-ce une Liquidation en fin de cycle ?
                elif is_liquidation:
                    action_label = f"♻️ REPUBLIER (Liquidation {prix_vente}€)"

                elif is_statut_quo:
                    action_label = "⚖️ REPUBLIER (Statut Quo : Revoir Photos)"
                    display_statut_quo = True

                if score < self.SEUIL_LOW_PERF:
                    prix_suggere = max(prix_plancher, round(prix_vente * 0.90, 2))
                    action_label = f"♻️ REPUBLIER avec BAISSE ({prix_suggere}€)"
                else:
                    action_label = "♻️ REPUBLIER (Même prix)"

            # 🟢 LOGIQUE DE LIQUIDATION AMÉLIORÉE
            elif is_liquidation:
                prix_cible_liq = max(prix_plancher, round(prix_vente * 0.70, 2))
                
                # On vérifie si on a déjà appliqué la baisse de 30% ou si on est au plancher
                if prix_vente <= prix_cible_liq:
                    action_label = f"💀 LIQUIDATION : Poste OK ({prix_vente}€)"
                else:
                    action_label = f"💀 LIQUIDATION : Prix Plancher ({prix_cible_liq}€)"
            
            # 🟤 PRIORITÉ 5 : ANALYSE DES PERFORMANCES (En cours de cycle)
            elif is_pepite:
                taux = 0.90 if jours_en_ligne > 7 else 0.95
                prix_suggere = max(prix_plancher, round(prix_vente * taux, 2))
                action_label = f"💎 PÉPITE : Baisse -{int((1-taux)*100)}% ({prix_suggere}€)"
                display_pepite = True

            # 5. MAUVAISE PERFORMANCE
            elif is_low_perf:
                display_low_perf = True
                if prix_vente < 10.0 or is_new:
                    prix_suggere = max(prix_plancher, round(prix_vente * 0.90, 2))
                    action_label = f"📉 MAUVAISE PERF : Baisse -10% ({prix_suggere}€)"
                else:
                    if marge_dispo > 3.0:
                        prix_suggere = max(prix_plancher, round(prix_vente * 0.80, 2))
                        action_label = f"🚨 MAUVAISE PERF : Baisse -20% ({prix_suggere}€)"
                    else:
                        action_label = "📸 MAUVAISE PERF : Changer Photos (Plancher atteint)"
            
            elif is_statut_quo:
                    action_label = "⚖️ STATUT QUO : Analyse Prix/Photos"
                    display_statut_quo = True
            else:
                action_label = "✅ OK"
            
            articles.append({
                "id": item["id"],
                "nom": item_name,
                "rang": rang,
                "prix_achat": prix_achat,
                "prix_vente": prix_vente,
                "prix_plancher": prix_plancher,
                "marge_dispo": round(marge_dispo, 2),
                "prix_optimiste":prix_optimiste,
                "score": score,
                "photo_url": photo_url,
                "action_label": action_label,
                "is_critical": is_critical,
                "is_very_old": is_very_old,
                "jours_en_vente": jours_en_vente,
                "jours_en_ligne": jours_en_ligne,
                "jours_restants": max(0, delai_max - jours_en_ligne),
                "is_stuck": display_pepite, # On l'envoie au front
                "is_low_perf": display_low_perf,
                "is_statut_quo": display_statut_quo,
                "delai_max": delai_max,
                "v_tot": v_tot,
                "f_tot": f_tot,
                "v_reel": v_reel,
                "f_reel": f_reel,
                "is_new":  is_new,
                "is_invisible": is_invisible,
                "needs_time_republish": needs_time_republish,
                "ticket_type": "HIGH" if prix_vente >= self.LIMITE_LOW_TICKET else "LOW",
                "dressing": dressing_value,
                "is_done": item["properties"].get("Traité", {}).get("checkbox", False),
                "is_vendu": item["properties"].get("Vendu", {}).get("checkbox", False),
                "date_de_publication": date_pub,
                "date_de_republication": date_repub
            })

        # Trie la liste par le champ 'nom'. 
        # .lower() assure que le tri ignore la différence entre majuscules et minuscules.
        articles.sort(key=lambda x: x["nom"].lower())
        
        return articles # FastAPI convertit automatiquement cette liste en JSON
    
    # 3. Recupération des filtres personnalisée
    async def fetch_notion_filters(self):
    
        # On filtre uniquement les recherches cochées comme "Actif" dans Notion
        payload = {
            "filter": {
                "property": "Actif",
                "checkbox": {"equals": True}
            }
        }
        
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"https://api.notion.com/v1/databases/{FILTERS_DB_ID}/query",
                headers=self.headers,
                json=payload
            )
            
            if res.status_code != 200: return []

            results = res.json().get("results", [])
            filters = []
            
            for page in results:
                props = page["properties"]
                
                # Extraction propre des données multi-sélect
                marques = [s["name"] for s in props.get("Marques", {}).get("multi_select", [])]
                categories = [s["name"] for s in props.get("Categories", {}).get("multi_select", [])]
                tailles = [s["name"] for s in props.get("Tailles", {}).get("multi_select", [])]
                etats = [s["name"] for s in props.get("Etats", {}).get("multi_select", [])]
                
                filters.append({
                    "id": page["id"],
                    "nom": props.get("Nom", {}).get("title", [{}])[0].get("plain_text", "Sans nom"),
                    "marques": marques,
                    "tailles": tailles,
                    "categories": categories,
                    "etats": etats,
                    "prix_max": props.get("Prix Max", {}).get("number")
                })
                
            return filters
        
    

    async def get_sales_goals(self, goals_db_id):
        today = datetime.now()
        current_month_str = self.strip_accents(format_date(today, "MMMM yyyy", locale='fr_FR')).lower() #
        
        first_day_this_month = today.replace(day=1)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        last_month_str = self.strip_accents(format_date(last_day_prev_month, "MMMM yyyy", locale='fr_FR')).lower() #

        purchase_map = await self._get_purchase_prices() #
        all_sales = await self._fetch_all_pages(SALES_DB_ID) #

        async with httpx.AsyncClient() as client:
            response = await client.post(f"https://api.notion.com/v1/databases/{goals_db_id}/query", headers=self.headers) 
            if response.status_code != 200 : return {"status": "error"}
        
            results = response.json().get("results", [])
            goals_summary = {"current": None, "last": None}

            for goal in results:
                props = goal["properties"]
                goal_id = goal["id"]
                
                # --- CALCUL DU BÉNÉFICE NET MENSUEL ---
                benefice_mensuel = 0.0
                for sale in all_sales:
                    sale_props = sale["properties"]
                    relations = sale_props.get("Objectifs", {}).get("relation", [])
                    
                    # On vérifie si la vente appartient à ce mois (ID de l'Objectif)
                    if any(r["id"] == goal_id for r in relations):
                        s_name = sale_props.get("Nom", {}).get("title", [{}])[0].get("plain_text", "")
                        
                        # Récupération du prix (Format Formule)
                        formula_obj = sale_props.get("Prix", {}).get("formula", {}) #
                        s_vente = float(formula_obj.get("number") or 0.0) #
                        
                        s_achat = float(purchase_map.get(s_name, 0.0)) #
                        
                        if s_achat > 0:
                            benefice_mensuel += (s_vente - s_achat)

                # --- PROGRESSION ET FORMATAGE ---
                objectif_val = props.get("Objectif", {}).get("number") or 0
                ca_realise = props.get("Total_vendu", {}).get("rollup", {}).get("number") or 0 #
                
                progression_pct = int(min(100, (ca_realise / objectif_val) * 100)) if objectif_val > 0 else 0

                nom_notion = props.get("Mois", {}).get("title", [{}])[0].get("plain_text", "") #
                nom_notion_clean = self.strip_accents(nom_notion).lower() #

                goal_item = {
                    "id": goal_id,
                    "mois": nom_notion,
                    "objectif_du_mois": objectif_val,
                    "total_vendu_format": f"{ca_realise:.2f} €".replace(".", ","),
                    "benefice_net": round(benefice_mensuel, 2),
                    "benefice_format": f"{benefice_mensuel:.2f} €".replace(".", ","),
                    "progression_value": progression_pct
                }

                if nom_notion_clean == current_month_str:
                    goals_summary["current"] = goal_item
                elif nom_notion_clean == last_month_str:
                    goals_summary["last"] = goal_item

        return goals_summary
    
    
    async def _fetch_notion_data(self):
        all_results = []
        has_more = True
        next_cursor = None
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while has_more:
                # Ajout du filtre pour exclure les articles vendus
                payload = {
                    "filter": {
                        "property": "Vendu",
                        "checkbox": {"equals": False}
                    }
                }
                if next_cursor:
                    payload["start_cursor"] = next_cursor
                
                res = await client.post(
                    f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
                    headers=self.headers,
                    json=payload
                )
                data = res.json()
                all_results.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                next_cursor = data.get("next_cursor")
        return all_results

    async def republish_by_name_logic(self, item_name: str):
        """Met à jour la date d'un produit suite à une republication."""
        # 1. Recherche de l'ID par le nom
        query_payload = {"filter": {"property": "Nom", "title": {"equals": item_name}}}
        async with httpx.AsyncClient() as client:
            res = await client.post(f"https://api.notion.com/v1/databases/{DATABASE_ID}/query", 
                                    headers=self.headers, json=query_payload)
            results = res.json().get("results", [])
            if not results: return {"status": "error", "message": "Produit non trouvé"}

            # 2. Mise à jour de la date et reset du statut
            page_id = results[0]["id"]
            today_str = datetime.now().strftime("%Y-%m-%d")
            update_data = {
                "properties": {
                    "Date de republication": {"date": {"start": today_str}},
                    "Traité": {"checkbox": False}
                }
            }
            await client.patch(f"https://api.notion.com/v1/pages/{page_id}", 
                               headers=self.headers, json=update_data)
            return {"status": "success", "updated_date": today_str}
    
    async def update_page(self, page_id: str, properties: dict):
        """Met à jour les propriétés d'une page Notion spécifique."""
        async with httpx.AsyncClient() as client:
            res = await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=self.headers,
                json={"properties": properties}
            )
            # Optionnel : log pour debug en cas de souci avec l'API Notion
            if res.status_code != 200:
                print(f"❌ Erreur Notion ({res.status_code}): {res.text}")
            return res.json()
        
    async def create_sourcing_filter(self, data: dict):
        """Enregistre un nouveau filtre de recherche dans Notion."""
        properties = {
            "Nom": {"title": [{"text": {"content": data.get("nom")}}]},
            "Marques": {"multi_select": [{"name": m} for m in data.get("marques", [])]},
            "Categories": {"multi_select": [{"name": c} for c in data.get("categories", [])]},
            "Tailles": {"multi_select": [{"name": t} for t in data.get("tailles", [])]},
            "Etats": {"multi_select": [{"name": e} for e in data.get("etats", [])]},
            "Prix Max": {"number": data.get("prix_max")},
            "Actif": {"checkbox": True}
        }
        async with httpx.AsyncClient() as client:
            await client.post("https://api.notion.com/v1/pages", headers=self.headers, 
                              json={"parent": {"database_id": FILTERS_DB_ID}, "properties": properties})
            return {"status": "success"}

    # --- MÉTHODES PRIVÉES ---
    async def _fetch_all_pages(self, db_id):
        results = []
        cursor = None
        async with httpx.AsyncClient() as client:
            while True:
                payload = {"start_cursor": cursor} if cursor else {}
                res = await client.post(f"https://api.notion.com/v1/databases/{db_id}/query", 
                                        headers=self.headers, json=payload)
                data = res.json()
                results.extend(data.get("results", []))
                if not data.get("has_more"): break
                cursor = data.get("next_cursor")
        return results


    # Recupération des prix d'achats
    async def _get_purchase_prices(self):
        pages = await self._fetch_all_pages(PURCHASE_DB_ID)
        price_map = {}
        for p in pages:
            name_list = p["properties"].get("Nom", {}).get("title", [])
            if name_list:
                name = name_list[0].get("plain_text")
                price = p["properties"].get("Prix Achat", {}).get("number") or 0
                price_map[name] = price
        return price_map
    
    async def clear_scraper_database(self, database_id: str):
        """Archive (supprime) toutes les pages de la base de données Notion."""
        # On utilise la méthode de récupération déjà existante dans votre code
        pages = await self._fetch_all_pages(database_id)
        
        count = 0
        async with httpx.AsyncClient() as client:
            for page in pages:
                try:
                    # Dans l'API Notion, supprimer = archiver
                    await client.patch(
                        f"https://api.notion.com/v1/pages/{page['id']}",
                        headers=self.headers,
                        json={"archived": True}
                    )
                    count += 1
                except Exception as e:
                    print(f"❌ Erreur lors de l'archivage de la page {page['id']}: {e}")
        
        return {"status": "success", "deleted_count": count}
    
    async def get_stats_comptables(self, goals_db_id):
        purchase_map = await self._get_purchase_prices() #
        sales_pages = await self._fetch_all_pages(SALES_DB_ID) #

        total_ca = 0.0
        total_benefice_net = 0.0
        total_investissement_vendu = 0.0

        for page in sales_pages:
            props = page["properties"]
            item_name = props.get("Nom", {}).get("title", [{}])[0].get("plain_text", "")
            
            # Gestion du champ Formule pour le CA global également
            formula_obj = props.get("Prix de vente NET", {}).get("formula", {}) #
            prix_vente = float(formula_obj.get("number") or 0.0) #
            
            prix_achat = float(purchase_map.get(item_name, 0.0)) #

            total_ca += prix_vente
            if prix_achat > 0:
                total_benefice_net += (prix_vente - prix_achat)
                total_investissement_vendu += prix_achat

        return {
            "ca_global": total_ca,
            "ca_global_format": f"{total_ca:.2f} €".replace(".", ","),
            "investissement_total": total_investissement_vendu,
            "investissement_total_format": f"{total_investissement_vendu:.2f} €".replace(".", ","),
            "benefice_estime": total_benefice_net,
            "benefice_format": f"{total_benefice_net:.2f} €".replace(".", ",")
        }
    
def update_sync_timestamp(key):
        """
        Met à jour une date spécifique dans cron_status.json
        Keys possibles : 'last_cron_14h', 'last_cron_22h', 'last_manual_scrap', 'last_clemz_sync'
        """
        file_path = "cron_status.json"
        now = datetime.now().strftime("%d/%m %H:%M")
        
        data = {}
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                try:
                    data = json.load(f)
                except:
                    data = {}
        
        data[key] = now
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)