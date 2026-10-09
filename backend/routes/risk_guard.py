"""
Route de consultation du statut anti-détection (risk_guard.py +
planification_republication.py) -- affiché en LED par dressing dans
SystemControlBar. Ne modifie jamais risk_guard.py lui-même ; peut en revanche
régénérer le plan du jour en cache via rafraichir_repos_si_leve() s'il est
devenu obsolète (cf. docstring de cette fonction) avant de le lire, pour que
l'affichage reste "live" plutôt que figé sur l'état du matin.
"""
from fastapi import APIRouter

from services.risk_guard import get_active_convalescence, get_quota_manuel_restant
from services.automation_scheduler import get_plan_du_jour
from services.planification_republication import rafraichir_repos_si_leve

router = APIRouter(prefix="/api/risk-guard", tags=["RiskGuard"])

_DRESSINGS = {"dressing1": "Dressing 1", "dressing2": "Dressing 2"}


@router.get("/statut")
def get_statut():
    """
    Pour chaque dressing :
      - "convalescence" : détail du palier en cours (rang de récidive, durée
        totale, jours restants, jour actif/pause du cycle), ou null si aucune
        suspension récente ne déclenche de palier actif.
      - "plan_du_jour" : décision du jour (niveau ayant tranché parmi
        convalescence/repos_force/calendrier, actif/pause, horaire et volume
        cible prévus si actif), ou null si pas encore généré aujourd'hui --
        rafraîchi ici même via rafraichir_repos_si_leve() si un repos
        Niveau 1/2 mis en cache ce matin n'est plus vrai maintenant (cf.
        échange du 11/09/2026).
      - "quota_baisse_prix_restant" : nombre de baisses de prix que le bouton
        manuel autoriserait réellement là, maintenant, pour ce dressing (quota
        brut + Niveaux 1/2 anti-détection) -- affiché à côté de l'objectif de
        republication dans la barre d'état.
      - "quota_republication_restant" : idem pour la republication -- distinct
        de plan_du_jour.volume_cible, qui reste la CIBLE du jour (jamais
        décrémentée), alors que ce champ reflète ce qu'il reste vraiment à
        faire aujourd'hui. Sans lui, la barre affichait "9 auj." avec un point
        vert plein même une fois la cible du jour déjà atteinte, aucun moyen
        de distinguer visuellement "9 à faire" de "9 faits, 0 restant" (cf.
        échange du 15/09/2026).
    Aucune automatisation n'est bloquée par cette route, c'est un simple
    rappel visuel pendant la gestion manuelle.
    """
    for nom in _DRESSINGS.values():
        rafraichir_repos_si_leve(nom)

    return {
        cle: {
            "convalescence": get_active_convalescence(nom),
            "plan_du_jour": get_plan_du_jour(nom),
            "quota_baisse_prix_restant": get_quota_manuel_restant(nom, "baisse_prix"),
            "quota_republication_restant": get_quota_manuel_restant(nom, "republication"),
        }
        for cle, nom in _DRESSINGS.items()
    }


@router.get("/plan-du-jour")
def get_plans_du_jour_bruts():
    """
    Plans du jour BRUTS de cette machine, lus par le satellite (PC de dev,
    cf. services/plan_source.py) -- la VM est la seule source de vérité du
    tirage. Même rafraîchissement live que /statut avant lecture (un repos
    Niveau 1/2 levé en cours de journée est régénéré ici, sur la VM).
    Format : {"date": "AAAA-MM-JJ", "Dressing 1": plan|null, "Dressing 2": plan|null}.
    """
    from datetime import datetime
    for nom in _DRESSINGS.values():
        rafraichir_repos_si_leve(nom)
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        **{nom: get_plan_du_jour(nom) for nom in _DRESSINGS.values()},
    }
