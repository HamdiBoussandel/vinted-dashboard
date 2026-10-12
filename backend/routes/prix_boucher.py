"""
Stratégie "prix de boucher" en mode observation (13/10/2026) -- lecture seule,
cf. services/prix_boucher.py. Aucun prix n'est modifié par ces routes.
"""
from fastapi import APIRouter

from services import prix_boucher

router = APIRouter(prefix="/api/prix-boucher", tags=["PrixBoucher"])


@router.get("/observation")
async def get_observation_route():
    return {
        "regles": {
            "seuil_favoris_reels": prix_boucher.SEUIL_FAVORIS_REELS,
            "jour_premier_controle": prix_boucher.JOUR_PREMIER_CONTROLE,
            "intervalle_controles_jours": prix_boucher.INTERVALLE_CONTROLES_JOURS,
            "pas_marche_pourcent": round(prix_boucher.PAS_MARCHE * 100),
            "fenetre_jours": prix_boucher.FENETRE_JOURS,
        },
        "articles": await prix_boucher.get_observation(),
    }
