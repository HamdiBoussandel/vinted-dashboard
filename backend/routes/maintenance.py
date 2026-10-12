import asyncio
import logging

from fastapi import APIRouter, HTTPException, Body, UploadFile, File, Form

from services.maintenance_service import maintenance_service
from services import sync_dates_service

router = APIRouter(prefix="/api/maintenance", tags=["Maintenance"])
logger = logging.getLogger(__name__)


@router.get("/profiles")
async def get_profiles():
    """Liste les profils navigateur disponibles + leur statut courant (ferme/ouverture/ouvert)."""
    return maintenance_service.list_profiles()


@router.get("/vm-lifecycle")
async def get_vm_lifecycle_route(limit: int = 14):
    """
    Historique des démarrages/extinctions du VM (succès/échec) -- cf. échange
    du 08/10/2026, log_vm_lifecycle_event() appelé par startup_event()
    (main.py) et preparer_arret() ci-dessous. Utilisé par le Journal de
    routine du dashboard.
    """
    from services.automation_scheduler import get_vm_lifecycle_log
    return {"events": get_vm_lifecycle_log(limit=limit)}


def _compter_resultats(task, dressing):
    """
    Compte succès/total/bloqués-quota/erreurs-Clemz pour UN dressing, à partir
    des `results` d'une tâche task_history (qui peut mélanger les deux
    dressings -- scraping, baisse_prix). Exclut les lignes d'anomalie
    synthétiques (id "anomaly_*") du décompte article, les compte séparément
    par catégorie (même logique que TaskHistory.jsx, cf. échange du 08/10/2026).
    """
    resultats = task.get("results") or []
    succes = total = bloques_quota = erreurs_clemz = ecartes = 0
    for r in resultats:
        r_dressing = r.get("dressing")
        if r_dressing != dressing:
            continue
        if str(r.get("id", "")).startswith("anomaly_"):
            if r.get("category") == "quota":
                bloques_quota += 1
            else:
                erreurs_clemz += 1
            continue
        # Écartés AVANT traitement (volume cible du jour, quota) : hors du
        # total, comptés à part -- sinon "0/32" pour 7 articles réellement
        # envoyés à Clemz (cf. échange du 12/10/2026).
        if r.get("status") == "skipped":
            ecartes += 1
            continue
        total += 1
        if r.get("status") == "success":
            succes += 1
    return {"succes": succes, "total": total, "bloques_quota": bloques_quota, "erreurs_clemz": erreurs_clemz, "ecartes": ecartes}


