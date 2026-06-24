from fastapi import APIRouter, BackgroundTasks, Body
from database import app_state, config
import json
import os
from services.sniper_service import SniperService
from services.notion_service import NotionService
from services.sourcing_worker import VintedMasterWorker # Import du nouveau worker
from services.auth_service import AuthService

auth_manager = AuthService()

router = APIRouter(prefix="/api", tags=["Sourcing"])

# Initialisation des services avec l'état partagé
sniper = SniperService(app_state)
notion = NotionService()

# Initialisation de l'exécuteur (Worker)
# Note : auth_data doit contenir token, session et ua récupérés au préalable
worker = VintedMasterWorker(config.auth_data, config.brands_db, sniper)

@router.post("/toggle-sniper")
async def toggle_radar(background_tasks: BackgroundTasks):
    current_status = app_state.get("sniper_active", False)
    new_status = not current_status

    if new_status:
        local_filters = sniper.load_local_filters()
        # On recharge les filtres depuis le JSON local (très rapide)
        app_state["active_filters"] = sniper.load_local_filters()
        print(f"📋 [FILTRES] {len(local_filters)} filtres chargés depuis le JSON.")
        
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

        worker.headers.update({
            "Authorization": f"Bearer {auth['token']}",
            "Cookie": f"_vinted_fr_session={auth['session']}"
        })

        # 3. MISE À JOUR FILTRES + ACTIVATION
        #app_state["active_filters"] = await notion.fetch_notion_filters()
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
        # On définit le chemin vers le dossier services
        # On part du dossier actuel (routes) pour aller dans ../services/filters.json
        base_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(base_dir, "..", "services", "filters.json")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"🚨 Erreur lecture filters.json : {e}")
        return []