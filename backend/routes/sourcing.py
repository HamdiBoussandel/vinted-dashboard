import json
import os
from fastapi import APIRouter, BackgroundTasks, Body
from database import app_state, config
from services.sniper_service import SniperService
from services.supabase_service import SupabaseService
from services.sourcing_worker import VintedMasterWorker # Import du nouveau worker
from services.auth_service import AuthService

REFERENTIEL_PATH = r"C:\Users\hamdi\Documents\vinted-dashboard\backend\vinted_referentiels.json"


auth_manager = AuthService()

router = APIRouter(prefix="/api", tags=["Sourcing"])

# Initialisation des services avec l'état partagé
sniper = SniperService(app_state)
supabase_svc = SupabaseService()

# Initialisation de l'exécuteur (Worker)
# Note : auth_data doit contenir token, session et ua récupérés au préalable
worker = VintedMasterWorker(config.auth_data, config.brands_db, sniper)

@router.post("/toggle-sniper")
async def toggle_radar(background_tasks: BackgroundTasks):
    current_status = app_state.get("sniper_active", False)
    new_status = not current_status

    if new_status:
        local_filters = supabase_svc.fetch_filters()
        print(f"📋 [FILTRES] {len(local_filters)} filtres chargés depuis Supabase.")
        
        # 1. RÉCUPÉRATION DES JETONS TOUT NEUFS
        print("🔑 [AUTH] Récupération des accès Vinted...")
        auth = await auth_manager.fetch_vinted_auth()

        if not auth["token"] or not auth["session"]:
            print("❌ [AUTH] Impossible de récupérer les accès.")
            return {"status": "error", "message": "Échec authentification Vinted"}

        # 2. MISE À JOUR DU WORKER AVEC LES NOUVEAUX JETONS
        worker.auth_data = auth
        # On s'assure que le worker a bien les filtres
        worker.filters = local_filters

        # Reconstruction complète des headers (même logique que VintedMasterWorker.__init__) :
        # svc-catalogue exige le jar de cookies complet + x-anon-id / x-csrf-token,
        # PAS un header Authorization Bearer ni un cookie unique.
        cookie_header = "; ".join(f"{k}={v}" for k, v in auth.get("all_cookies", {}).items())
        anon_id = auth.get("all_cookies", {}).get("anon_id", "")

        worker.headers = {
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie_header,
            "User-Agent": auth["ua"],
            "Referer": "https://www.vinted.fr/",
            "Origin": "https://www.vinted.fr",
            "Locale": "fr-FR",
            "Platform": "web",
            "X-Next-App": "marketplace-web",
            "X-Anon-Id": anon_id,
            "X-Csrf-Token": auth.get("csrf_token") or "",
        }

        # 3. MISE À JOUR FILTRES + ACTIVATION
        app_state["active_filters"] = supabase_svc.fetch_filters()
        sniper.set_sniper_status(True)
        background_tasks.add_task(worker.run)
    else:
        # ARRÊT
        sniper.set_sniper_status(False)

    return {"sniper_active": new_status}

@router.get("/feed-data")
async def get_feed():
    # Pour React qui affiche les produits
    return app_state.get("current_feed", [])

@router.post("/blacklist")
async def update_blacklist(data: dict = Body(...)):
    """Ajoute un produit à la liste noire."""
    return sniper.add_to_blacklist(data.get("id"))

@router.get("/sniper-status") # On change 'state' par 'sniper-status'
async def get_sniper_status():
    """Renvoie uniquement les infos dont le Sniper a besoin."""
    return {
        "sniper_active": app_state.get("sniper_active", False),
        "product_count": len(app_state.get("current_feed", []))
    }

@router.get("/sourcing/filters")
async def get_sourcing_filters():
    try:
        return supabase_svc.fetch_filters()
    except Exception as e:
        print(f"🚨 Erreur récupération filtres Supabase : {e}")
        return []

@router.post("/sourcing/filters")
async def create_sourcing_filter(data: dict = Body(...)):
    if not data.get("categorie_ids"):
        return {"status": "error", "message": "Le filtre doit cibler au moins une catégorie."}
    try:
        return supabase_svc.create_filter(data)
    except Exception as e:
        print(f"🚨 Erreur création filtre : {e}")
        return {"status": "error", "message": str(e)}

@router.put("/sourcing/filters/{filter_id}")
async def update_sourcing_filter(filter_id: int, data: dict = Body(...)):
    if "categorie_ids" in data and not data.get("categorie_ids"):
        return {"status": "error", "message": "Le filtre doit cibler au moins une catégorie."}
    try:
        return supabase_svc.update_filter(filter_id, data)
    except Exception as e:
        print(f"🚨 Erreur mise à jour filtre : {e}")
        return {"status": "error", "message": str(e)}

@router.delete("/sourcing/filters/{filter_id}")
async def delete_sourcing_filter(filter_id: int):
    try:
        return supabase_svc.delete_filter(filter_id)
    except Exception as e:
        print(f"🚨 Erreur suppression filtre : {e}")
        return {"status": "error", "message": str(e)}

@router.get("/sourcing/referentiel")
async def get_referentiel():
    """Expose le référentiel Vinted (marques, catégories, tailles, couleurs, états, matières, motifs)
    pour peupler les formulaires de filtre côté front."""
    try:
        with open(REFERENTIEL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"🚨 Erreur lecture référentiel : {e}")
        return {}