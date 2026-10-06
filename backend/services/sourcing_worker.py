import asyncio
import random
import logging
import os
from curl_cffi import requests
from datetime import datetime

# Dossier de logs, un fichier par cycle de scan
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "sniper_cycles")
os.makedirs(LOGS_DIR, exist_ok=True)

worker_logger = logging.getLogger("sniper_worker")
worker_logger.setLevel(logging.INFO)
worker_logger.propagate = False  # évite la duplication dans les logs FastAPI/uvicorn

# Garde une sortie console en plus du fichier
if not any(isinstance(h, logging.StreamHandler) for h in worker_logger.handlers):
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    worker_logger.addHandler(console_handler)


def _start_new_cycle_log_file():
    """Crée un nouveau fichier de log pour le cycle qui commence, en retirant l'ancien handler fichier."""
    for h in list(worker_logger.handlers):
        if isinstance(h, logging.FileHandler):
            worker_logger.removeHandler(h)
            h.close()

    filename = f"cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    filepath = os.path.join(LOGS_DIR, filename)
    file_handler = logging.FileHandler(filepath, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    worker_logger.addHandler(file_handler)
    return filepath

class VintedMasterWorker:
    def __init__(self, auth_data, brands_db, service_instance):
        self.auth_data = auth_data
        self.brands_db = brands_db
        self.sniper = service_instance
        self.is_running = False
        self.filters = []
        self.session = requests.AsyncSession(impersonate="chrome120")
        self.headers = {
            "Authorization": f"Bearer {self.auth_data['token']}",
            "Cookie": f"_vinted_fr_session={self.auth_data['session']}",
            "User-Agent": self.auth_data["ua"],
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _build_filter_params(self, f):
        """Construit les paramètres pour UN seul filtre."""
        params = []
        for cid in (f.get("categorie_ids") or []):
            params.append(("catalog[]", cid))
        for bid in (f.get("marque_ids") or []):
            params.append(("brand_ids[]", bid))
        for sid in (f.get("taille_ids") or []):
            params.append(("size_ids[]", sid))
        for col in (f.get("couleur_ids") or []):
            params.append(("color_ids[]", col))
        for st in (f.get("etat_ids") or []):
            params.append(("status_ids[]", st))
        for mat in (f.get("matiere_ids") or []):
            params.append(("material_ids[]", mat))
        for pat in (f.get("motif_ids") or []):
            params.append(("patterns_ids[]", pat))
        if f.get("prix_max"):
            params.append(("price_to", int(float(f["prix_max"]))))
        if f.get("prix_min"):
            params.append(("price_from", int(float(f["prix_min"]))))
        params.append(("currency", "EUR"))
        params.append(("order", "newest_first"))
        params.append(("per_page", random.choice([60, 72, 90])))
        return params

    def get_cost_estimation(self, title, size_title, price_amount):
        t = title.lower()
        s = str(size_title).upper()
        heavy_items = ["veste", "manteau", "blouson", "jean", "pantalon", "chaussures"]
        is_medium = any(word in t for word in heavy_items) or s in ["M", "L", "XL"]
        shipping = 3.75 if is_medium else 3.10
        return {
            "shipping_est": round(shipping, 2),
        }

    async def run(self):
        worker_logger.info("🚀 [WORKER] Agent Master démarré et prêt.")

        while self.sniper.app_state.get("sniper_active"):
            if not self.filters:
                worker_logger.info("⚠️ Aucun filtre chargé dans le worker, attente...")
                await asyncio.sleep(5)
                continue

            if not self.sniper.app_state.get("sniper_active"):
                await asyncio.sleep(2)
                continue

            log_filepath = _start_new_cycle_log_file()
            worker_logger.info(f"📂 Cycle démarré — log : {log_filepath}")

            try:
                total_matched_count = 0
                seen_ids = set()

                for f in self.filters:
                    marque_ids = f.get("marque_ids") or []
                    brand_batches = [[bid] for bid in marque_ids] if marque_ids else [[]]

                    for brand_batch in brand_batches:
                        f_single = dict(f)
                        f_single["marque_ids"] = brand_batch
                        params = self._build_filter_params(f_single)

                        label = f"{f.get('nom')} [{brand_batch[0] if brand_batch else 'toutes marques'}]"
                        worker_logger.info(f"🔍 [{datetime.now().strftime('%H:%M:%S')}] Scan filtre '{label}'...")

                        res = await self.session.get(
                            "https://www.vinted.fr/api/v2/catalog/items",
                            headers=self.headers,
                            params=params,
                            timeout=10
                        )

                        if res.status_code == 200:
                            raw_items = res.json().get("items", [])
                            worker_logger.info(f"📦 [{label}] {len(raw_items)} articles bruts")

                            raw_ids = {str(it.get("id")) for it in raw_items}
                            blacklisted_in_batch = raw_ids & self.sniper.blacklist_cache
                            if blacklisted_in_batch:
                                worker_logger.info(f"🚫 [DEBUG BLACKLIST] Présent dans le brut Vinted alors que blacklisté : {blacklisted_in_batch}")

                            matched = self.sniper.local_dispatcher(raw_items, [f], self.brands_db)
                            worker_logger.info(f"🎯 [{label}] {len(matched)} articles matchés")

                            matched_ids = {str(m.get("id")) for m in matched}
                            leaked = matched_ids & self.sniper.blacklist_cache
                            if leaked:
                                worker_logger.info(f"🚨 [DEBUG BLACKLIST] FUITE CONFIRMÉE : {leaked} blacklisté(s) mais quand même dans 'matched' !")

                            batch_to_push = []
                            for item in matched:
                                item_id = str(item.get("id"))
                                if item_id in seen_ids:
                                    continue
                                seen_ids.add(item_id)
                                item["filter_name"] = f.get("nom")
                                price_val = float(item.get("price", {}).get("amount", 0))
                                costs = self.get_cost_estimation(item.get("title", ""), item.get("size_title", ""), price_val)
                                item["shipping_est"] = costs["shipping_est"]
                                batch_to_push.append(item)

                            # Poussée immédiate au flux, marque par marque —
                            # ne pas attendre que les 10 marques soient scannées
                            # avant que le frontend voie les résultats.
                            if batch_to_push:
                                self.sniper.update_feed(batch_to_push)
                                total_matched_count += len(batch_to_push)

                        elif res.status_code == 401:
                            worker_logger.info("🔑 Session expirée.")
                            self.sniper.set_sniper_status(False)
                            break
                        elif res.status_code == 429:
                            worker_logger.info("🐢 Rate limit, pause 60s")
                            await asyncio.sleep(60)

                        await asyncio.sleep(random.uniform(3, 6))

                worker_logger.info(f"📊 [FLUX] {total_matched_count} articles ajoutés ce cycle")

            except Exception as e:
                worker_logger.info(f"🚨 [ERREUR WORKER] : {e}")

            wait_time = 20 + random.uniform(0, 10)
            await asyncio.sleep(wait_time)

    def stop(self):
        self.is_running = False