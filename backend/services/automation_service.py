# services/automation_service.py
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from services.vinted_scraper import VintedScraper

from services.automation_scheduler import (
    get_scheduled_republish,
    mark_scheduled_republish_status,
    start_task_run,
    update_task_result,
    finish_task_run,
    start_partage_vues_favoris_run,
    finish_partage_vues_favoris_run,
    add_task_global_anomaly,
)
from services.planification_republication import generer_plans_du_jour

# Initialisation
scheduler = AsyncIOScheduler()
scraper = VintedScraper()

def _planifier_republish_oneshot(dressing, plan, job_id, creneau):
    """
    Factorisé hors de generer_plans_du_jour_cron() pour être réutilisable par
    rattraper_republish_manques() (cf. échange du 21/09/2026) : le job
    DateTrigger planifié ici ne vit QU'EN MÉMOIRE dans le scheduler
    (jobstore par défaut, non persisté) -- un redémarrage du backend entre la
    génération du plan (minuit) et l'horaire tiré le perd silencieusement,
    sans aucune trace ni republication, alors que le plan_du_jour_*.json (lui
    persisté sur disque) continue d'afficher "actif" avec son horaire comme
    si de rien n'était.
    """
    if not plan["actif"] or not plan["horaire"]:
        print(f"⏭️  [PLAN DU JOUR] {dressing} — repos aujourd'hui (niveau : {plan['niveau']}).")
        return

    heure, minute, seconde = (int(x) for x in plan["horaire"].split(":"))
    now = datetime.now()
    run_time = now.replace(hour=heure, minute=minute, second=seconde, microsecond=0)
    if run_time <= now:
        # Garde-fou (le job tourne à minuit normalement, la plage est 11h-19h,
        # donc toujours dans le futur) -- mais aussi le chemin normal du
        # rattrapage démarrage quand l'horaire tiré est déjà passé : on ne
        # perd pas le jour, on republie au plus tôt.
        run_time = now + timedelta(minutes=1)

    print(f"🎲 [PLAN DU JOUR] {dressing} — planifié à {run_time.strftime('%H:%M:%S')} "
          f"(niveau : {plan['niveau']}, volume cible : {plan['volume_cible']}).")

    job_func = _execute_republish_midi if creneau == "midi" else _execute_republish_soir
    scheduler.add_job(
        job_func,
        DateTrigger(run_date=run_time),
        args=[plan["volume_cible"]],
        id=job_id,
        replace_existing=True,
    )


async def generer_plans_du_jour_cron():
    """
    Job de minuit : recalcule la hiérarchie de décision anti-détection
    (convalescence à paliers > repos forcé > calendrier aléatoire) pour CHAQUE
    dressing, indépendamment chaque jour -- remplace les anciens créneaux fixes
    (11h30-12h30 / 17h00-18h30), mécaniquement prévisibles. Si un dressing est
    actif aujourd'hui (quel que soit le niveau qui l'a décidé), planifie le
    job de republication réel à l'horaire calculé par le plan.
    """
    plan_d1, plan_d2 = generer_plans_du_jour()

    for dressing, plan, creneau, job_id in [
        ("Dressing 1", plan_d1, "midi", "republish_oneshot_d1"),
        ("Dressing 2", plan_d2, "soir", "republish_oneshot_d2"),
    ]:
        _planifier_republish_oneshot(dressing, plan, job_id, creneau)