def _dernier_plan_historique(dressing, date_str):
    """Relit plan_du_jour_historique.jsonl pour retrouver le DERNIER plan connu
    de ce dressing à cette date -- get_plan_du_jour() ne renvoie que le plan du
    jour COURANT, jamais un plan passé."""
    import json
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fichier = os.path.join(base_dir, "plan_du_jour_historique.jsonl")
    if not os.path.exists(fichier):
        return None
    dernier = None
    with open(fichier, "r", encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                entree = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            if entree.get("dressing") == dressing and entree.get("date") == date_str:
                dernier = entree
    return dernier


@router.get("/journal")
async def get_journal_route(date: str = None):
    """
    Journal de routine : agrège task_history + vm_lifecycle_log + plan_du_jour
    pour UNE journée, structuré selon le scénario métier fixé (démarrage ->
    scraping D1 -> scraping D2 -> baisse D1 -> baisse D2 -> republication D1 ->
    republication D2 -> partage vues/favoris -> extinction). Ne duplique
    aucune donnée détaillée par article -- chaque étape référence le task_id
    de TaskHistory pour le détail (cf. échange du 08/10/2026). `date` au
    format YYYY-MM-DD, défaut aujourd'hui.
    """
    from datetime import datetime as dt
    from services.automation_scheduler import get_task_history, get_vm_lifecycle_log

    date_str = date or dt.now().strftime("%Y-%m-%d")

    toutes_taches = get_task_history(limit=200)
    taches_du_jour = [t for t in toutes_taches if (t.get("started_at") or "").startswith(date_str)]

    tous_events_vm = get_vm_lifecycle_log(limit=30)
    events_du_jour = [e for e in tous_events_vm if (e.get("timestamp") or "").startswith(date_str)]

    def _premier(task_type_predicate):
        return next((t for t in taches_du_jour if task_type_predicate(t.get("type") or "")), None)

    demarrage = next((e for e in events_du_jour if e["event"] == "demarrage"), None)
    extinction = next((e for e in events_du_jour if e["event"] == "extinction"), None)
    scraping = _premier(lambda t: t == "scraping")
    baisse_prix = _premier(lambda t: t == "baisse_prix")
    partage = _premier(lambda t: t == "partage_vues_favoris")

    def _republication_routine(dressing):
        """Dernière republication AUTOMATIQUE (sous-type "routine") du jour pour
        ce dressing -- les republications manuelles du dashboard partagent le
        type "republication" mais ne font pas partie de la routine."""
        return next(
            (t for t in taches_du_jour
             if t.get("type") == "republication" and t.get("sous_type") == "routine"
             and any(r.get("dressing") == dressing for r in t.get("results") or [])),
            None,
        )

    repub_d1 = _republication_routine("Dressing 1")
    repub_d2 = _republication_routine("Dressing 2")

    def _etape_vm(event, label):
        if not event:
            return {"label": label, "statut": "inconnu", "heure": None, "detail": None}
        return {
            "label": label,
            "statut": event["status"],
            "heure": event["timestamp"],
            "detail": event.get("detail"),
        }

    # Progression live des actions Clemz en cours (12/10/2026) -- jour courant
    # seulement, attachée à l'étape "running" du dressing concerné.
    from services.clemz_automation import PROGRESSION
    est_aujourdhui = date_str == dt.now().strftime("%Y-%m-%d")

    def _etape_dressing(task, dressing, label, plan=None):
        if not task:
            return {"label": label, "dressing": dressing, "statut": "inconnu", "heure": None,
                    "task_id": None, "compteurs": None, "prevu": plan}
        compteurs = _compter_resultats(task, dressing)
        en_cours = task.get("global_status") == "running"
        return {
            "label": label,
            "dressing": dressing,
            "statut": task.get("global_status"),
            "heure": task.get("started_at"),
            "task_id": task.get("task_id"),
            "compteurs": compteurs,
            "prevu": plan,
            "detail": task.get("interruption"),
            "progression": PROGRESSION.get(dressing) if (en_cours and est_aujourdhui) else None,
        }

    def _etape_partage(task):
        """
        Dernière étape de la routine (cf. échange du 08/10/2026) -- forme de
        résultats différente des autres tâches ({account, vues:{status},
        favoris:{status}}, pas {id, nom, dressing, status}), compte donc
        séparément plutôt que de réutiliser _compter_resultats.
        """
        if not task:
            return {"label": "Partage vues/favoris", "statut": "inconnu", "heure": None, "task_id": None, "compteurs": None, "prevu": None}
        resultats = task.get("results") or []
        ok_statuses = {"success", "skipped"}
        succes = sum(1 for r in resultats if r.get("vues", {}).get("status") in ok_statuses and r.get("favoris", {}).get("status") in ok_statuses)
        return {
            "label": "Partage vues/favoris",
            "statut": task.get("global_status"),
            "heure": task.get("started_at"),
            "task_id": task.get("task_id"),
            "compteurs": {"succes": succes, "total": len(resultats), "bloques_quota": 0, "erreurs_clemz": 0},
            "prevu": None,
            "detail": task.get("interruption"),
        }

    plan_d1 = _dernier_plan_historique("Dressing 1", date_str)
    plan_d2 = _dernier_plan_historique("Dressing 2", date_str)

    # État de la chaîne de routine par dressing (12/10/2026) -- seul le jour
    # courant est conservé (routine_du_jour.json), None pour un jour passé.
    from services.automation_scheduler import get_routine_du_jour
    routine = get_routine_du_jour()
    if routine.get("date") != date_str:
        routine = None

    def _avec_chaine(etape, dressing, cle):
        etape["chaine"] = ((routine or {}).get(dressing) or {}).get(cle)
        return etape

    etapes = [
        _etape_vm(demarrage, "Démarrage VM"),
        _avec_chaine(_etape_dressing(scraping, "Dressing 1", "Scraping Dressing 1"), "Dressing 1", "scraping"),
        _avec_chaine(_etape_dressing(scraping, "Dressing 2", "Scraping Dressing 2"), "Dressing 2", "scraping"),
        _avec_chaine(_etape_dressing(baisse_prix, "Dressing 1", "Baisse de prix Dressing 1", plan_d1), "Dressing 1", "baisse_prix"),
        _avec_chaine(_etape_dressing(baisse_prix, "Dressing 2", "Baisse de prix Dressing 2", plan_d2), "Dressing 2", "baisse_prix"),
        _avec_chaine(_etape_dressing(repub_d1, "Dressing 1", "Republication Dressing 1", plan_d1), "Dressing 1", "republication"),
        _avec_chaine(_etape_dressing(repub_d2, "Dressing 2", "Republication Dressing 2", plan_d2), "Dressing 2", "republication"),
        _etape_partage(partage),
        _etape_vm(extinction, "Extinction VM"),
    ]

    from services.automation_scheduler import get_pause_routine
    return {
        "date": date_str,
        "etapes": etapes,
        "pause": get_pause_routine() if est_aujourdhui else None,
    }


_taches_reprise = set()


def _lancer_en_fond(coro):
    tache = asyncio.create_task(coro)
    _taches_reprise.add(tache)  # référence forte, sinon collectable en cours de route
    tache.add_done_callback(_taches_reprise.discard)


def _refuser_sur_satellite():
    from services.plan_source import est_satellite
    if est_satellite():
        raise HTTPException(status_code=400, detail="La routine ne tourne que sur la VM.")


@router.post("/reprendre-routine")
async def reprendre_routine_route():
    """
    Bouton "Reprendre la routine" du Journal (cf. échange du 12/10/2026) :
    relance, dans l'ordre et en tâche de fond, les étapes du jour manquées,
    en échec, bloquées ou interrompues (scraping -> baisse de prix par
    dressing), puis replanifie une republication à reprendre (+1 min) --
    celle-ci attend la fin des étapes précédentes via le verrou de routine.
    Seul moyen de reprendre une étape en échec : le redémarrage du backend ne
    le fait plus. Une étape arrêtée à la main n'est jamais relancée.
    """
    _refuser_sur_satellite()
    from services.automation_service import rattraper_routine, rattraper_republish_manques
    _lancer_en_fond(rattraper_routine(reprendre_echecs=True))
    await rattraper_republish_manques(reprendre=True)
    return {"status": "reprise lancée"}


@router.post("/arreter-tache")
async def arreter_tache_route(data: dict = Body(...)):
    """
    Bouton "Arrêter" d'une étape en cours (12/10/2026) : demande l'arrêt de
    l'action Clemz en cours sur ce dressing. Honoré à un point sûr (avant la
    suppression d'une annonce) via le STOP natif de Clemz, forcé après 5 min
    sans point sûr -- cf. clemz_automation._poll_mass_action. Les articles
    déjà traités restent enregistrés ; l'étape est notée "arrêtée" et n'est
    plus relancée automatiquement de la journée.
    """
    from services.clemz_automation import demander_arret, PROGRESSION
    from services.clemz_auto_message import get_profile_lock
    dressing = data.get("dressing")
    cle_profil = {"Dressing 1": "chrome_clemz", "Dressing 2": "edge_clemz"}.get(dressing)
    if not cle_profil:
        raise HTTPException(status_code=400, detail="Dressing inconnu.")
    if dressing not in PROGRESSION and not get_profile_lock(cle_profil).locked():
        raise HTTPException(status_code=409, detail=f"Aucune action Clemz en cours sur {dressing}.")
    demander_arret(dressing)
    logger.warning(f"🛑 [MAINTENANCE] Arrêt demandé depuis le Journal pour {dressing}.")
    return {"status": "arret_demande", "dressing": dressing}


@router.get("/routine-pause")
async def get_routine_pause_route():
    from services.automation_scheduler import get_pause_routine
    return {"pause": get_pause_routine()}


@router.post("/routine-pause")
async def set_routine_pause_route(data: dict = Body(...)):
    """
    Pause globale de la routine (12/10/2026) : {"mode": "jour"} jusqu'à
    minuit, {"mode": "indefini"} jusqu'à reprise, {"mode": null} pour lever.
    Une action déjà en cours continue (bouton "Arrêter" pour la couper). À la
    levée, les étapes du jour non commencées sont rattrapées dans l'ordre.
    """
    _refuser_sur_satellite()
    from services.automation_scheduler import set_pause_routine
    mode = data.get("mode")
    if mode not in ("jour", "indefini", None):
        raise HTTPException(status_code=400, detail="mode attendu : 'jour', 'indefini' ou null.")
    pause = set_pause_routine(mode)
    logger.info(f"⏸️ [MAINTENANCE] Pause de la routine : {mode or 'levée'}.")
    if mode is None:
        from services.automation_service import rattraper_routine, rattraper_republish_manques
        _lancer_en_fond(rattraper_routine(reprendre_echecs=False))
        await rattraper_republish_manques(reprendre=False)
    return {"pause": pause}


@router.post("/open-browser")
async def open_browser(data: dict = Body(...)):
    """
    Lance le navigateur Playwright (même profil de session + extension Clemz que
    les tâches d'automatisation) pour vérifier/rétablir manuellement la connexion
    Vinted et Clemz, ou ajuster des paramètres Clemz. Le navigateur reste ouvert
    jusqu'à fermeture manuelle par l'utilisateur.

    Payload attendu : { "profile": "clemz_chrome" | "clemz_edge" | "vinted_chrome" | "vinted_edge" }
    """
    key = data.get("profile")
    if key not in maintenance_service.profiles:
        raise HTTPException(status_code=400, detail="Profil de navigateur inconnu.")

    if maintenance_service.status.get(key) in ("ouverture", "ouvert"):
        return {"status": "already_open", "profile": key}

    asyncio.create_task(maintenance_service.open_browser(key))
    return {"status": "opening", "profile": key}


@router.get("/extension")
async def get_extension_status():
    """
    Compare la version de l'extension Clemz chargée localement (copie utilisée
    par Playwright) à la dernière version disponible dans Chrome, sans rien
    copier. Permet d'afficher un badge "à jour" / "mise à jour disponible".
    """
    return await asyncio.to_thread(maintenance_service.check_extension_status)


@router.post("/update-extension")
async def update_extension():
    """
    Copie la dernière version de l'extension Clemz installée (et auto-mise à jour)
    dans Chrome vers la copie locale utilisée par les automatisations Playwright.
    À lancer après une mise à jour de Clemz détectée dans Chrome.
    """
    open_clemz_profiles = [
        key for key in ("clemz_chrome", "clemz_edge")
        if maintenance_service.status.get(key) in ("ouverture", "ouvert")
    ]

    try:
        info = await asyncio.to_thread(maintenance_service.update_clemz_extension)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"❌ [MAINTENANCE] Erreur mise à jour extension Clemz : {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la mise à jour de l'extension Clemz.")

    return {
        "status": "success",
        **info,
        "warning": (
            "Des profils Clemz sont actuellement ouverts (" + ", ".join(open_clemz_profiles) +
            "). Ferme-les et relance-les pour charger la nouvelle version."
        ) if open_clemz_profiles else None,
    }
