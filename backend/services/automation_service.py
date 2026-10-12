# services/automation_service.py
import asyncio
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from services.vinted_scraper import VintedScraper

from services.automation_scheduler import (
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

# Un job de republication par dressing, à l'horaire de son plan du jour.
JOB_ID_REPUBLICATION = {"Dressing 1": "republication_dressing1", "Dressing 2": "republication_dressing2"}


def _planifier_republish_oneshot(dressing, plan):
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
    from services.plan_source import est_satellite
    if est_satellite():
        return  # le satellite ne programme jamais de republication (la VM fait foi)
    if not plan["actif"] or not plan["horaire"]:
        print(f"⏭️  [PLAN DU JOUR] {dressing} — repos aujourd'hui (niveau : {plan['niveau']}).")
        return

    heure, minute, seconde = (int(x) for x in plan["horaire"].split(":"))
    now = datetime.now()
    run_time = now.replace(hour=heure, minute=minute, second=seconde, microsecond=0)
    if run_time <= now:
        # Garde-fou (le job tourne à minuit normalement, les horaires tirés
        # sont dans la journée, donc toujours dans le futur) -- mais aussi le
        # chemin normal du rattrapage démarrage quand l'horaire tiré est déjà
        # passé : on ne perd pas le jour, on republie au plus tôt.
        run_time = now + timedelta(minutes=1)

    print(f"🎲 [PLAN DU JOUR] {dressing} — planifié à {run_time.strftime('%H:%M:%S')} "
          f"(niveau : {plan['niveau']}, volume cible : {plan['volume_cible']}).")

    scheduler.add_job(
        _republication_chainee,
        DateTrigger(run_date=run_time),
        args=[dressing, plan["volume_cible"]],
        id=JOB_ID_REPUBLICATION[dressing],
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
    from services.plan_source import est_satellite
    if est_satellite():
        print("⏭️  [PLAN DU JOUR] Satellite : aucun tirage local, plan lu sur la VM.")
        return
    plan_d1, plan_d2 = generer_plans_du_jour()

    for dressing, plan in [("Dressing 1", plan_d1), ("Dressing 2", plan_d2)]:
        _planifier_republish_oneshot(dressing, plan)


async def rattraper_republish_manques(reprendre=False):
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
    from services.plan_source import est_satellite
    if est_satellite():
        return  # aucun rattrapage sur le satellite : la VM fait foi
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
    # Pas de continuité si l'un des deux a déjà republié aujourd'hui (cf.
    # échange du 12/10/2026, même garde que rafraichir_pause_si_necessaire) :
    # un redémarrage après une republication suivie d'un repos_force ne doit
    # pas réactiver l'autre dressing.
    deja_republie = any(get_volume_depuis_minuit(d, "republication") > 0 for d in ("Dressing 1", "Dressing 2"))
    if (not deja_republie and plan_d1 and plan_d2 and plan_d1.get("date") == aujourdhui and plan_d2.get("date") == aujourdhui
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

    # DÉMARRAGE PRUDENT (12/10/2026) : ne reprogramme automatiquement QUE la
    # republication jamais commencée aujourd'hui. Une republication en échec,
    # bloquée ou interrompue (coupée par l'arrêt du serveur) attend "Reprendre
    # la routine" (reprendre=True) -- une republication arrêtée à la main
    # n'est jamais relancée. Avant, toute republication sans quota enregistré
    # repartait au redémarrage, y compris celle que l'utilisateur venait de
    # couper. Le volume déjà fait est déduit dans _execute_republish.
    from services.automation_scheduler import get_statut_etape, STATUTS_ETAPE_A_REPRENDRE
    for dressing in ("Dressing 1", "Dressing 2"):
        plan = get_plan_du_jour(dressing)
        if plan is None or not plan.get("actif") or not plan.get("horaire"):
            continue
        statut = get_statut_etape(dressing, "republication")
        if statut is not None and not (reprendre and statut in STATUTS_ETAPE_A_REPRENDRE):
            continue

        print(f"🩹 [ROUTINE] Republication {dressing} "
              f"{'à reprendre (' + statut + ')' if statut else 'non encore faite aujourd’hui'} "
              f"— plan actif à {plan['horaire']}, replanification.")
        _planifier_republish_oneshot(dressing, plan)


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


def _republication_auto_desactivee(dressing):
    """
    Interrupteur "Republication automatique" (Maintenance, 10/10/2026) --
    vérifié ICI, au point d'entrée des jobs planifiés (plan du jour et
    rattrapage au démarrage passent tous deux par _planifier_republish_oneshot
    -> _republication_chainee), et PAS dans _execute_republish() lui-même.
    """
    from services.maintenance_service import maintenance_service
    if not maintenance_service.republication_auto_active:
        print(f"⏭️  [REPUB] {dressing} — republication automatique désactivée (Maintenance) — on passe.")
        return True
    return False

# =============================================================================
# CHAÎNE DE ROUTINE PAR DRESSING (cf. échange du 12/10/2026)
# =============================================================================
# Pour CHAQUE dressing, indépendamment : scraping -> baisse de prix ->
# republication. Une étape ne se lance que si la précédente a RÉUSSI pour ce
# dressing (succès partiel = OK, "rien à faire" / bascule désactivée / repos /
# arrêtée à la main = OK). Un échec arrête la suite de CE dressing seulement
# (l'autre continue), avec un e-mail. Reprise d'une étape en échec, bloquée ou
# interrompue UNIQUEMENT via le bouton "Reprendre la routine" du Journal (POST
# /api/maintenance/reprendre-routine) -- jamais au simple redémarrage du
# backend (12/10/2026 : plus de relance surprise), jamais en boucle. Le
# démarrage ne rattrape que les étapes jamais commencées (VM allumée en retard).
# Pause globale (bouton du Journal, get_pause_routine) : aucune étape ne part.
#
# Origine : VM rallumée à 18h41 le 12/10, le scraping de 10h30 et la baisse de
# 10h40 avaient été manqués (APScheduler ne rejoue pas un cron passé pendant
# que le PC était éteint), mais la republication D2 était relancée seule à
# 18h42. Les étapes manquées sont donc aussi rattrapées, dans l'ordre, avant
# toute republication.
#
# _verrou_routine sérialise toutes les étapes (crons, rattrapages,
# republications) : une republication de rattrapage planifiée à +1 min attend
# la fin du scraping/de la baisse rattrapés au lieu de les doubler.
DRESSINGS_ROUTINE = ("Dressing 1", "Dressing 2")
HEURE_SCRAPING_MATIN = (10, 30)  # = CronTrigger "sync_14h" plus bas
HEURE_BAISSE_PRIX = (10, 40)     # = CronTrigger "baisse_prix_auto" plus bas
LIBELLES_ETAPES = {"scraping": "Scraping", "baisse_prix": "Baisse de prix", "republication": "Republication"}
_verrou_routine = asyncio.Lock()


def _etape_ok(dressing, etape):
    from services.automation_scheduler import get_statut_etape, STATUTS_ETAPE_OK
    return get_statut_etape(dressing, etape) in STATUTS_ETAPE_OK


def _routine_en_pause(contexte):
    """Pause globale en vigueur : l'étape ne part pas et ne note AUCUN statut
    -- restée "jamais commencée", elle est rattrapée à la levée de la pause."""
    from services.automation_scheduler import get_pause_routine
    pause = get_pause_routine()
    if not pause:
        return False
    duree = "jusqu'à demain" if pause.get("mode") == "jour" else "jusqu'à reprise"
    print(f"⏸️  [ROUTINE] En pause ({duree}) — {contexte} non lancé(e).")
    return True


def _noter_et_alerter(dressing, etape, statut, raison=None):
    from services.automation_scheduler import noter_etape_routine
    noter_etape_routine(dressing, etape, statut, raison)
    if statut != "echec":
        return
    libelle = LIBELLES_ETAPES[etape]
    print(f"🛑 [ROUTINE] {dressing} — {libelle} en échec : {raison} — suite de la routine de {dressing} arrêtée.")
    try:
        from services.email_notifier import envoyer_notification_echec
        envoyer_notification_echec(
            f"Routine {dressing} arrêtée — {libelle} en échec",
            f"L'étape « {libelle} » a échoué pour {dressing} : {raison or 'raison inconnue'}.\n\n"
            f"Les étapes suivantes de {dressing} ne seront pas lancées (l'autre dressing "
            f"continue normalement). Une fois le problème corrigé (ex. reconnexion de la "
            f"session via AnyDesk), clique sur « Reprendre la routine » dans le Journal du "
            f"dashboard de la VM : la routine reprendra à cette étape.",
        )
    except Exception as e:
        print(f"⚠️ [ROUTINE] Échec envoi e-mail d'alerte : {e}")


async def _etape_scraping(slot):
    """Scraping des deux dressings (un seul run_full_sync, lecture seule) puis
    bilan PAR dressing depuis les résultats de la tâche "scraping" créée."""
    from services.maintenance_service import maintenance_service
    from services.automation_scheduler import get_task_history
    if not maintenance_service.scraping_auto_active:
        print(f"⏭️  [SCRAPING] Synchronisation automatique désactivée (Maintenance) — {slot} ignorée.")
        for dressing in DRESSINGS_ROUTINE:
            _noter_et_alerter(dressing, "scraping", "desactive")
        return

    debut = datetime.now().isoformat()
    await run_cron_sync(slot)
    tache = next(
        (h for h in get_task_history(limit=200, task_type="scraping") if (h.get("started_at") or "") >= debut),
        None,
    )
    for dressing in DRESSINGS_ROUTINE:
        resultat = next(
            (r for r in (tache or {}).get("results", [])
             if r.get("dressing") == dressing and not str(r.get("id", "")).startswith("anomaly_")),
            None,
        )
        if resultat and resultat.get("status") == "success":
            _noter_et_alerter(dressing, "scraping", "succes")
        else:
            raison = (resultat or {}).get("reason") or "Synchronisation interrompue (voir logs)"
            _noter_et_alerter(dressing, "scraping", "echec", raison)


async def _etape_baisse_prix(dressings):
    from services.maintenance_service import maintenance_service
    if not maintenance_service.baisse_prix_auto_active:
        print("⏭️  [BAISSE PRIX] Automatisation quotidienne désactivée (Maintenance) — on passe.")
        for dressing in dressings:
            _noter_et_alerter(dressing, "baisse_prix", "desactive")
        return
    try:
        bilan = await run_baisse_prix_auto(dressings=dressings)
    except Exception as e:
        print(f"❌ [BAISSE PRIX] Erreur : {e}")
        bilan = {d: ("echec", f"Erreur : {str(e)[:120]}") for d in dressings}
    for dressing in dressings:
        statut, raison = bilan.get(dressing, ("echec", "Aucun bilan retourné"))
        _noter_et_alerter(dressing, "baisse_prix", statut, raison)


async def _rattraper_sans_verrou(reprendre_echecs):
    """
    Relance, dans l'ordre, les étapes du matin manquées (horaire de cron passé,
    jamais exécutées aujourd'hui) -- et, si reprendre_echecs (bouton
    "Reprendre la routine" uniquement), aussi celles en échec, bloquées ou
    interrompues. `maintenant` est figé à l'entrée : une étape dont le cron
    n'est pas encore passé est laissée à son cron. L'appelant tient
    _verrou_routine.
    """
    from services.plan_source import est_satellite
    from services.automation_scheduler import get_statut_etape, STATUTS_ETAPE_A_REPRENDRE
    if est_satellite():
        return  # la VM fait foi, le satellite n'exécute aucune routine
    if _routine_en_pause("rattrapage des étapes du matin"):
        return

    def a_refaire(dressing, etape):
        statut = get_statut_etape(dressing, etape)
        return statut is None or (reprendre_echecs and statut in STATUTS_ETAPE_A_REPRENDRE)

    maintenant = (datetime.now().hour, datetime.now().minute)
    if maintenant >= HEURE_SCRAPING_MATIN and any(a_refaire(d, "scraping") for d in DRESSINGS_ROUTINE):
        print("🩹 [ROUTINE] Scraping du jour manquant ou en échec — (re)lancement avant la suite de la routine.")
        await _etape_scraping("rattrapage")
    if maintenant >= HEURE_BAISSE_PRIX:
        await _baisse_prix_chainee([d for d in DRESSINGS_ROUTINE if a_refaire(d, "baisse_prix")])


async def _baisse_prix_chainee(dressings_candidats):
    """Baisse de prix pour les dressings dont le scraping du jour a réussi ;
    les autres sont notés "bloqué" (pas d'e-mail : celui du scraping suffit).
    Un dressing au REPOS aujourd'hui (plan du jour inactif) n'a aucune baisse
    de prix -- règle utilisateur du 12/10/2026 : le repos vaut pour toute
    action Clemz, pas seulement la republication (D1 en repos avait reçu une
    baisse ce jour-là)."""
    from services.automation_scheduler import get_plan_du_jour, noter_etape_routine
    autorises = []
    for dressing in dressings_candidats:
        plan = get_plan_du_jour(dressing)
        if plan is not None and not plan.get("actif"):
            print(f"😴 [ROUTINE] {dressing} — au repos aujourd'hui ({plan.get('niveau')}) : pas de baisse de prix.")
            noter_etape_routine(dressing, "baisse_prix", "repos", f"Repos du jour ({plan.get('niveau')})")
            continue
        if _etape_ok(dressing, "scraping"):
            autorises.append(dressing)
        else:
            print(f"⛔ [ROUTINE] {dressing} — baisse de prix bloquée : scraping du jour non réussi.")
            noter_etape_routine(dressing, "baisse_prix", "bloque", "Scraping du jour non réussi")
    if autorises:
        await _etape_baisse_prix(autorises)


async def rattraper_routine(reprendre_echecs=False):
    async with _verrou_routine:
        await _rattraper_sans_verrou(reprendre_echecs)


async def _cron_scraping_matin():
    if _routine_en_pause("scraping de 10h30"):
        return
    async with _verrou_routine:
        await _etape_scraping("10h30")


async def _cron_baisse_prix_matin():
    from services.automation_scheduler import get_statut_etape
    if _routine_en_pause("baisse de prix de 10h40"):
        return
    async with _verrou_routine:
        # Ignore un dressing déjà traité aujourd'hui (rattrapage antérieur).
        await _baisse_prix_chainee([d for d in DRESSINGS_ROUTINE if get_statut_etape(d, "baisse_prix") is None])


async def _republication_chainee(dressing, volume_cible):
    """Job de republication automatique d'un dressing (planifié par
    _planifier_republish_oneshot à l'horaire de son plan du jour)."""
    from services.automation_scheduler import noter_etape_routine
    if _republication_auto_desactivee(dressing) or _routine_en_pause(f"republication {dressing}"):
        return
    async with _verrou_routine:
        await _rattraper_sans_verrou(reprendre_echecs=False)
        for etape in ("scraping", "baisse_prix"):
            if not _etape_ok(dressing, etape):
                raison = f"{LIBELLES_ETAPES[etape]} du jour non réussi(e)"
                print(f"⛔ [ROUTINE] {dressing} — republication bloquée : {raison}.")
                noter_etape_routine(dressing, "republication", "bloque", raison)
                return
        try:
            statut, raison = await _execute_republish(dressing, volume_cible)
        except Exception as e:
            statut, raison = "echec", f"Erreur : {str(e)[:120]}"
        _noter_et_alerter(dressing, "republication", statut, raison)
        if _etape_ok(dressing, "republication"):
            await _declencher_partage_si_dernier(dressing)


async def _execute_republish(dressing, volume_cible=None):
    """
    volume_cible : plafond décidé par le plan du jour (niveau 3 de la
    hiérarchie anti-détection, cf. planification_republication.py) -- None
    = aucune limite supplémentaire, seul le quota risk_guard s'applique.

    Tâche enregistrée en type "republication", sous-type "routine" -- le même
    type que les republications manuelles du dashboard (onglet
    "Republications" de l'historique), le sous-type les distinguant.
    """
    from services.clemz_automation import ClemzAutomation
    from services.supabase_service import SupabaseService

    # Liste construite directement depuis l'inventaire réel (cf. échange du
    # 08/10/2026), même filtre/tri que l'onglet "À republier" du dashboard
    # (frontend/src/pages/Dashboard.jsx, articlesToRepublish) : les plus
    # anciens en ligne d'abord.
    svc = SupabaseService()
    inventaire = await svc.get_processed_inventory()
    today_iso = datetime.now().strftime("%Y-%m-%d")
    eligibles = [
        a for a in inventaire
        if a.get("dressing") == dressing
        and a.get("needs_republish_action")
        and not a.get("is_vendu")
        and not (a.get("report_republication_jusqu_au") and a["report_republication_jusqu_au"] > today_iso)
    ]
    eligibles.sort(key=lambda a: a.get("jours_en_ligne") or 0, reverse=True)
    items = [{"id": a["id"], "nom": a["nom"], "dressing": a["dressing"]} for a in eligibles]

    if not items:
        print(f"⏭️  [REPUB] {dressing} — aucun article éligible aujourd'hui.")
        return ("rien_a_faire", "Aucun article éligible aujourd'hui")

    # Volume RESTANT du jour, pas la cible brute (12/10/2026) : une reprise
    # après interruption ou une republication manuelle du jour ne doit pas
    # refaire tout le volume cible -- les articles déjà faits sont comptés
    # (quota enregistré article par article).
    if volume_cible is not None:
        from services.risk_guard import get_volume_depuis_minuit
        deja_faits = get_volume_depuis_minuit(dressing, "republication")
        restant = max(0, volume_cible - deja_faits)
        if restant == 0:
            print(f"⏭️  [REPUB] {dressing} — volume du jour déjà atteint ({deja_faits}/{volume_cible}).")
            return ("rien_a_faire", f"Volume du jour déjà atteint ({deja_faits}/{volume_cible})")
    else:
        restant = None

    # Bilan de l'étape pour la chaîne de routine (cf. _republication_chainee) --
    # (statut, raison), écrasé ci-dessous selon le résultat réel.
    bilan = ("echec", "Republication interrompue (voir logs)")

    print(f"🚀 [REPUB] {dressing} — lancement, {len(items)} article(s) éligible(s).")
    task_id = start_task_run("republication", items, sous_type="routine")

    if restant is not None and restant < len(items):
        exclus_volume = items[restant:]
        items = items[:restant]
        print(f"🎯 [REPUB] {dressing} — volume restant du jour : {restant} (cible {volume_cible}) "
              f"— {len(exclus_volume)} article(s) reporté(s).")
        for item in exclus_volume:
            update_task_result(
                task_id, item["id"], status="skipped",
                reason=f"Volume cible du jour atteint ({volume_cible}) — calendrier anti-détection.",
            )

    try:
        from services.risk_guard import get_allowed_quantity

        produits_d1 = [i["nom"] for i in items if i.get("dressing") == "Dressing 1"]
        produits_d2 = [i["nom"] for i in items if i.get("dressing") == "Dressing 2"]

        allowed_d1, truncated_d1, reason_d1 = get_allowed_quantity("Dressing 1", "republication", len(produits_d1))
        allowed_d2, truncated_d2, reason_d2 = get_allowed_quantity("Dressing 2", "republication", len(produits_d2))

        excluded_d1 = produits_d1[allowed_d1:]
        excluded_d2 = produits_d2[allowed_d2:]
        produits_d1 = produits_d1[:allowed_d1]
        produits_d2 = produits_d2[:allowed_d2]

        if truncated_d1:
            add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 1", reason_d1, category="quota")
            for nom in excluded_d1:
                matched = next((i for i in items if i["nom"] == nom), None)
                if matched:
                    update_task_result(task_id, matched["id"], status="skipped", reason="Quota anti-détection atteint — reporté au prochain cycle.")
        if truncated_d2:
            add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 2", reason_d2, category="quota")
            for nom in excluded_d2:
                matched = next((i for i in items if i["nom"] == nom), None)
                if matched:
                    update_task_result(task_id, matched["id"], status="skipped", reason="Quota anti-détection atteint — reporté au prochain cycle.")

        # Quota anti-détection, dates en base et résultats de la tâche :
        # enregistrés par ClemzAutomation article par article (12/10/2026).
        automation = ClemzAutomation(produits_d1, produits_d2, task_id=task_id)
        results = await automation.run()

        traites_d1 = traites_d2 = 0
        for account_result in results:
            nb_succes = sum(
                1 for r in account_result.get("selection_results", []) if r.get("status") == "success"
            )
            if "Dressing 1" in account_result.get("account", ""):
                traites_d1 += nb_succes
            elif "Dressing 2" in account_result.get("account", ""):
                traites_d2 += nb_succes

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
                    category="erreur_clemz",
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
                    category="erreur_clemz",
                )

        nb_envoyes = len(produits_d1 if dressing == "Dressing 1" else produits_d2)
        traites = traites_d1 if dressing == "Dressing 1" else traites_d2
        arret = next((r["repost_result"]["reason"] for r in results
                      if (r.get("repost_result") or {}).get("status") == "stopped"), None)
        if nb_envoyes == 0:
            bilan = ("rien_a_faire", "Quota anti-détection atteint — aucun article envoyé")
        elif arret:
            bilan = ("arrete", f"{traites}/{nb_envoyes} article(s) republié(s) — {arret}")
        elif traites > 0:
            bilan = ("succes", f"{traites}/{nb_envoyes} article(s) republié(s)")
        else:
            echec_repost = next(
                (r["repost_result"]["reason"] for r in results
                 if (r.get("repost_result") or {}).get("status") == "failed"),
                None,
            )
            bilan = ("echec", f"0/{nb_envoyes} article(s) republié(s)" + (f" — {echec_repost}" if echec_repost else ""))

    except Exception as e:
        print(f"❌ [REPUB] {dressing} — erreur : {e}")
        for item in items:
            update_task_result(task_id, item["id"], status="failed", reason=f"Erreur fatale : {str(e)[:120]}")
        bilan = ("echec", f"Erreur fatale : {str(e)[:120]}")

    finally:
        finish_task_run(task_id)
        print(f"✅ [REPUB] {dressing} — tâche terminée.")

    return bilan


async def _declencher_partage_si_dernier(dressing):
    """
    Le partage vues/favoris est la DERNIÈRE étape de la routine journalière
    (cf. échange du 08/10/2026) -- ne se déclenche donc qu'après la
    republication RÉUSSIE (succès, même partiel, ou rien à faire) des DEUX
    dressings, jamais après la première des deux à se terminer. Comme les
    horaires sont tirés indépendamment par dressing, "le dernier à finir"
    n'est pas toujours le même -- appelé à chaque fin de republication
    réussie, on vérifie l'état de L'AUTRE dressing dans la routine du jour (12/10/2026 :
    une republication en échec/bloquée de l'autre reporte aussi le partage,
    jusqu'à la reprise de sa routine).
    """
    from services.automation_scheduler import get_task_history, get_plan_du_jour
    from services.maintenance_service import maintenance_service

    today_str = datetime.now().strftime("%Y-%m-%d")
    if not maintenance_service.partage_vues_favoris_auto_active:
        print("⏭️  [PARTAGE] Automatisation désactivée (Maintenance) — on passe.")
    elif _routine_en_pause("partage vues/favoris"):
        return
    else:
        autre_dressing = "Dressing 2" if dressing == "Dressing 1" else "Dressing 1"
        plan_autre = get_plan_du_jour(autre_dressing)
        autre_actif_aujourdhui = bool(plan_autre and plan_autre.get("actif"))

        if autre_actif_aujourdhui and not _etape_ok(autre_dressing, "republication"):
            print(f"⏭️  [PARTAGE] Republication de {autre_dressing} pas encore réussie aujourd'hui — partage reporté à la fin de SA republication.")
            return

        partage_history = get_task_history(limit=50, task_type="partage_vues_favoris")
        already_run_today = any(
            h["task_id"].startswith(f"partage_vues_favoris_{today_str}")
            and h.get("global_status") in ("success", "partial_success")
            for h in partage_history
        )
        if already_run_today:
            print("⏭️  [PARTAGE] Déjà exécuté avec succès aujourd'hui, on passe.")
        else:
            print("🏁 [PARTAGE] Dernière republication du jour terminée — lancement du partage vues/favoris.")
            await run_partage_vues_favoris_task()


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

async def run_baisse_prix_auto(article_ids_restriction=None, dressings=None):
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

    dressings : si fourni, restreint aux articles de ces dressings -- utilisé
    par la chaîne de routine (12/10/2026) pour ne pas baisser les prix d'un
    dressing dont le scraping du jour a échoué. None = les deux.

    Retourne le bilan par dressing traité, {dressing: (statut, raison)} avec
    statut "succes" (au moins un article baissé), "rien_a_faire" ou "echec".
    """
    from services.supabase_service import SupabaseService
    from services.clemz_automation import ClemzAutomation

    dressings_vises = tuple(dressings) if dressings is not None else ("Dressing 1", "Dressing 2")
    rien_a_faire = {d: ("rien_a_faire", "Aucun article à baisser") for d in dressings_vises}

    svc = SupabaseService()
    articles = await svc.get_processed_inventory()

    # --- Stratégie "prix de boucher" (activée le 13/10/2026, cf.
    # services/prix_boucher.py) : un article publié ou republié depuis moins de
    # 15 jours relève UNIQUEMENT de cette logique (marches de -10 % tous les
    # 3 jours jusqu'au plancher de sa tranche, tant qu'il n'a pas 5 vrais
    # favoris) -- jamais aussi de la "mauvaise performance", pour ne pas
    # cumuler deux baisses. Ignore est_traite : une marche marque l'article
    # "traité", ce qui bloquerait la suivante jusqu'à sa republication. Pas
    # appliqué au déclenchement manuel (article_ids_restriction), qui garde la
    # sélection faite par l'utilisateur.
    marches_boucher = []
    ids_suivis_boucher = set()
    if article_ids_restriction is None:
        from services import prix_boucher
        evaluations = await asyncio.to_thread(prix_boucher.evaluer_articles, articles)
        ids_suivis_boucher = {e["id"] for e in evaluations}
        a_baisser = [e for e in evaluations if e["statut"] == "ne_fonctionne_pas" and e["dressing"] in dressings_vises]
        recents = svc.get_articles_baisses_recentes(
            [e["id"] for e in a_baisser], jours_cooldown=prix_boucher.COOLDOWN_MARCHE_JOURS
        )
        par_id = {a["id"]: a for a in articles}
        for e in a_baisser:
            taux_marche = round((1 - e["prix_conseille"] / e["prix_actuel"]) * 100) if e["prix_actuel"] else 0
            if e["id"] in recents or taux_marche < 1:
                continue
            marches_boucher.append({**par_id[e["id"]], "baisse_prix_taux": taux_marche, "_boucher": True})
        print(f"🥩 [BAISSE PRIX] Prix de boucher : {len(marches_boucher)} marche(s) due(s) "
              f"sur {len(a_baisser)} article(s) qui ne fonctionnent pas à leur prix.")

    candidats = [a for a in articles if not a["is_done"] and not a["is_vendu"] and a["dressing"] in dressings_vises
                 and a["id"] not in ids_suivis_boucher]
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
    # Les marches "prix de boucher" passent en premier (priorité dans le quota).
    lots_par_taux = {}
    for a in marches_boucher:
        lots_par_taux.setdefault(a["baisse_prix_taux"], []).append(a)
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
        return rien_a_faire

    # --- Quota anti-détection (risk_guard.py) -- jusqu'ici jamais vérifié pour
    # la baisse de prix (seule la republication passait par get_allowed_quantity),
    # alors que c'est la même automatisation Clemz, donc le même risque de
    # détection. Un seul quota par dressing, PARTAGÉ entre tous les taux (pas un
    # quota par taux) -- calculé sur le total demandé avant de répartir en lots.
    from services.risk_guard import get_allowed_quantity

    # Tri stable : marches "prix de boucher" d'abord, quel que soit leur taux --
    # ce sont elles qui passent si le quota du jour ne suffit pas pour tout.
    tous_les_demandes = sorted(
        (a for lot in lots_par_taux.values() for a in lot), key=lambda a: not a.get("_boucher")
    )
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

    nb_boucher = sum(1 for a in tous_les_demandes if a.get("_boucher"))
    sous_type = ("prix_boucher" if nb_boucher == len(tous_les_demandes)
                 else "mauvaise_performance" if nb_boucher == 0
                 else "prix_boucher_et_mauvaise_performance")
    task_id = start_task_run("baisse_prix", tous_les_items, sous_type=sous_type)

    if truncated_d1:
        add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 1", reason_d1, category="quota")
    if truncated_d2:
        add_task_global_anomaly(task_id, "Quota anti-détection atteint", "Dressing 2", reason_d2, category="quota")
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
    #
    # SÉPARATION STRICTE PAR DRESSING (cf. échange du 08/10/2026) : les deux
    # dressings ne sont plus jamais traités dans le même appel ClemzAutomation --
    # tous les lots Dressing 1 d'abord, PUIS une pause aléatoire 5-10 min, PUIS
    # tous les lots Dressing 2. Remplace l'ancien comportement où chaque lot
    # (par taux) mélangeait les deux comptes dans un seul appel, avec une pause
    # interne à ClemzAutomation.run() seulement si les deux avaient du travail.
    async def _traiter_lots_dressing(dressing):
        """Retourne (nb articles baissés, raison d'arrêt ou None)."""
        traites = 0
        arret = None
        for taux, lot_complet in sorted(lots_par_taux.items()):
            lot = [a for a in lot_complet if a["dressing"] == dressing]
            if not lot:
                continue
            if arret:
                # Arrêt demandé pendant un lot précédent : les lots suivants de
                # CE dressing ne partent pas (la demande ne vaut que pour la
                # tâche en cours, ClemzAutomation l'efface en fin de lot).
                for a in lot:
                    update_task_result(task_id, a["id"], status="skipped", reason="Arrêtée à ta demande — lot non lancé.")
                continue
            noms = [a["nom"] for a in lot]
            produits_d1 = noms if dressing == "Dressing 1" else []
            produits_d2 = noms if dressing == "Dressing 2" else []
            print(f"🚀 [BAISSE PRIX] {dressing} — lancement du lot -{taux}% — {len(lot)} article(s).")
            try:
                # Quota et price_drops enregistrés par ClemzAutomation article par
                # article (12/10/2026).
                automation = ClemzAutomation(produits_d1, produits_d2, task_type="baisse_prix",
                                             pourcentage_baisse_prix=taux, task_id=task_id)
                results = await automation.run()
                arret = next((r["repost_result"]["reason"] for r in results
                              if (r.get("repost_result") or {}).get("status") == "stopped"), None)

                for account_result in results:
                    nb_succes = sum(
                        1 for r in account_result.get("selection_results", []) if r.get("status") == "success"
                    )
                    if dressing in account_result.get("account", ""):
                        traites += nb_succes

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
                            category="erreur_clemz",
                        )
            except Exception as e:
                print(f"❌ [BAISSE PRIX] {dressing} — erreur sur le lot -{taux}% : {e}")
                for a in lot:
                    update_task_result(task_id, a["id"], status="failed", reason=f"Erreur lot -{taux}% : {str(e)[:100]}")
        return traites, arret

    d1_a_des_lots = any(a["dressing"] == "Dressing 1" for lot in lots_par_taux.values() for a in lot)
    d2_a_des_lots = any(a["dressing"] == "Dressing 2" for lot in lots_par_taux.values() for a in lot)

    traites_d1, arret_d1 = await _traiter_lots_dressing("Dressing 1")

    if d1_a_des_lots and d2_a_des_lots:
        pause_secondes = random.uniform(300, 600)
        print(f"⏸️  [BAISSE PRIX] Pause de {pause_secondes / 60:.1f} min avant Dressing 2...")
        await asyncio.sleep(pause_secondes)

    traites_d2, arret_d2 = await _traiter_lots_dressing("Dressing 2")

    from services.planification_republication import rafraichir_pause_si_necessaire
    rafraichir_pause_si_necessaire("Dressing 1")
    rafraichir_pause_si_necessaire("Dressing 2")

    finish_task_run(task_id)
    print("✅ [BAISSE PRIX] Traitement terminé.")

    bilan = dict(rien_a_faire)
    for dressing, a_des_lots, traites, arret in (
        ("Dressing 1", d1_a_des_lots, traites_d1, arret_d1),
        ("Dressing 2", d2_a_des_lots, traites_d2, arret_d2),
    ):
        if dressing not in dressings_vises or not a_des_lots:
            continue
        nb_envoyes = sum(1 for lot in lots_par_taux.values() for a in lot if a["dressing"] == dressing)
        if arret:
            bilan[dressing] = ("arrete", f"{traites}/{nb_envoyes} article(s) baissé(s) — {arret}")
        elif traites > 0:
            bilan[dressing] = ("succes", f"{traites}/{nb_envoyes} article(s) baissé(s)")
        else:
            bilan[dressing] = ("echec", f"0/{nb_envoyes} article(s) baissé(s) — voir TaskHistory")
    return bilan

# Baisse de prix automatique quotidienne -- déclenchée à 10h40 par
# _cron_baisse_prix_matin (chaîne de routine, plus haut), après la sync de
# 10h30 et avant le premier créneau de republication (12h00). Le toggle
# "Baisse de prix automatique" (Maintenance) y est vérifié par
# _etape_baisse_prix ; le déclenchement MANUEL ("Forcer baisse de prix")
# appelle run_baisse_prix_auto() directement et n'est jamais affecté par ce
# toggle ni par la chaîne.


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
    from services.plan_source import est_satellite
    if est_satellite():
        return  # la VM fait foi, rien à vérifier ni régénérer ici

    # Journal quotidien du mode observation "prix de boucher" (13/10/2026) --
    # une fois par jour, après le scraping de 10h30 (données fraîches), quelle
    # que soit l'heure d'allumage de la VM. Idempotent.
    if (datetime.now().hour, datetime.now().minute) >= (10, 45):
        try:
            from services.prix_boucher import enregistrer_observation_du_jour
            await enregistrer_observation_du_jour()
        except Exception as e:
            print(f"⚠️ [PRIX BOUCHER] Journal d'observation non enregistré : {e}")

    from services.automation_scheduler import get_plan_du_jour
    if get_plan_du_jour("Dressing 1") is None or get_plan_du_jour("Dressing 2") is None:
        print("🩹 [PLAN DU JOUR] Plan manquant détecté en cours de journée (vérification périodique) — génération de rattrapage.")
        await generer_plans_du_jour_cron()
        return

    # Bascule en repos si une action faite DEPUIS UN AUTRE PC (satellite, qui
    # ne modifie jamais de plan) a fait franchir le seuil de volume -- le
    # volume est partagé via Supabase, mais seule la VM met son plan à jour
    # (10/10/2026). Idempotent : ne fait rien si aucune pause ne s'impose.
    from services.planification_republication import rafraichir_pause_si_necessaire
    rafraichir_pause_si_necessaire("Dressing 1")
    rafraichir_pause_si_necessaire("Dressing 2")


scheduler.add_job(
    _verifier_plan_du_jour_cron,
    IntervalTrigger(minutes=15),
    id="verifier_plan_du_jour",
    replace_existing=True,
)


# ORDRE DU SCÉNARIO JOURNALIER (cf. échange du 08/10/2026) : scraping -> baisse
# de prix -> republication, TOUJOURS dans cet ordre. Avancé de 14h à 10h30 car
# les créneaux de republication tirés au sort commencent dès 12h00
# (CRENEAUX_HORAIRES, planification_republication.py) -- un cron à 14h
# republierait APRÈS un tirage tombé dans 12h-13h, cassant l'ordre. Marge
# calculée sur des durées réelles observées (task_history.json) : scraping
# ~1,5-1,8 min pour les deux dressings, baisse de prix jusqu'à ~11 min pour un
# gros lot AVANT l'ajout de la pause 5-10 min + sauvegarde chaînée (plus long
# désormais) -- 10h30/10h40 laisse ~80 min de marge avant 12h00, largement
# suffisant même avec la marge supplémentaire. Le verrou de profil Clemz
# (ClemzAutomation.run()) reste le filet de sécurité réel si jamais la baisse
# de prix débordait malgré tout : une republication qui démarrerait pendant
# qu'il est encore tenu attend simplement son tour, elle ne se chevauche
# jamais avec une autre automatisation sur le même profil.
scheduler.add_job(
    _cron_scraping_matin,
    CronTrigger(hour=10, minute=30),
    id="sync_14h",
    replace_existing=True,
)

scheduler.add_job(
    _cron_baisse_prix_matin,
    CronTrigger(hour=10, minute=40),
    id="baisse_prix_auto",
    replace_existing=True,
)

# Sync de 22h -- conservée (fraîcheur des données avant l'extinction du
# VM à 23h), indépendante de l'ordre scraping -> baisse -> republication
# ci-dessus (purpose différent, pas liée aux créneaux de republication).
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