async def rattraper_republish_manques():
    """
    Rattrapage démarrage (cf. échange du 21/09/2026) : contrairement au cas
    "aucun plan du jour" déjà géré dans startup_event(), ce cas-ci concerne un
    plan qui EXISTE bien pour aujourd'hui (généré à minuit, dressing actif,
    horaire tiré) mais dont le job APScheduler associé a été perdu par un
    redémarrage intervenu entre la génération et l'horaire -- silencieusement,
    puisque le jobstore par défaut n'est pas persisté sur disque. Sans ce
    filet, un dressing actif au tirage peut ne JAMAIS republier de la journée
    si le backend redémarre ne serait-ce qu'une fois avant son horaire.
    Ne rattrape que si aucune republication n'a déjà été loguée pour ce
    dressing depuis la génération du plan (le job a peut-être eu le temps de
    s'exécuter avant le redémarrage -- pas la peine de doubler la mise).
    """
    from services.automation_scheduler import get_plan_du_jour
    from services.risk_guard import get_volume_depuis_minuit

    # Second cas distinct, découvert le même jour : la garantie de continuité
    # ("au moins un dressing actif") ne s'applique normalement qu'au cron de
    # minuit -- si un dressing bascule en repos EN COURS DE JOURNÉE (ex: une
    # action manuelle fait franchir le seuil de volume glissant) alors que
    # l'AUTRE était déjà en repos depuis minuit, les deux se retrouvent au
    # repos le même jour sans que la garantie n'ait jamais eu l'occasion de
    # se prononcer (rafraichir_pause_si_necessaire() la rejoue désormais en
    # direct pour toute NOUVELLE bascule, mais ne corrige pas rétroactivement
    # un état déjà figé comme celui-ci). On la réapplique ici au démarrage.
    plan_d1 = get_plan_du_jour("Dressing 1")
    plan_d2 = get_plan_du_jour("Dressing 2")
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    if (plan_d1 and plan_d2 and plan_d1.get("date") == aujourdhui and plan_d2.get("date") == aujourdhui
            and not plan_d1.get("actif") and not plan_d2.get("actif")):
        from services.planification_republication import _garantir_un_dressing_actif, _persister_decision, _replanifier_job_oneshot
        decisions = {
            "Dressing 1": {"niveau": plan_d1["niveau"], "actif": False, "horaire": None, "volume_cible": 0, "convalescence": plan_d1.get("convalescence")},
            "Dressing 2": {"niveau": plan_d2["niveau"], "actif": False, "horaire": None, "volume_cible": 0, "convalescence": plan_d2.get("convalescence")},
        }
        nouvelle_d1, nouvelle_d2 = _garantir_un_dressing_actif(
            "Dressing 1", decisions["Dressing 1"], "Dressing 2", decisions["Dressing 2"], "republication"
        )
        for dr, nouvelle in [("Dressing 1", nouvelle_d1), ("Dressing 2", nouvelle_d2)]:
            if nouvelle is not decisions[dr]:
                print(f"🩹 [STARTUP] Continuité : les deux dressings étaient en repos aujourd'hui "
                      f"— {dr} réactivé (mode continuité) faute de meilleure option.")
                plan_persiste = _persister_decision(dr, nouvelle)
                _replanifier_job_oneshot(dr, plan_persiste)

    for dressing, creneau, job_id in [
        ("Dressing 1", "midi", "republish_oneshot_d1"),
        ("Dressing 2", "soir", "republish_oneshot_d2"),
    ]:
        plan = get_plan_du_jour(dressing)
        if plan is None or not plan.get("actif") or not plan.get("horaire"):
            continue
        if get_volume_depuis_minuit(dressing, "republication") > 0:
            continue  # déjà fait aujourd'hui -- le job a dû tourner avant le redémarrage

        print(f"🩹 [STARTUP] Job de republication manquant pour {dressing} "
              f"(perdu par un redémarrage précédent, plan pourtant actif à "
              f"{plan['horaire']}) — replanification.")
        _planifier_republish_oneshot(dressing, plan, job_id, creneau)