@router.post("/reset-extension-state")
async def reset_extension_state_route(data: dict = Body(...)):
    """
    Vide le stockage local de l'extension Clemz (chrome.storage.local, IndexedDB)
    pour le profil donné -- à utiliser après un crash qui laisse Clemz figé sur
    un écran obsolète au prochain lancement. Le profil doit être fermé.
    Payload attendu : { "profile": "clemz_chrome" | "clemz_edge" }
    """
    key = data.get("profile")
    if key not in maintenance_service.profiles:
        raise HTTPException(status_code=400, detail="Profil de navigateur inconnu.")

    try:
        result = await asyncio.to_thread(maintenance_service.reset_extension_state, key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"❌ [MAINTENANCE] Erreur réinitialisation extension : {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la réinitialisation de l'extension Clemz.")

    return {"status": "success", **result}


@router.get("/watchdog")
async def get_watchdog_status():
    """Statut courant du watchdog 'Messages automatiques' (actif ou non)."""
    return maintenance_service.get_watchdog_status()


@router.post("/watchdog/toggle")
async def toggle_watchdog(data: dict = Body(...)):
    """
    Active ou désactive le watchdog 'Messages automatiques' (Chrome + Edge).
    Payload attendu : { "active": true | false }
    """
    active = data.get("active")
    if active is None:
        raise HTTPException(status_code=400, detail="Le champ 'active' est requis.")

    if active:
        maintenance_service.start_watchdog()
    else:
        await maintenance_service.stop_watchdog()

    return maintenance_service.get_watchdog_status()

