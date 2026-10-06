"""
Routes pour la page "Préparer Annonces" : pilotage du watcher (start/stop/statut)
et consultation des lots en cours de traitement.
"""
import os
import shutil

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from database import supabase
from services.photo_watcher_service import watcher_service, DOSSIER_TRAITE, creer_lot_depuis_fichiers
from services.brouillon_worker import (
    diviser_lot,
    vider_historique,
    regenerer_lot_avec_ajustements,
    deplacer_photos,
    valider_lot,
    valider_tous_les_lots,
    supprimer_lot,
    reessayer_creation_brouillon,
    changer_dressing_lot,
)
from vinted_draft_creation_worker import main as lancer_creation_brouillons, statut_worker, traiter_lot_par_id

router = APIRouter(prefix="/api", tags=["preparer-annonces"])


class RegenererLotPayload(BaseModel):
    ajustements: dict


class DiviserLotPayload(BaseModel):
    photos_a_extraire: list[str]


class DeplacerPhotosPayload(BaseModel):
    photos_a_deplacer: list[str]
    lot_id_cible: str | None = None


class ChangerDressingPayload(BaseModel):
    dressing: str


@router.post("/preparer-annonces/watcher/start")
def demarrer_watcher():
    return watcher_service.demarrer()


@router.post("/preparer-annonces/watcher/stop")
def arreter_watcher():
    return watcher_service.arreter()


@router.get("/preparer-annonces/watcher/status")
def statut_watcher():
    return watcher_service.statut()


@router.get("/preparer-annonces/lots")
def lister_lots(limit: int = 50):
    """
    Liste les lots actifs pour affichage dashboard -- exclut les brouillons
    déjà créés avec succès (status='draft_created'), archivés pour laisser
    la place aux nouvelles créations plutôt que de s'accumuler indéfiniment
    dans la vue principale (cf. échange du 10/09/2026).
    """
    response = supabase.table("brouillons_pending") \
        .select("*") \
        .neq("status", "draft_created") \
        .order("created_at", desc=True) \
        .limit(limit) \
        .execute()
    return response.data


@router.post("/preparer-annonces/lots/creer-depuis-fichiers")
async def creer_lot_depuis_fichiers_route(
    fichiers: list[UploadFile] = File(...),
    dressing: str = Form("Dressing 1"),
):
    """
    Crée un lot directement depuis des fichiers glissés-déposés depuis
    l'explorateur de fichiers (mode "Regrouper manuellement") -- pas de
    clustering automatique, le regroupement est entièrement décidé côté client.
    """
    if not fichiers:
        raise HTTPException(status_code=400, detail="Aucun fichier reçu.")
    contenus = [(f.filename, await f.read()) for f in fichiers]
    lot_id = creer_lot_depuis_fichiers(contenus, dressing=dressing)
    if not lot_id:
        raise HTTPException(status_code=500, detail="Échec de la création du lot.")
    return {"status": "success", "lot_id": lot_id}


@router.get("/preparer-annonces/photo")
def obtenir_photo(path: str):
    """
    Sert une photo archivée pour affichage en vignette. Restreint strictement
    au dossier d'archive du watcher pour éviter tout accès fichier arbitraire.
    """
    chemin_normalise = os.path.normpath(path)
    dossier_normalise = os.path.normpath(DOSSIER_TRAITE)
    if not chemin_normalise.startswith(dossier_normalise):
        raise HTTPException(status_code=403, detail="Chemin non autorisé")
    if not os.path.isfile(chemin_normalise):
        raise HTTPException(status_code=404, detail="Photo introuvable")
    return FileResponse(chemin_normalise)


@router.post("/preparer-annonces/lots/{lot_id}/split")
def diviser_lot_route(lot_id: str, payload: DiviserLotPayload):
    resultat = diviser_lot(lot_id, payload.photos_a_extraire)
    if resultat["status"] != "success":
        raise HTTPException(status_code=400, detail=resultat["message"])
    return resultat

@router.post("/preparer-annonces/lots/{lot_id}/deplacer-photos")
def deplacer_photos_route(lot_id: str, payload: DeplacerPhotosPayload):
    resultat = deplacer_photos(lot_id, payload.photos_a_deplacer, payload.lot_id_cible)
    if resultat["status"] != "success":
        raise HTTPException(status_code=400, detail=resultat["message"])
    return resultat