async def run_cron_sync(slot):
    """
    Vérifie le toggle "Scraping automatique" (page Maintenance) avant de lancer.
    Un déclenchement manuel du scraping (bouton "Lancer Scraping" du dashboard)
    n'est pas affecté par ce toggle -- celui-ci ne coupe que les 2 syncs
    planifiées (14h/22h), jamais le contrôle manuel explicite.
    """
    from services.maintenance_service import maintenance_service
    if not maintenance_service.scraping_auto_active:
        print(f"⏭️  [SCRAPING] Synchronisation automatique désactivée (Maintenance) — {slot} ignorée.")
        return

    print(f"🕒 [CRON] Démarrage de la synchronisation de {slot}...")
    try:
        await scraper.run_full_sync()
        # Snapshot des scores après chaque sync
        from services.supabase_service import SupabaseService
        svc = SupabaseService()
        nb = svc.capture_score_snapshots()
        print(f"📸 [CRON] {nb} snapshots de score enregistrés")
    except Exception as e:
        print(f"❌ [CRON] Échec de la synchronisation de {slot} : {e}")


async def _execute_republish_midi(volume_cible=None):
    await _execute_republish("midi", volume_cible)

async def _execute_republish_soir(volume_cible=None):
    await _execute_republish("soir", volume_cible)

