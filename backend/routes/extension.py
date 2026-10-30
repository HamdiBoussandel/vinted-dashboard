"""
Routes de l'extension Chrome "VintedPro Sync" (26/10/2026, étape 1 : liaison).

L'extension détecte le compte Vinted connecté dans le navigateur (appel à
/api/v2/users/current DEPUIS la page vinted.fr, avec la session du
navigateur) et le lie à son dressing. Choix de conception : AUCUN jeton
Vinted ne transite par le backend ni n'est stocké -- l'extension fera
elle-même les appels à l'API Vinted et n'enverra que des données (étapes
suivantes : synchronisation du dressing, puis des ventes).

Liaisons gardées dans backend/extension_liaisons.json (local, gitignoré) :
{member_id: {dressing, login, lie_le, derniere_vue}}.
"""
import json
import os
import threading
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/extension", tags=["Extension"])

# Comptes Vinted connus -> dressing (mêmes identifiants que session_manager)
MEMBRES = {"287248160": "Dressing 1", "3136979514": "Dressing 2"}

_FICHIER = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "extension_liaisons.json")
_verrou = threading.Lock()


def _lire():
    try:
        with open(_FICHIER, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _ecrire(liaisons):
    with open(_FICHIER, "w", encoding="utf-8") as f:
        json.dump(liaisons, f, ensure_ascii=False, indent=1)


def _compte(payload):
    member_id = str(payload.get("member_id") or "").strip()
    if not member_id.isdigit():
        raise HTTPException(status_code=400, detail="member_id manquant ou invalide.")
    return member_id, (payload.get("login") or "").strip()


@router.post("/ping")
async def ping(payload: dict = Body(...)):
    """Appelé par l'extension à chaque détection du compte connecté : dit si
    ce compte est connu (D1/D2) et lié, et note la dernière présence."""
    member_id, login = _compte(payload)
    with _verrou:
        liaisons = _lire()
        liaison = liaisons.get(member_id)
        if liaison:
            liaison["derniere_vue"] = datetime.now().isoformat(timespec="seconds")
            if login:
                liaison["login"] = login
            _ecrire(liaisons)
    return {"connu": member_id in MEMBRES, "dressing": MEMBRES.get(member_id), "lie": bool(liaison),
            "liaison": liaison}


@router.post("/lier")
async def lier(payload: dict = Body(...)):
    """Bouton "Lier ce dressing" de l'extension. Refusé pour un compte qui
    n'est ni D1 ni D2 (rien n'est enregistré)."""
    member_id, login = _compte(payload)
    dressing = MEMBRES.get(member_id)
    if not dressing:
        raise HTTPException(status_code=404, detail=f"Compte Vinted {login or member_id} inconnu : "
                                                    f"ni Dressing 1 ni Dressing 2.")
    maintenant = datetime.now().isoformat(timespec="seconds")
    with _verrou:
        liaisons = _lire()
        liaison = liaisons.get(member_id) or {"lie_le": maintenant}
        liaison.update({"dressing": dressing, "login": login, "derniere_vue": maintenant})
        liaisons[member_id] = liaison
        _ecrire(liaisons)
    print(f"🔗 [EXTENSION] {dressing} lié (@{login}, membre {member_id}).")
    return {"lie": True, "dressing": dressing, "liaison": liaison}


@router.delete("/lier/{member_id}")
async def delier(member_id: str):
    """Retire la liaison (fichier local uniquement, aucune donnée d'article touchée)."""
    with _verrou:
        liaisons = _lire()
        retiree = liaisons.pop(member_id, None)
        _ecrire(liaisons)
    if not retiree:
        raise HTTPException(status_code=404, detail="Ce compte n'est pas lié.")
    print(f"🔓 [EXTENSION] {retiree['dressing']} délié (membre {member_id}).")
    return {"lie": False}


@router.get("/statut")
async def statut():
    """Liaisons connues, par dressing (pour un futur affichage dashboard)."""
    liaisons = _lire()
    return {dressing: next(({"member_id": m, **l} for m, l in liaisons.items() if l.get("dressing") == dressing), None)
            for dressing in MEMBRES.values()}
