import asyncio
import sys

# CORRECTIF WINDOWS POUR PLAYWRIGHT
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# CORRECTIF ENCODAGE WINDOWS (cf. échange du 07/10/2026) -- sans ça, un simple
# print() contenant un emoji (omniprésents dans tout le code, ex: 🩹 dans
# automation_service.rattraper_republish_manques) fait planter le démarrage
# ENTIER avec UnicodeEncodeError dès que stdout utilise l'encodage Windows
# par défaut (cp1252 sur une machine en français) -- systématique quand la
# sortie est redirigée vers un fichier (demarrer_backend.bat), pas seulement
# en console. Le logging (handlers ci-dessous) a déjà sa propre protection
# (encoding="utf-8" explicite) ; ceci couvre tous les print() bruts, trop
# nombreux pour être corrigés un par un. errors="replace" : un caractère
# jamais vu plus tard ne doit plus jamais refaire planter tout le serveur.
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

# Configuration centralisée du logging -- DOIT s'exécuter avant tout import
# de module métier (routes/services), dont plusieurs appellent chacun leur
# propre logging.basicConfig(level=logging.INFO) sans handler de fichier :
# seul le PREMIER appel à basicConfig() dans le process a un effet (les
# suivants sont ignorés), donc celui-ci doit gagner la course -- force=True
# en plus, pour ne dépendre d'aucun ordre d'import. Sans ça, tous les
# logger.error(...) partaient uniquement vers la console du process, invisible
# dès qu'on tourne en tâche planifiée Windows (aucune console attachée) ou dès
# que le terminal de dev est fermé -- aucune trace persistante des erreurs
# (ex: échec au lancement de Chrome), cf. échange du 19/09/2026.
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        # encoding="utf-8" explicite -- sans lui, l'encodage par défaut
        # Windows (cp1252) plante sur les emojis omniprésents dans les logs
        # du projet (❌, 🚀, ✅...).
        RotatingFileHandler(
            os.path.join(_LOG_DIR, "server.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
    force=True,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Imports des routes
from routes import inventory, sourcing, price_estimation, preparer_annonces, maintenance, risk_guard
# On importe l'état global et le config depuis database pour la cohérence
from database import app_state 
# On importe le scheduler pour le piloter au démarrage
from services.automation_service import scheduler
from services.clemz_auto_message import ClemzAutoMessageWatchdog
from services.maintenance_service import maintenance_service

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vinted Sniper API",
    description="Système d'automatisation Sourcing & Stock"
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Référence globale à la task watchdog, pour pouvoir l'annuler proprement au shutdown
watchdog_task = None


def _on_watchdog_task_done(task: asyncio.Task):
    """
    Callback appelé quand la task watchdog se termine, pour QUELQUE raison que ce
    soit. Sans ce callback, une exception levée dans run_forever() serait perdue
    silencieusement (asyncio.create_task() n'affiche rien tout seul) -- ici on la
    logue explicitement, SANS jamais faire planter le reste du serveur FastAPI.
    """
    if task.cancelled():
        logger.info("📨 [WATCHDOG] Task arrêtée proprement (annulation demandée).")
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"❌ [WATCHDOG] La task s'est arrêtée suite à une erreur non gérée : {exc}")
        # Le reste du serveur (scraping, dashboard) continue de tourner normalement --
        # seule la fonctionnalité 'messages automatiques' est down jusqu'au redémarrage.


# 4. Événements de cycle de vie (Startup / Shutdown)
@app.on_event("startup")
async def startup_event():
    """Actions au démarrage : lancement du scheduler + du watchdog messages auto."""
    if not scheduler.running:
        scheduler.start()

    from services.automation_scheduler import cleanup_stale_running_tasks
    nb_cleaned = cleanup_stale_running_tasks()
    if nb_cleaned:
        print(f"🧹 [STARTUP] {nb_cleaned} tâche(s) 'en cours' interrompue(s) par le redémarrage précédent — marquée(s) 'Échec'.")
        print("📅 [SYSTEM] Ordonnanceur APScheduler démarré (Sync à 14h et 22h).")

    # Rattrapage : si le backend était éteint au passage de minuit, aucun plan
    # du jour n'a été généré pour D1/D2 -- sans ce filet, les deux dressings
    # resteraient au repos toute la journée, faute d'horaire calculé.
    from services.automation_scheduler import get_plan_du_jour
    from services.automation_service import generer_plans_du_jour_cron, rattraper_republish_manques
    if get_plan_du_jour("Dressing 1") is None or get_plan_du_jour("Dressing 2") is None:
        print("🎲 [STARTUP] Aucun plan du jour valide pour aujourd'hui — génération de rattrapage.")
        await generer_plans_du_jour_cron()
    else:
        # Cas distinct du précédent : un plan du jour existe déjà (généré à
        # minuit), mais le job de republication associé ne vit qu'en mémoire
        # dans APScheduler -- un redémarrage survenu entre la génération du
        # plan et l'horaire tiré le perd silencieusement (cf. échange du
        # 21/09/2026, Dressing 2 actif à 11h00 jamais republié après deux
        # redémarrages consécutifs avant cet horaire).
        await rattraper_republish_manques()

    global watchdog_task
    # 🔧 TEMPORAIRE : watchdog désactivé le temps de régler le problème de
    # sessions Vinted (conflit entre reconnexion manuelle du scraping et
    # tentatives simultanées du watchdog). À réactiver une fois le souci résolu.
    print("📨 [SYSTEM] Watchdog 'Messages automatiques' DÉSACTIVÉ (temporaire).")
    # try:
    #     watchdog = ClemzAutoMessageWatchdog()
    #     watchdog_task = asyncio.create_task(watchdog.run_forever())
    #     watchdog_task.add_done_callback(_on_watchdog_task_done)
    #     print("📨 [SYSTEM] Watchdog 'Messages automatiques' démarré (Chrome + Edge).")
    # except Exception as e:
    #     # Même l'initialisation elle-même (avant le premier await) est protégée :
    #     # le serveur doit démarrer quoi qu'il arrive, avec juste un log d'alerte.
    #     logger.error(f"❌ [SYSTEM] Échec du démarrage du watchdog 'Messages automatiques' : {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Actions à l'arrêt : extinction propre du scheduler et du watchdog."""
    if scheduler.running:
        scheduler.shutdown()
        print("📅 [SYSTEM] Ordonnanceur arrêté proprement.")

    global watchdog_task
    if watchdog_task and not watchdog_task.done():
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        print("📨 [SYSTEM] Watchdog 'Messages automatiques' arrêté proprement.")

    # Watchdog piloté manuellement depuis la section Automatisation de Maintenance
    await maintenance_service.stop_watchdog()

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
app.include_router(preparer_annonces.router)
app.include_router(maintenance.router)
app.include_router(risk_guard.router)
# app.include_router(price_estimation.router)  # TODO: routeur pas encore implémenté (price_estimation.py n'expose que des fonctions, pas d'APIRouter)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="asyncio")