async def _execute_republish(creneau="soir", volume_cible=None):
    """
    volume_cible : plafond décidé par le plan du jour (niveau 3 de la
    hiérarchie anti-détection, cf. planification_republication.py) -- None si
    déclenché autrement qu'via generer_plans_du_jour_cron() (aucune limite
    supplémentaire dans ce cas, seul le quota risk_guard s'applique).
    """
    from services.clemz_automation import ClemzAutomation

    task = get_scheduled_republish(creneau)
    if not task or task.get("status") != "pending":
        print(f"⏭️  [REPUB {creneau.upper()}] Liste absente ou déjà traitée.")
        return

    items = task.get("items", [])
    if not items:
        print(f"⚠️  [REPUB {creneau.upper()}] Liste vide, rien à faire.")
        return

    print(f"🚀 [REPUB {creneau.upper()}] Lancement — {len(items)} article(s).")
    mark_scheduled_republish_status("running", creneau)
    task_id = start_task_run(f"republication_{creneau}", items)

    if volume_cible is not None and volume_cible < len(items):
        exclus_volume = items[volume_cible:]
        items = items[:volume_cible]
        print(f"🎯 [REPUB {creneau.upper()}] Volume cible du plan du jour : {volume_cible} "
              f"— {len(exclus_volume)} article(s) reporté(s).")
        for item in exclus_volume:
            update_task_result(
                task_id, item["id"], status="skipped",
                reason=f"Volume cible du jour atteint ({volume_cible}) — calendrier anti-détection.",
            )

    try:
        from services.risk_guard import get_allowed_quantity, log_action

        produits_d1 = [i["nom"] for i in items if i.get("dressing") == "Dressing 1"]
        produits_d2 = [i["nom"] for i in items if i.get("dressing") == "Dressing 2"]

        allowed_d1, truncated_d1, reason_d1 = get_allowed_quantity("Dressing 1", "republication", len(produits_d1))
        allowed_d2, truncated_d2, reason_d2 = get_allowed_quantity("Dressing 2", "republication", len(produits_d2))

        excluded_d1 = produits_d1[allowed_d1:]
        excluded_d2 = produits_d2[allowed_d2:]
        produits_d1 = produits_d1[:allowed_d1]
        produits_d2 = produits_d2[:allowed_d2]

        if truncated_d1:
            add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 1", reason_d1)
            for nom in excluded_d1:
                matched = next((i for i in items if i["nom"] == nom), None)
                if matched:
                    update_task_result(task_id, matched["id"], status="skipped", reason="Quota anti-détection atteint — reporté au prochain cycle.")
        if truncated_d2:
            add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 2", reason_d2)
            for nom in excluded_d2:
                matched = next((i for i in items if i["nom"] == nom), None)
                if matched:
                    update_task_result(task_id, matched["id"], status="skipped", reason="Quota anti-détection atteint — reporté au prochain cycle.")

        automation = ClemzAutomation(produits_d1, produits_d2)
        results = await automation.run()

        # CORRIGÉ (02/10/2026) : log_action() comptait jusqu'ici len(produits_d1)/
        # len(produits_d2) -- le nombre ENVOYÉ à Clemz, pas le nombre RÉELLEMENT
        # réussi sur Vinted. Même bug déjà corrigé le 21/09/2026 côté run_now_task()
        # (bouton manuel du dashboard) et le 02/10/2026 côté run_baisse_prix_auto(),
        # mais jamais reporté ici -- alors que c'est la tâche qui s'exécute TOUS LES
        # JOURS automatiquement (horaire tiré 11h-19h), sans aucune intervention
        # manuelle pour remarquer un comptage faussé par un échec partiel Clemz.
        traites_d1 = traites_d2 = 0
        for account_result in results:
            nb_succes = sum(
                1 for r in account_result.get("selection_results", []) if r.get("status") == "success"
            )
            if "Dressing 1" in account_result.get("account", ""):
                traites_d1 += nb_succes
            elif "Dressing 2" in account_result.get("account", ""):
                traites_d2 += nb_succes

        log_action("Dressing 1", "republication", traites_d1)
        log_action("Dressing 2", "republication", traites_d2)
        from services.planification_republication import rafraichir_pause_si_necessaire
        rafraichir_pause_si_necessaire("Dressing 1")
        rafraichir_pause_si_necessaire("Dressing 2")
        for account_result in results:
            for item_result in account_result.get("selection_results", []):
                matched = next((i for i in items if i["nom"] == item_result["nom"]), None)
                item_id = matched["id"] if matched else item_result["nom"]
                update_task_result(task_id, item_id, status=item_result["status"], reason=item_result.get("reason"))

            repost = account_result.get("repost_result")
            if repost and repost["status"] == "failed":
                print(f"⚠️  [REPUB] {account_result['account']} — repost échoué : {repost['reason']}")
                add_task_global_anomaly(
                    task_id,
                    label="Échec navigation 'Voir mes listes'",
                    dressing=account_result["account"],
                    reason=repost["reason"],
                )

            # Erreurs majeures Clemz ignorées en cours de route (ancienne annonce
            # supprimée, nouvelle jamais créée) -- possibles même quand repost_result
            # est globalement "success", puisque l'article en erreur est simplement
            # ignoré et le reste du batch continue.
            for erreur in (repost or {}).get("erreurs_majeures", []):
                add_task_global_anomaly(
                    task_id,
                    label=f"Erreur majeure ignorée : {erreur['nom']}",
                    dressing=account_result["account"],
                    reason=f"Ancienne annonce supprimée, nouvelle non créée — {erreur.get('url') or 'URL inconnue'}. Intervention manuelle nécessaire.",
                )

    except Exception as e:
        print(f"❌ [REPUB {creneau.upper()}] Erreur : {e}")
        for item in items:
            update_task_result(task_id, item["id"], status="failed", reason=f"Erreur fatale : {str(e)[:120]}")

    finally:
        finish_task_run(task_id)
        mark_scheduled_republish_status("done", creneau)
        print(f"✅ [REPUB {creneau.upper()}] Tâche terminée.")

    # Le partage vues/favoris se déclenche désormais après CETTE unique
    # republication (fenêtre 14h-19h), peu importe l'heure exacte à laquelle
    # elle se termine -- et uniquement si elle a réussi (au moins partiellement).
    # Généralisé sur `creneau` plutôt que hardcodé sur "soir" : comme "soir"
    # reste toujours vide (early return plus haut dans la fonction), ce bloc
    # n'est de toute façon jamais atteint pour ce créneau-là.
    from services.automation_scheduler import get_task_history

    history = get_task_history(limit=200, task_type=f"republication_{creneau}")
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_entry = next((h for h in history if h["task_id"].startswith(f"republication_{creneau}_{today_str}")), None)
    repost_ok = current_entry is not None and current_entry.get("global_status") in ("success", "partial_success")

    from services.maintenance_service import maintenance_service

    if not maintenance_service.partage_vues_favoris_auto_active:
        print("⏭️  [PARTAGE] Automatisation désactivée (Maintenance) — on passe.")
    elif repost_ok:
        partage_history = get_task_history(limit=50, task_type="partage_vues_favoris")
        already_run_today = any(
            h["task_id"].startswith(f"partage_vues_favoris_{today_str}")
            and h.get("global_status") in ("success", "partial_success")
            for h in partage_history
        )
        if already_run_today:
            print("⏭️  [PARTAGE] Déjà exécuté avec succès aujourd'hui, on passe.")
        else:
            await run_partage_vues_favoris_task()
    else:
        print("⏭️  [PARTAGE] Repost non réussi — partage vues/favoris non déclenché aujourd'hui.")


