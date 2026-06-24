# services/automation_service.py
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from services.vinted_scraper import VintedScraper
from services.automation_scheduler import (
    get_scheduled_republish,
    mark_scheduled_republish_status,
    update_scheduled_run_time,
    start_task_run,
    update_task_result,
    finish_task_run,
)

# Initialisation
scheduler = AsyncIOScheduler()
scraper = VintedScraper()

async def run_cron_sync(slot):
    print(f"🕒 [CRON] Démarrage de la synchronisation de {slot}...")
    try:
        # CORRECTION : Ajout de await pour lancer l'exécution réelle
        await scraper.run_full_sync() 
        print(f"✅ [CRON] Synchronisation de {slot} terminée avec succès.")
    except Exception as e:
        print(f"❌ [CRON] Échec de la synchronisation de {slot} : {e}")

async def run_cron_republish():
    """
    Exécuté chaque soir à 18h00 : tire une heure aléatoire dans le créneau 18h-19h
    et replanifie le vrai job de republication à cet instant précis.
    Si aucune liste n'est programmée pour aujourd'hui, ne fait rien.
    """
    task = get_scheduled_republish()

    if not task:
        print("⏭️  [CRON REPUB] Aucune liste programmée ce soir, on passe.")
        return

    if task.get("status") != "pending":
        print(f"⏭️  [CRON REPUB] Statut '{task.get('status')}' — déjà traité ou en cours, on passe.")
        return

    # Tirage aléatoire d'une minute dans le créneau 18h00-18h59
    random_minute = random.randint(0, 59)
    random_second = random.randint(0, 59)
    now = datetime.now()
    run_time = now.replace(hour=18, minute=random_minute, second=random_second, microsecond=0)

    # Si on est déjà passé devant l'heure tirée (ex: le job 18h00 a mis du temps à se lancer),
    # on décale à la minute suivante pour ne pas rater le déclenchement
    if run_time <= now:
        run_time += timedelta(minutes=1)

    run_time_str = run_time.strftime("%H:%M:%S")
    update_scheduled_run_time(run_time_str)
    print(f"🎲 [CRON REPUB] Heure de republication tirée : {run_time_str}")

async def _execute_republish():
    """
    Job one-shot déclenché à l'heure tirée aléatoirement.
    Lit la liste depuis scheduled_republish.json, lance ClemzAutomation,
    et enregistre les résultats dans task_history.json.
    """
    from services.clemz_automation import ClemzAutomation  # Import local pour éviter les imports circulaires

    task = get_scheduled_republish()
    if not task or task.get("status") != "pending":
        print("⏭️  [REPUB] Liste absente ou déjà traitée au moment du déclenchement.")
        return

    items = task.get("items", [])
    if not items:
        print("⚠️  [REPUB] Liste vide, rien à faire.")
        return

    print(f"🚀 [REPUB] Lancement de la republication — {len(items)} article(s).")
    mark_scheduled_republish_status("running")

    # Démarrage du suivi dans l'historique
    task_id = start_task_run("republication", items)

    try:
        # Séparation des articles par dressing
        produits_d1 = [i["nom"] for i in items if i.get("dressing") == "Dressing 1"]
        produits_d2 = [i["nom"] for i in items if i.get("dressing") == "Dressing 2"]

        automation = ClemzAutomation(produits_d1, produits_d2)
        results = await automation.run()

        # Mise à jour de l'historique article par article
        for account_result in results:
            for item_result in account_result.get("selection_results", []):
                # On retrouve l'id Notion de l'article via son nom
                matched = next((i for i in items if i["nom"] == item_result["nom"]), None)
                item_id = matched["id"] if matched else item_result["nom"]
                update_task_result(
                    task_id,
                    item_id,
                    status=item_result["status"],
                    reason=item_result.get("reason"),
                )

            # Statut global du repost pour ce compte
            repost = account_result.get("repost_result")
            if repost and repost["status"] == "failed":
                print(f"⚠️  [REPUB] {account_result['account']} — repost échoué : {repost['reason']}")

    except Exception as e:
        print(f"❌ [REPUB] Erreur inattendue : {e}")
        # On marque tous les articles restants comme échoués
        for item in items:
            update_task_result(task_id, item["id"], status="failed", reason=f"Erreur fatale : {str(e)[:120]}")

    finally:
        finish_task_run(task_id)
        mark_scheduled_republish_status("done")
        print(f"✅ [REPUB] Tâche terminée, historique mis à jour.")


scheduler.add_job(
    run_cron_sync,
    CronTrigger(hour=14, minute=0),
    args=["14h"],
    id="sync_14h",
    replace_existing=True,
)

scheduler.add_job(
    run_cron_sync,
    CronTrigger(hour=22, minute=0),
    args=["22h"],
    id="sync_22h",
    replace_existing=True,
)

# Job déclencheur à 18h00 : tire l'heure aléatoire et replanifie le vrai job
scheduler.add_job(
    run_cron_republish,
    CronTrigger(hour=18, minute=0),
    id="republish_trigger",
    replace_existing=True,
)