@router.get("/clemz-visible")
async def get_clemz_visible_route():
    return maintenance_service.get_clemz_visible_mode()


@router.post("/clemz-visible/toggle")
async def toggle_clemz_visible_route(payload: dict = Body(...)):
    visible = payload.get("visible", False)
    maintenance_service.set_clemz_visible_mode(visible)
    return maintenance_service.get_clemz_visible_mode()


@router.get("/baisse-prix-auto")
async def get_baisse_prix_auto_route():
    """Statut courant de la baisse de prix automatique quotidienne (cron 10h40)."""
    return maintenance_service.get_baisse_prix_auto_status()


@router.post("/baisse-prix-auto/toggle")
async def toggle_baisse_prix_auto_route(data: dict = Body(...)):
    """
    Active ou désactive la baisse de prix automatique quotidienne. N'affecte pas
    le déclenchement manuel "Forcer baisse de prix" depuis le dashboard.
    Payload attendu : { "active": true | false }
    """
    active = data.get("active")
    if active is None:
        raise HTTPException(status_code=400, detail="Le champ 'active' est requis.")
    maintenance_service.set_baisse_prix_auto_active(active)
    return maintenance_service.get_baisse_prix_auto_status()

@router.get("/partage-vues-favoris-auto")
async def get_partage_vues_favoris_auto_route():
    """Statut courant du déclenchement automatique du partage vues/favoris (post-republication)."""
    return maintenance_service.get_partage_vues_favoris_auto_status()