async def run_partage_vues_favoris_task(dressing: str = None):
    """
    Lance le partage des vues et des favoris (Clemz). dressing=None traite les
    deux comptes (auto planifiée) ; dressing="Dressing 1"/"Dressing 2" ne traite
    que ce compte (déclenchement manuel indépendant depuis le dashboard).
    """
    from services.clemz_partage import ClemzPartageVuesFavoris

    start_time = datetime.now()
    print(f"🚀 [PARTAGE] Lancement à {start_time.strftime('%H:%M:%S')} ({dressing or 'les deux comptes'})...")
    task_id = start_partage_vues_favoris_run()

    try:
        automation = ClemzPartageVuesFavoris(dressing=dressing)
        results = await automation.run()
    except Exception as e:
        print(f"❌ [PARTAGE] Erreur fatale : {e}")
        results = [
            {
                "account": acc["name"],
                "vues": {"status": "failed", "reason": f"Erreur fatale : {str(e)[:150]}"},
                "favoris": {"status": "failed", "reason": f"Erreur fatale : {str(e)[:150]}"},
            }
            for acc in automation.accounts
        ]

    finish_partage_vues_favoris_run(task_id, results)

    end_time = datetime.now()
    estimated_deadline = start_time.replace(hour=19, minute=15, second=0, microsecond=0)
    if end_time > estimated_deadline:
        print(f"⚠️  [PARTAGE] Terminé à {end_time.strftime('%H:%M:%S')} — dépassement du 19h15 informatif constaté.")
    else:
        print(f"✅ [PARTAGE] Terminé à {end_time.strftime('%H:%M:%S')}.")

