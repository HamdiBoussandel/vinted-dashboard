import json
import os
from fastapi import HTTPException
from services.sourcing_worker import VintedMasterWorker
from services.auth_service import AuthService

class SniperService:
    def __init__(self, app_state):
        """
        Gère la logique métier du Sourcing.
        :param state_reference: Référence vers app_state pour manipuler le flux et le statut.
        :param logger_service: Instance de LoggerService pour notifier le front.
        """
        self.app_state = app_state

        # Dans SniperService.__init__
        base_dir = os.path.dirname(os.path.abspath(__file__))
        # Comme SniperService est déjà dans /services, on pointe directement le fichier ici
        self.filters_file = os.path.join(base_dir, "filters.json")

        self.app_state["active_filters"] = self.load_local_filters()

        # CHARGEMENT MÉMOIRE : On crée un set() pour des recherches instantanées
        self.blacklist_file = "blacklist.json"
        self.blacklist_cache = self._load_blacklist()
        
        # Initialisation du flux dans l'état s'il n'existe pas
        if "current_feed" not in self.app_state:
            self.app_state["current_feed"] = []

        # Statut par défaut : Éteint au lancement
        if "sniper_active" not in self.app_state:
            self.app_state["sniper_active"] = False

    def _load_blacklist(self):
        """Récupère la liste des IDs bannis depuis le fichier JSON."""
        if not os.path.exists(self.blacklist_file):
            return set()
        try:
            with open(self.blacklist_file, "r") as f:
                data = json.load(f)
                # On convertit tout en string pour éviter les erreurs de type (int vs str)
                return set(map(str, data))
        except Exception as e:
            print(f"⚠️ Erreur chargement blacklist: {e}")
            return set()
        
    def load_local_filters(self):
        """Lit les filtres depuis le fichier JSON local."""
        if not os.path.exists(self.filters_file):
            print("⚠️ Fichier filters.json introuvable.")
            return []
        try:
            with open(self.filters_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"🚨 Erreur lecture filters.json : {e}")
            return []
    
    def is_excluded(self, product_id):
        """
        Vérifie si un produit doit être ignoré.
        Vrai si l'ID est dans la blacklist ou déjà présent dans le flux actuel.
        """
        str_id = str(product_id)
        
        # 1. Vérification dans la blacklist persistante
        if str_id in self.blacklist_cache:
            return True
            
        # 2. Vérification dans le flux mémoire (doublon visuel)
        existing_ids = {str(p['id']) for p in self.app_state["current_feed"]}
        if str_id in existing_ids:
            return True
            
        return False
    
    def set_sniper_status(self, active: bool):
        """Allume ou éteint le worker via l'état partagé."""
        self.app_state["sniper_active"] = active
        status = "ACTIF" if active else "INACTIF"
        print(f"🤖 [STATUT] Sniper passé en mode : {status}")
        return {"status": "success", "sniper_active": active}

    def add_to_blacklist(self, product_id):
        """Ajoute un ID à la blacklist, met à jour le fichier et nettoie le flux."""
        str_id = str(product_id)
        if str_id not in self.blacklist_cache:
            self.blacklist_cache.add(str_id)
            # Sauvegarde persistante sur le disque
            with open(self.blacklist_file, "w") as f:
                json.dump(list(self.blacklist_cache), f)
        
        # Nettoyage immédiat : On retire l'article de l'affichage React
        self.app_state["current_feed"] = [
            p for p in self.app_state["current_feed"] if str(p['id']) != str_id
        ]
        return {"status": "blacklisted", "id": str_id}
    
    def local_dispatcher(self, raw_items, active_filters, brands_db):
        dispatched_items = []
        
        for item in raw_items:
            item_id = str(item.get("id"))
            if self.is_excluded(item_id):
                continue

            # On récupère le NOM de la marque envoyé par Vinted
            # Ex: "A.P.C.", "Octobre Éditions", etc.
            vinted_brand_name = item.get("brand_title")
            price = float(item.get("price", {}).get("amount", 0))

            if not vinted_brand_name:
                continue

            for f in active_filters:
                # On récupère la liste des noms autorisés dans ton filtre
                # Ex: ["A.P.C", "Octobre Éditions"]
                allowed_brand_names = f.get("marques", [])

                # Comparaison directe du texte (on ignore la casse pour plus de sécurité)
                if vinted_brand_name.strip().lower() in [name.lower() for name in allowed_brand_names]:
                    if price <= float(f.get("prix_max", 999)):
                        item["filter_name"] = f.get("nom")
                        dispatched_items.append(item)
                        print(f"🎯 MATCH TEXTE: {item.get('title')} | Marque: {vinted_brand_name}")
                        break
                else:
                    # Log de debug optionnel
                    pass

        return dispatched_items
    
    def update_feed(self, new_items):
        """
        Ajoute les nouvelles pépites au flux et limite la taille à 50.
        """
        if not new_items:
            return

        # On insère les nouveaux au début (index 0)
        # On utilise une liste temporaire pour garder l'ordre chronologique inverse
        self.app_state["current_feed"] = new_items + self.app_state["current_feed"]

        # LIMITATION MÉMOIRE : On garde les 50 plus récents
        if len(self.app_state["current_feed"]) > 100:
            self.app_state["current_feed"] = self.app_state["current_feed"][:100]
            
        print(f"📊 [FLUX] {len(new_items)} nouveaux articles ajoutés. Total en mémoire : {len(self.app_state['current_feed'])}")