@router.post("/partage-vues-favoris-auto/toggle")
async def toggle_partage_vues_favoris_auto_route(data: dict = Body(...)):
    """
    Active ou désactive l'enchaînement automatique du partage vues/favoris après
    une republication réussie. N'affecte pas le bouton de test manuel du dashboard.
    Payload attendu : { "active": true | false }
    """
    active = data.get("active")
    if active is None:
        raise HTTPException(status_code=400, detail="Le champ 'active' est requis.")
    maintenance_service.set_partage_vues_favoris_auto_active(active)
    return maintenance_service.get_partage_vues_favoris_auto_status()


@router.get("/scraping-auto")
async def get_scraping_auto_route():
    """Statut courant du scraping automatique planifié (syncs 10h30/22h)."""
    return maintenance_service.get_scraping_auto_status()


@router.post("/scraping-auto/toggle")
async def toggle_scraping_auto_route(data: dict = Body(...)):
    """
    Active ou désactive les 2 synchronisations planifiées (10h30/22h). N'affecte
    pas le bouton "Lancer Scraping" manuel du dashboard.
    Payload attendu : { "active": true | false }
    """
    active = data.get("active")
    if active is None:
        raise HTTPException(status_code=400, detail="Le champ 'active' est requis.")
    maintenance_service.set_scraping_auto_active(active)
    return maintenance_service.get_scraping_auto_status()