async def run_baisse_prix_auto(article_ids_restriction=None):
    """
    Identifie les articles en Mauvaise Performance, les répartit en lots
    -10%/-20% selon le sous-cas déjà calculé par get_processed_inventory,
    exclut les articles à plancher atteint (aucune baisse) et ceux en audit
    (3+ republications sans vente), applique un cooldown de 7 jours
    (price_drops -- pas de double baisse rapprochée sur un même article). Les
    deux lots sont envoyés à Clemz l'UN APRÈS L'AUTRE (jamais en parallèle --
    chaque lot lance ses propres navigateurs sur les mêmes sessions partagées
    Dressing 1/2).

    article_ids_restriction : si fourni (set/list d'IDs), restreint le calcul à
    CES articles précis -- utilisé par le déclenchement manuel depuis le
    dashboard pour respecter la sélection/les exclusions faites par
    l'utilisateur (bouton "Forcer baisse de prix"), plutôt que de reprendre tout
    l'univers "Mauvaise Perf" comme le fait le cron quotidien (None = pas de
    restriction).

    Un seul historique (task_history, type='baisse_prix') est créé pour tout le
    run, quel que soit le nombre de lots -- le pourcentage réellement appliqué à
    chaque produit apparaît dans le champ 'taux' et dans le 'reason' de succès.
    """
    from services.supabase_service import SupabaseService
    from services.clemz_automation import ClemzAutomation

    svc = SupabaseService()
    articles = await svc.get_processed_inventory()

    candidats = [a for a in articles if not a["is_done"] and not a["is_vendu"]]
    if article_ids_restriction is not None:
        restriction = set(article_ids_restriction)
        candidats = [a for a in candidats if a["id"] in restriction]

    tous_les_ids = [a["id"] for a in candidats if a["baisse_prix_taux"] is not None]
    deja_baisses_recemment = svc.get_articles_baisses_recentes(tous_les_ids, jours_cooldown=7)

    # Regroupe par taux EFFECTIF -- généralisé le 19/09/2026 pour supporter les
    # tranches de prix de la mauvaise perf (10/15/20%, cf.
    # _get_taux_baisse_low_perf), plutôt que les deux lots figés -10%/-20%
    # d'avant. Le reste de la fonction (quota, cooldown, lancement séquentiel)
    # ne change pas de logique, juste le nombre de groupes possibles.
    lots_par_taux = {}
    for a in candidats:
        taux = a["baisse_prix_taux"]
        if taux is None or a["id"] in deja_baisses_recemment:
            continue
        lots_par_taux.setdefault(taux, []).append(a)

    nb_exclus_cooldown = sum(
        1 for a in candidats if a["baisse_prix_taux"] is not None and a["id"] in deja_baisses_recemment
    )
    detail_taux = ", ".join(f"{len(lot)} à -{taux}%" for taux, lot in sorted(lots_par_taux.items()))
    print(f"💸 [BAISSE PRIX] {detail_taux or 'aucun'}"
          f" ({nb_exclus_cooldown} exclu(s), déjà baissés dans les 7 derniers jours).")

    if not lots_par_taux:
        print("💸 [BAISSE PRIX] Aucun article à traiter (rien en attente, tout en cooldown, ou sélection vide).")
        return

    # --- Quota anti-détection (risk_guard.py) -- jusqu'ici jamais vérifié pour
    # la baisse de prix (seule la republication passait par get_allowed_quantity),
    # alors que c'est la même automatisation Clemz, donc le même risque de
    # détection. Un seul quota par dressing, PARTAGÉ entre tous les taux (pas un
    # quota par taux) -- calculé sur le total demandé avant de répartir en lots.
    from services.risk_guard import get_allowed_quantity, log_action

    tous_les_demandes = [a for lot in lots_par_taux.values() for a in lot]
    demandes_d1 = [a for a in tous_les_demandes if a["dressing"] == "Dressing 1"]
    demandes_d2 = [a for a in tous_les_demandes if a["dressing"] == "Dressing 2"]

    allowed_d1, truncated_d1, reason_d1 = get_allowed_quantity("Dressing 1", "baisse_prix", len(demandes_d1))
    allowed_d2, truncated_d2, reason_d2 = get_allowed_quantity("Dressing 2", "baisse_prix", len(demandes_d2))

    ids_autorises = {a["id"] for a in demandes_d1[:allowed_d1]} | {a["id"] for a in demandes_d2[:allowed_d2]}
    exclus_quota = [a for a in tous_les_demandes if a["id"] not in ids_autorises]

    lots_par_taux = {
        taux: [a for a in lot if a["id"] in ids_autorises]
        for taux, lot in lots_par_taux.items()
    }

    tous_les_items = (
        [{"id": a["id"], "nom": a["nom"], "dressing": a["dressing"], "taux": taux} for taux, lot in lots_par_taux.items() for a in lot]
        + [{"id": a["id"], "nom": a["nom"], "dressing": a["dressing"], "taux": a["baisse_prix_taux"]} for a in exclus_quota]
    )

    task_id = start_task_run("baisse_prix", tous_les_items)

    if truncated_d1:
        add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 1", reason_d1)
    if truncated_d2:
        add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 2", reason_d2)
    for a in exclus_quota:
        update_task_result(task_id, a["id"], status="skipped", reason="Quota anti-détection atteint — reporté au prochain cycle.")

    # CORRIGÉ (02/10/2026) : le quota comptait jusqu'ici allowed_d1/allowed_d2 --
    # le nombre d'items ADMIS par le contrôle de quota en amont, pas le nombre
    # RÉELLEMENT réussi sur Vinted. C'est exactement le même bug déjà corrigé le
    # 21/09/2026 côté run_now_task() (routes/inventory.py) pour la republication,
    # mais jamais reporté ici pour la baisse de prix automatique quotidienne --
    # un échec partiel Clemz (déjà observé : "panneau perdu", message "X non
    # republiés") gonflait silencieusement le quota anti-détection sans qu'aucune
    # baisse n'ait réellement eu lieu.
    traites_d1 = traites_d2 = 0
    for taux, lot in sorted(lots_par_taux.items()):
        if not lot:
            continue
        produits_d1 = [a["nom"] for a in lot if a["dressing"] == "Dressing 1"]
        produits_d2 = [a["nom"] for a in lot if a["dressing"] == "Dressing 2"]
        print(f"🚀 [BAISSE PRIX] Lancement du lot -{taux}% — {len(lot)} article(s).")
        try:
            automation = ClemzAutomation(produits_d1, produits_d2, task_type="baisse_prix", pourcentage_baisse_prix=taux)
            results = await automation.run()

            for account_result in results:
                nb_succes = sum(
                    1 for r in account_result.get("selection_results", []) if r.get("status") == "success"
                )
                if "Dressing 1" in account_result.get("account", ""):
                    traites_d1 += nb_succes
                elif "Dressing 2" in account_result.get("account", ""):
                    traites_d2 += nb_succes

                for item_result in account_result.get("selection_results", []):
                    matched = next((a for a in lot if a["nom"] == item_result["nom"]), None)
                    item_id = matched["id"] if matched else item_result["nom"]
                    reason = item_result.get("reason")
                    if item_result["status"] == "success":
                        reason = f"Baisse -{taux}% appliquée"
                    update_task_result(task_id, item_id, status=item_result["status"], reason=reason)

                # Erreurs majeures Clemz ignorées en cours de route (ancienne annonce
                # supprimée, nouvelle jamais créée) -- possibles même quand le lot
                # se termine globalement en succès.
                repost = account_result.get("repost_result") or {}
                for erreur in repost.get("erreurs_majeures", []):
                    add_task_global_anomaly(
                        task_id,
                        label=f"Erreur majeure ignorée (-{taux}%) : {erreur['nom']}",
                        dressing=account_result["account"],
                        reason=f"Ancienne annonce supprimée, nouvelle non créée — {erreur.get('url') or 'URL inconnue'}. Intervention manuelle nécessaire.",
                    )
        except Exception as e:
            print(f"❌ [BAISSE PRIX] Erreur sur le lot -{taux}% : {e}")
            for a in lot:
                update_task_result(task_id, a["id"], status="failed", reason=f"Erreur lot -{taux}% : {str(e)[:100]}")

    log_action("Dressing 1", "baisse_prix", traites_d1)
    log_action("Dressing 2", "baisse_prix", traites_d2)
    from services.planification_republication import rafraichir_pause_si_necessaire
    rafraichir_pause_si_necessaire("Dressing 1")
    rafraichir_pause_si_necessaire("Dressing 2")

    finish_task_run(task_id)
    print("✅ [BAISSE PRIX] Traitement terminé.")

