import asyncio
import sys

# CORRECTIF WINDOWS POUR PLAYWRIGHT
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Imports des routes
from routes import inventory, sourcing
# On importe l'état global et le config depuis database pour la cohérence
from database import app_state 
# On importe le scheduler pour le piloter au démarrage
from services.automation_service import scheduler
# On importe le service pour l'instancier
from services.sniper_service import SniperService

app = FastAPI(
    title="Vinted Sniper API",
    description="Système d'automatisation Sourcing & Stock"
)

# 2. Initialisation du Singleton Sniper avec l'état partagé
# On s'assure que sniper_active et errors sont bien présents
if "errors" not in app_state:
    app_state["errors"] = []

# 1. Création de la MÉMOIRE PARTAGÉE (Le dictionnaire global)
# C'est cette référence qui est passée au SniperService
sniper_service = SniperService(app_state)

# 2. INSTANCIATION UNIQUE (Le Singleton)
# On passe 'app_state' ici. Toute modification faite par 'sniper' 
# sera visible directement dans 'app_state'.
sniper = SniperService(app_state)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Événements de cycle de vie (Startup / Shutdown)
@app.on_event("startup")
async def startup_event():
    """Actions au démarrage : lancement du scheduler."""
    if not scheduler.running:
        scheduler.start()
        print("📅 [SYSTEM] Ordonnanceur APScheduler démarré (Sync à 14h et 22h).")

@app.on_event("shutdown")
async def shutdown_event():
    """Actions à l'arrêt : extinction propre du scheduler."""
    if scheduler.running:
        scheduler.shutdown()
        print("📅 [SYSTEM] Ordonnanceur arrêté proprement.")

# Route racine (remplace la route erronée de inventory.py)
@app.get("/")
async def root():
    """Route racine pour vérifier le statut de l'API."""
    return {
        "status": "online",
        "message": "Vinted Sniper API est opérationnelle",
        "sniper_active": app_state.get("sniper_active", False),
        "scheduler_running": scheduler.running
    }

# Montage des modules de routes
app.include_router(inventory.router)
app.include_router(sourcing.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)