import asyncio
import random
from curl_cffi import requests  # Simulation parfaite de l'empreinte TLS Chrome
from datetime import datetime

class VintedMasterWorker:
    def __init__(self, auth_data, brands_db, service_instance):
        """
        Worker optimisé pour le sniping multi-filtres.
        """
        self.auth_data = auth_data
        self.brands_db = brands_db
        self.sniper = service_instance  # Instance du SniperService (le cerveau)
        self.is_running = False

        self.filters = []

        # 1. Création de la session persistante (Chrome 120)
        # On la crée une seule fois pour maintenir la connexion et les cookies
        self.session = requests.AsyncSession(impersonate="chrome120")

        # 2. Préparation des headers authentifiés
        self.headers = {
            "Authorization": f"Bearer {self.auth_data['token']}",
            "Cookie": f"_vinted_fr_session={self.auth_data['session']}",
            "User-Agent": self.auth_data["ua"],
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _build_master_params(self, active_filters):
        """
        Fusionne tous les filtres Notion en une seule requête "Parapluie".
        """
        all_brands = []
        max_price = 0
        
        for f in active_filters:
            # Récupération des IDs de marques depuis ton JSON local
            names = f.get("marques", [])
            for name in names:
                if name in self.brands_db:
                    b_info = self.brands_db[name]
                    # Gestion du format d'ID (objet ou simple valeur)
                    b_id = str(b_info['id'] if isinstance(b_info, dict) else b_info)
                    all_brands.append(b_id)
            
            # On prend le prix le plus élevé parmi tous les filtres pour la requête Vinted
            p_max = float(f.get("prix_max", 0))
            if p_max > max_price:
                max_price = p_max

        return {
            "brand_ids": ",".join(list(set(all_brands))), # Suppression des doublons
            "price_to": max_price,
            "order": "newest_first",
            "catalog_ids": "5", # Catégories : Homme, Hauts, Vestes
            "per_page": 90
        }

    def get_cost_estimation(self, title, size_title, price_amount):
        """
        Calcule les frais réels (Port + Protection) pour afficher le Total Investi.
        """
        t = title.lower()
        s = str(size_title).upper()
        
        # Estimation du poids/format du colis
        heavy_items = ["veste", "manteau", "blouson", "jean", "pantalon", "chaussures"]
        is_medium = any(word in t for word in heavy_items) or s in ["M", "L", "XL"]
        
        shipping = 3.75 if is_medium else 3.10
        service_fee = 0.70 + (price_amount * 0.05)
        
        return {
            "shipping_est": round(shipping, 2),
            "service_fee": round(service_fee, 2),
            "total_invested": round(price_amount + service_fee + shipping, 2)
        }

    async def run(self):
        """
        Boucle principale du sniper pilotée par le bouton React.
        """
        print("🚀 [WORKER] Agent Master démarré et prêt.")

        while self.sniper.app_state.get("sniper_active"):
            # UTILISATION DES FILTRES :
            if not self.filters:
                print("⚠️ Aucun filtre chargé dans le worker, attente...")
                await asyncio.sleep(5)
                continue

            # 1. Vérification du bouton Toggle (lecture mémoire directe dans le service)
            if not self.sniper.app_state.get("sniper_active"):
                await asyncio.sleep(2)
                continue

            try:
                params = self._build_master_params(self.filters)
                # 2. Récupération des filtres actifs (stockés dans l'état par ton app)
                  
                print(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] Scan Vinted (Master Request)...")
                
                res = await self.session.get(
                    "https://www.vinted.fr/api/v2/catalog/items",
                    headers=self.headers,
                    params=params,
                    timeout=10
                )

                if res.status_code == 200:
                    raw_items = res.json().get("items", [])
                    
                    # 4. TRI LOCAL : Le service filtre par thématique (Low/Mid) et retire la blacklist
                    matched_items = self.sniper.local_dispatcher(
                        raw_items, 
                        self.filters,
                        self.brands_db
                    )
                    
                    # 5. ENRICHISSEMENT : Ajout des calculs de coûts pour chaque match
                    for item in matched_items:
                        price_val = float(item.get("price", {}).get("amount", 0))
                        size_val = item.get("size_title", "N/A")
                        
                        costs = self.get_cost_estimation(item.get("title", ""), size_val, price_val)
                        item.update(costs) # Injecte shipping_est, service_fee et total_invested
                    
                    # 6. MISE À JOUR DU DASHBOARD REACT
                    self.sniper.update_feed(matched_items)

                elif res.status_code == 401:
                    print("🔑 [ALERTE] Session Vinted expirée. Reconnexion nécessaire.")
                    self.sniper.set_sniper_status(False)
                    break
                
                elif res.status_code == 429:
                    print("🐢 [ALERTE] Rate limit détecté (429). Augmentation du délai...")
                    await asyncio.sleep(60)

            except Exception as e:
                print(f"🚨 [ERREUR WORKER] : {e}")

            # 7. TIMING : Intervalle de 15-20 secondes avec Jitter (aléatoire)
            wait_time = 15 + random.uniform(0, 5)
            await asyncio.sleep(wait_time)

    def stop(self):
        """Arrête proprement la boucle du worker."""
        self.is_running = False