# Baisse de prix automatique quotidienne -- déclenchée après la sync de 14h
# (données fraîches) et avant la fenêtre de republication 14h-19h (qui tire son
# heure aléatoire dans cette même fenêtre, laissant de la marge).
async def _run_baisse_prix_auto_cron():
    """
    Wrapper appelé uniquement par le cron quotidien -- vérifie le toggle
    "Baisse de prix automatique" (page Maintenance) avant de lancer. Le
    déclenchement MANUEL ("Forcer baisse de prix" depuis le dashboard) appelle
    run_baisse_prix_auto() directement et n'est donc jamais affecté par ce
    toggle : celui-ci ne coupe que l'automatisation quotidienne, pas le
    contrôle manuel explicite de l'utilisateur.
    """
    from services.maintenance_service import maintenance_service
    if not maintenance_service.baisse_prix_auto_active:
        print("⏭️  [BAISSE PRIX] Automatisation quotidienne désactivée (Maintenance) — on passe.")
        return
    await run_baisse_prix_auto()


async def _check_watchdog_health_cron():
    """
    Vérification périodique (toutes les 30 min) de la santé du watchdog
    'Messages automatiques' Clemz -- le relance si l'utilisateur le veut actif
    (toggle Maintenance) mais que la tâche est tombée en erreur/arrêtée de façon
    inattendue. Respecte toujours un arrêt volontaire de l'utilisateur.
    """
    from services.maintenance_service import maintenance_service
    maintenance_service.check_and_restart_watchdog_if_needed()


