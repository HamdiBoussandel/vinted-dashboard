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
    """Statut courant de la baisse de prix automatique quotidienne (cron 14h05)."""
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
    """Statut courant du scraping automatique planifié (syncs 14h/22h)."""
    return maintenance_service.get_scraping_auto_status()


@router.post("/scraping-auto/toggle")
async def toggle_scraping_auto_route(data: dict = Body(...)):
    """
    Active ou désactive les 2 synchronisations planifiées (14h/22h). N'affecte
    pas le bouton "Lancer Scraping" manuel du dashboard.
    Payload attendu : { "active": true | false }
    """
    active = data.get("active")
    if active is None:
        raise HTTPException(status_code=400, detail="Le champ 'active' est requis.")
    maintenance_service.set_scraping_auto_active(active)
    return maintenance_service.get_scraping_auto_status()

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

    locks = [get_profile_lock(acc["account_key"]) for acc in ACCOUNTS]
    elapsed = 0
    while elapsed < timeout_secondes:
        if not any(lock.locked() for lock in locks):
            logger.info(f"🌙 [MAINTENANCE] Prêt pour l'extinction nocturne après {elapsed}s d'attente.")
            return {"status": "pret", "attente_secondes": elapsed}
        if elapsed == 0:
            logger.warning("🌙 [MAINTENANCE] Extinction nocturne demandée mais une automatisation Clemz est en cours -- attente.")
        await asyncio.sleep(poll_secondes)
        elapsed += poll_secondes

    logger.error(f"🌙 [MAINTENANCE] Toujours occupé après {timeout_secondes}s -- extinction forcée malgré tout (délai dépassé).")
    return {"status": "timeout", "attente_secondes": elapsed}