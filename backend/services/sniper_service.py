import json
import os
import unicodedata
from datetime import datetime, timedelta
from fastapi import HTTPException
from services.sourcing_worker import VintedMasterWorker
from services.auth_service import AuthService
import logging

worker_logger = logging.getLogger("sniper_worker")

def _normalize(text):
    """Supprime les accents et met en minuscule pour une comparaison robuste."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip().lower()


class SniperService:
    def __init__(self, app_state):
        """
        Gère la logique métier du Sourcing.
        :param state_reference: Référence vers app_state pour manipuler le flux et le statut.
        :param logger_service: Instance de LoggerService pour notifier le front.
        """
        self.app_state = app_state

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        referentiel_path = os.path.join(base_dir, "vinted_referentiels.json")
        self.referentiel = self._load_referentiel(referentiel_path)
        self.app_state["active_filters"] = []

        # CHARGEMENT MÉMOIRE : On crée un set() pour des recherches instantanées
        self.blacklist_file = "blacklist.json"
        self.blacklist_cache = self._load_blacklist()

        # Initialisation du flux dans l'état s'il n'existe pas
        if "current_feed" not in self.app_state:
            self.app_state["current_feed"] = []

        # Statut par défaut : Éteint au lancement
        if "sniper_active" not in self.app_state:
            self.app_state["sniper_active"] = False

    def _load_referentiel(self, path):
        """Charge le référentiel unique des IDs Vinted (marques, catégories, tailles, etc.)."""
        if not os.path.exists(path):
            print("⚠️ vinted_referentiels.json introuvable.")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"🚨 Erreur lecture vinted_referentiels.json : {e}")
            return {}

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

    def local_dispatcher(self, raw_items, active_filters, brands_db):
        dispatched_items = []

        # Index inversé : brand_id → nom de marque (depuis vinted_referentiels.json)
        marques = self.referentiel.get("marques", {})
        id_to_brand = {str(v): _normalize(k) for k, v in marques.items()}

        for item in raw_items:
            item_id = str(item.get("id"))
            if self.is_excluded(item_id):
                continue

            price = float(item.get("price", {}).get("amount", 0))
            # Vinted fournit brand_title nativement à la racine de l'item
            # (confirmé sur www.vinted.fr/api/v2/catalog/items) — pas besoin
            # de le reconstruire depuis item_box.first_line, qui n'est pas fiable
            # pour toutes les marques.
            raw_brand = (item.get("brand_title") or "").strip()
            item_brand_title = _normalize(raw_brand)

            for f in active_filters:
                # Vérification marque par nom (résolution ID → nom via référentiel)
                if f.get("marque_ids"):
                    allowed_names = {id_to_brand.get(str(mid), "") for mid in f["marque_ids"]}
                    if item_brand_title not in allowed_names:
                        continue

                # Prix
                if price > float(f.get("prix_max") or 999):
                    continue
                if price < float(f.get("prix_min") or 0):
                    continue

                item["filter_name"] = f.get("nom")
                dispatched_items.append(item)
                print(f"🎯 MATCH: {item.get('title')} | Marque: {item_brand_title} | Filtre: {f.get('nom')}")
                break

        return dispatched_items

    def add_to_blacklist(self, product_id):
        """Ajoute un ID à la blacklist, met à jour le fichier et nettoie le flux."""
        str_id = str(product_id)
        print(f"🚫 [DEBUG BLACKLIST] Demande d'ajout pour l'ID '{str_id}' (type reçu : {type(product_id).__name__})")

        if str_id not in self.blacklist_cache:
            self.blacklist_cache.add(str_id)
            with open(self.blacklist_file, "w") as f:
                json.dump(list(self.blacklist_cache), f)
            print(f"🚫 [DEBUG BLACKLIST] Ajouté avec succès. Taille du cache : {len(self.blacklist_cache)}")
        else:
            print(f"🚫 [DEBUG BLACKLIST] Déjà présent dans le cache — rien à faire.")

        self.app_state["current_feed"] = [
            p for p in self.app_state["current_feed"] if str(p['id']) != str_id
        ]
        return {"status": "blacklisted", "id": str_id}

    FEED_TTL_MINUTES = 30  # durée minimale de vie garantie d'un article dans le flux
    MAX_FEED_SIZE = 500    # garde-fou anti-fuite mémoire, pas un mécanisme de rotation normal

    def update_feed(self, new_items):
        """
        Ajoute les nouvelles pépites au flux. Contrairement à un cap par volume
        (qui éjecte les plus anciens dès qu'un seuil est atteint, même si l'user
        n'a pas eu le temps de les trier), un article reste visible pendant
        FEED_TTL_MINUTES minimum, quel que soit le nombre de nouveaux articles
        qui arrivent derrière. Seul le blacklist (action explicite de l'user)
        ou l'expiration par âge le retirent.
        """
        if not new_items:
            return

        existing_ids = {str(item.get("id")) for item in self.app_state["current_feed"]}
        truly_new = [item for item in new_items if str(item.get("id")) not in existing_ids]

        now = datetime.now()

        if truly_new:
            for item in truly_new:
                item["first_seen_at"] = now.isoformat()
            self.app_state["current_feed"] = truly_new + self.app_state["current_feed"]
        else:
            print("📊 [FLUX] Aucun nouvel article depuis le dernier scan.")

        # Purge par âge, pas par volume : un article ne sort que s'il dépasse la TTL
        cutoff = now - timedelta(minutes=self.FEED_TTL_MINUTES)
        before_count = len(self.app_state["current_feed"])
        self.app_state["current_feed"] = [
            item for item in self.app_state["current_feed"]
            if datetime.fromisoformat(item.get("first_seen_at", now.isoformat())) > cutoff
        ]
        expired_count = before_count - len(self.app_state["current_feed"])
        if expired_count:
            print(f"🗑️ [FLUX] {expired_count} article(s) expiré(s) après {self.FEED_TTL_MINUTES} min.")

        # Garde-fou dur anti-fuite mémoire uniquement, ne devrait jamais se déclencher en usage normal
        if len(self.app_state["current_feed"]) > self.MAX_FEED_SIZE:
            self.app_state["current_feed"] = self.app_state["current_feed"][:self.MAX_FEED_SIZE]

        print(f"📊 [FLUX] {len(truly_new)} nouveaux articles ajoutés. Total : {len(self.app_state['current_feed'])}")