scheduler.add_job(
    _check_watchdog_health_cron,
    IntervalTrigger(minutes=30),
    id="watchdog_health_check",
    replace_existing=True,
)


async def _verifier_plan_du_jour_cron():
    """
    Filet de sécurité périodique (cf. échange du 24/09/2026) : le job de
    minuit (generer_plans_du_jour, ci-dessous) ne se rattrape PAS tout seul
    s'il est manqué -- un PC en veille toute la nuit (constaté le 24/09,
    aucune activité de 23h53 à 07h38) fait sauter purement et simplement le
    tirage du jour, sans qu'aucun plan valide n'existe jusqu'au prochain
    redémarrage du backend. Ce trou est risqué : tant qu'aucun plan n'existe,
    _plafonner_par_plan_du_jour() ne plafonne plus RIEN (seul le quota brut
    de 15/jour s'applique), perdant tout l'effet du calendrier anti-détection
    et du mode continuité tant qu'il dure.

    Rejoue exactement la même condition que le rattrapage de démarrage
    (main.py, startup_event) -- si un plan manque pour aujourd'hui pour au
    moins un dressing, régénère LES DEUX (comme le fait déjà ce rattrapage),
    plutôt que d'introduire une politique différente pour ce cas.
    """
    from services.automation_scheduler import get_plan_du_jour
    if get_plan_du_jour("Dressing 1") is None or get_plan_du_jour("Dressing 2") is None:
        print("🩹 [PLAN DU JOUR] Plan manquant détecté en cours de journée (vérification périodique) — génération de rattrapage.")
        await generer_plans_du_jour_cron()


scheduler.add_job(
    _verifier_plan_du_jour_cron,
    IntervalTrigger(minutes=15),
    id="verifier_plan_du_jour",
    replace_existing=True,
)


# Baisse de prix automatique quotidienne -- déclenchée après la sync de 14h
# (données fraîches) et avant la fenêtre de republication 14h-19h (qui tire son
# heure aléatoire dans cette même fenêtre, laissant de la marge).
scheduler.add_job(
    _run_baisse_prix_auto_cron,
    CronTrigger(hour=14, minute=5),
    id="baisse_prix_auto",
    replace_existing=True,
)

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

# Job de minuit : recalcule la hiérarchie de décision anti-détection
# (convalescence > repos forcé > calendrier aléatoire) pour D1 et D2, et
# planifie les jobs de republication réels aux horaires calculés du jour --
# remplace les deux anciens déclencheurs à fenêtre fixe (11h-19h codé en dur).
scheduler.add_job(
    generer_plans_du_jour_cron,
    CronTrigger(hour=0, minute=5),
    id="generer_plans_du_jour",
    replace_existing=True,
)