@router.post("/preparer-annonces/lots/{lot_id}/dressing")
def changer_dressing_lot_route(lot_id: str, payload: ChangerDressingPayload):
    """
    Change le dressing (Dressing 1/2) choisi pour un lot -- détermine quel
    profil Chrome vinted_draft_creation_worker utilisera à la création du
    brouillon. Refusé si le brouillon est déjà créé ou en cours de création.
    """
    resultat = changer_dressing_lot(lot_id, payload.dressing)
    if resultat["status"] != "success":
        raise HTTPException(status_code=400, detail=resultat["message"])
    return resultat


@router.post("/preparer-annonces/lots/{lot_id}/valider")
def valider_lot_route(lot_id: str):
    resultat = valider_lot(lot_id)
    if resultat["status"] != "success":
        raise HTTPException(status_code=400, detail=resultat.get("message", "Échec de la génération."))
    return resultat


@router.post("/preparer-annonces/lots/valider-tout")
def valider_tous_les_lots_route(background_tasks: BackgroundTasks):
    """
    En tâche de fond : avec ~400 photos et 15 req/min, valider tous les lots
    d'un coup peut prendre plusieurs minutes -- la requête HTTP répond tout de
    suite, le traitement continue derrière.
    """
    background_tasks.add_task(valider_tous_les_lots)
    return {"status": "started"}

@router.post("/preparer-annonces/lots/{lot_id}/regenerate")
def regenerer_lot_route(lot_id: str, payload: RegenererLotPayload):
    """
    Relance la génération IA (titre/description/prix) d'un lot en tenant compte
    des ajustements manuels (état/défaut, fabrication) cochés par l'utilisateur.
    """
    resultat = regenerer_lot_avec_ajustements(lot_id, payload.ajustements)
    if resultat["status"] != "success":
        raise HTTPException(status_code=400, detail=resultat["message"])
    return resultat


@router.delete("/preparer-annonces/lots/{lot_id}")
def supprimer_lot_route(lot_id: str):
    resultat = supprimer_lot(lot_id)
    if resultat["status"] != "success":
        raise HTTPException(status_code=500, detail=resultat["message"])
    # Nettoyage best-effort du dossier de photos archivées -- ne bloque jamais
    # la suppression en base, même si le dossier est déjà absent.
    dossier_archive = os.path.join(DOSSIER_TRAITE, lot_id)
    shutil.rmtree(dossier_archive, ignore_errors=True)
    return resultat

@router.delete("/preparer-annonces/lots")
def vider_historique_route():
    resultat = vider_historique()
    if resultat["status"] != "success":
        raise HTTPException(status_code=500, detail=resultat["message"])
    return resultat

@router.post("/preparer-annonces/lots/{lot_id}/reessayer-creation")
def reessayer_creation_brouillon_route(lot_id: str, background_tasks: BackgroundTasks):
    """
    Remet un lot 'draft_error' en file (sans repasser par Gemini) puis relance
    le worker de création de brouillon Vinted -- même mécanisme que
    /lancer-brouillons (tâche de fond, un seul worker à la fois).
    """
    if statut_worker["en_cours"]:
        raise HTTPException(status_code=409, detail="Un traitement est déjà en cours.")
    resultat = reessayer_creation_brouillon(lot_id)
    if resultat["status"] != "success":
        raise HTTPException(status_code=400, detail=resultat["message"])
    background_tasks.add_task(lancer_creation_brouillons)
    return resultat


@router.post("/preparer-annonces/lots/{lot_id}/creer-brouillon")
def creer_brouillon_unique_route(lot_id: str, background_tasks: BackgroundTasks):
    """
    Crée le brouillon Vinted pour UN SEUL lot 'generation_ok' -- alternative au
    lancement groupé (/lancer-brouillons) quand on veut traiter juste un lot
    sans attendre tous les autres.
    """
    if statut_worker["en_cours"]:
        raise HTTPException(status_code=409, detail="Un traitement est déjà en cours.")
    background_tasks.add_task(traiter_lot_par_id, lot_id)
    return {"status": "launched"}


@router.post("/preparer-annonces/lancer-brouillons")
async def lancer_brouillons_route(background_tasks: BackgroundTasks):
    if statut_worker["en_cours"]:
        raise HTTPException(status_code=409, detail="Un traitement est déjà en cours.")
    background_tasks.add_task(lancer_creation_brouillons)
    return {"status": "launched"}


@router.get("/preparer-annonces/statut-brouillons")
async def statut_brouillons_route():
    return statut_worker