@router.get("/republication-auto")
async def get_republication_auto_route():
    """Statut courant de la republication automatique (plan du jour + rattrapage)."""
    return maintenance_service.get_republication_auto_status()


@router.post("/republication-auto/toggle")
async def toggle_republication_auto_route(data: dict = Body(...)):
    """
    Active ou désactive la republication automatique planifiée (horaire tiré
    par le plan du jour, et son rattrapage au démarrage). N'affecte pas les
    boutons de republication manuels du dashboard.
    Payload attendu : { "active": true | false }
    """
    active = data.get("active")
    if active is None:
        raise HTTPException(status_code=400, detail="Le champ 'active' est requis.")
    maintenance_service.set_republication_auto_active(active)
    return maintenance_service.get_republication_auto_status()

@router.post("/sync-dates-republication")
async def sync_dates_republication_route(
    file: UploadFile = File(...),
    apply: bool = Form(False),
):
    """
    Corrige date_republication (et date_publication en cohérence) à partir d'un
    export CSV Clemz. apply=False (défaut) : aperçu seul, rien n'est écrit en base.
    apply=True : applique réellement les mises à jour calculées lors de l'aperçu.
    """
    try:
        file_bytes = await file.read()
        result = await asyncio.to_thread(sync_dates_service.run_sync, file_bytes, apply)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ [MAINTENANCE] Erreur sync dates republication : {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la synchronisation des dates.")


@router.post("/preparer-arret")
async def preparer_arret(data: dict = Body(default={})):
    """
    Appelée par la tâche planifiée Windows avant l'extinction nocturne du
    VM (cf. échange du 04/10/2026, PC-VM éteint entre 23h et 10h) --
    attend que les profils Chrome D1/D2 soient libres (aucune automatisation
    Clemz en cours : republication, baisse de prix, liquidation, ou le
    watchdog via la préemption) avant de laisser le script appelant couper
    l'alimentation. Un arrêt brutal en pleine écriture d'un profil Chrome
    (IndexedDB/leveldb) peut le corrompre -- bien plus grave qu'une tâche
    simplement interrompue (déjà géré proprement par cleanup_stale_running_tasks
    au prochain démarrage, cf. 21/09/2026).

    Payload optionnel : { "timeout_secondes": 2700 } (défaut 45 min -- large
    marge au-dessus du pire cas observé : un gros lot + 15 min d'attente
    captcha, cf. échange du 03/10/2026).

    Bloque la requête HTTP tant que ce n'est pas prêt (ou jusqu'au timeout) --
    le script appelant doit utiliser un timeout réseau au moins aussi long.
    Retourne {"status": "pret"|"timeout", "attente_secondes": ...}.
    """
    from services.clemz_auto_message import get_profile_lock, ACCOUNTS

    timeout_secondes = data.get("timeout_secondes") or 45 * 60
    poll_secondes = 15

    from services.automation_scheduler import log_vm_lifecycle_event

    locks = [get_profile_lock(acc["account_key"]) for acc in ACCOUNTS]
    elapsed = 0
    while elapsed < timeout_secondes:
        if not any(lock.locked() for lock in locks):
            logger.info(f"🌙 [MAINTENANCE] Prêt pour l'extinction nocturne après {elapsed}s d'attente.")
            log_vm_lifecycle_event("extinction", "succes", detail=f"Prêt après {elapsed}s d'attente.")
            return {"status": "pret", "attente_secondes": elapsed}
        if elapsed == 0:
            logger.warning("🌙 [MAINTENANCE] Extinction nocturne demandée mais une automatisation Clemz est en cours -- attente.")
        await asyncio.sleep(poll_secondes)
        elapsed += poll_secondes

    logger.error(f"🌙 [MAINTENANCE] Toujours occupé après {timeout_secondes}s -- extinction forcée malgré tout (délai dépassé).")
    log_vm_lifecycle_event("extinction", "echec", detail=f"Timeout après {timeout_secondes}s -- extinction forcée malgré une automatisation toujours en cours.")
    return {"status": "timeout", "attente_secondes": elapsed}