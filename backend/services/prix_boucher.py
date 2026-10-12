"""
Stratégie "prix de boucher" (13/10/2026) -- démarrée en observation, ACTIVÉE
le jour même après validation de l'utilisateur ("mon ressenti correspond") :
les marches conseillées ici sont appliquées par la baisse de prix de la
routine (automation_service.run_baisse_prix_auto), jours actifs seulement,
en priorité dans le quota. Ce module ne modifie lui-même aucun prix.

L'utilisateur publie volontairement ses articles AU-DESSUS du prix visé
(+33 % en moyenne sur 10 exemples réels), et veut corriger VITE le prix des
articles sur lesquels ce prix haut ne prend pas -- les autres restent au prix
haut. La baisse "mauvaise performance" existante n'est pas adaptée (pensée pour
des articles au juste prix qui stagnent sur des semaines : jugement tardif,
-10 % doux pour un article neuf, 7 jours entre deux baisses, aucun objectif).

Règle (affichée dans le dashboard, journalisée chaque jour pour suivre son
effet, appliquée par la routine) :
  - article concerné : tout article non vendu et hors liquidation, publié OU
    republié depuis moins de FENETRE_JOURS jours (une republication Clemz
    recrée l'annonce : vues et favoris repartent de zéro -- même logique que
    la première mise en ligne, élargi aux articles republiés le 13/10/2026) ;
  - jours comptés depuis la DERNIÈRE (re)publication ;
  - plancher calculé sur le TOUT PREMIER prix connu de l'article, pas sur le
    prix du cycle en cours : sinon chaque republication autoriserait une
    nouvelle descente de -26 % et le prix glisserait cycle après cycle ;
  - contrôle à J+3 puis tous les 3 jours ;
  - "fonctionne" : au moins SEUIL_FAVORIS_REELS vrais favoris (boost Clemz
    de 20 favoris déduit) -- choix de l'utilisateur, 1-2 favoris arrivant
    souvent même quand le prix est trop haut ;
  - sinon, marche conseillée : -10 % sur le prix actuel, jamais sous le
    plancher de la tranche (prix de départ x 0,74 / 0,76 / 0,83, moyennes des
    exemples de l'utilisateur) ni sous le plancher de marge existant.
"""
import json
import math
import os
from datetime import datetime

SEUIL_FAVORIS_REELS = 5
JOUR_PREMIER_CONTROLE = 3
# Mode "mixte" (13/10/2026) : premier jugement maintenu à J+3 (à J+2, médiane
# de 4 vues seulement -- trop tôt pour juger), puis une marche tous les 2 jours
# une fois l'article jugé "ne fonctionne pas" : J+3, J+5, J+7 au lieu de
# J+3, J+6, J+9 -- même baisse totale, atteinte ~2 jours plus tôt.
INTERVALLE_CONTROLES_JOURS = 2
PAS_MARCHE = 0.10
FENETRE_JOURS = 15  # au-delà, l'article relève de la logique habituelle

# Espacement réel entre deux marches appliquées par la routine (baisse de prix
# de 10h40, une fois par jour) : une marche le jour J bloque J+1, la suivante
# part à J+2 -- INTERVALLE_CONTROLES_JOURS - 1, sinon la baisse de J (10h41)
# serait encore dans la fenêtre à J+2 (10h40) et la marche glisserait d'un
# jour. Une marche manquée (jour de repos du dressing) part au jour actif suivant.
COOLDOWN_MARCHE_JOURS = INTERVALLE_CONTROLES_JOURS - 1

# (prix de départ strictement inférieur à, coefficient du plancher) -- descente
# nécessaire relevée sur les exemples de l'utilisateur (13/10/2026) : -26 %
# sous 50 €, -24 % entre 50 et 80 €, -17 % au-dessus (1 seul exemple).
PLANCHERS_TRANCHE = ((50.0, 0.74), (80.0, 0.76), (math.inf, 0.83))

# Le boost Clemz ajoute exactement 20 favoris à un moment donné (vérifié sur
# les articles publiés du 13/09 au 08/10) : au-delà de 20, on le déduit ;
# en dessous, le boost n'est pas encore passé et les favoris sont réels.
BOOST_FAVORIS = 20

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL_OBSERVATION_FILE = os.path.join(BASE_DIR, "prix_boucher_observation.jsonl")


def favoris_reels(favoris):
    favoris = favoris or 0
    return favoris - BOOST_FAVORIS if favoris >= BOOST_FAVORIS else favoris


def plancher_tranche(prix_depart):
    for borne, coefficient in PLANCHERS_TRANCHE:
        if prix_depart < borne:
            return round(prix_depart * coefficient, 2)


def _prix_de_depart(db_client, ids):
    """
    Plus haut prix connu de chaque article, faute de colonne "prix de
    publication" en base : plus ancien relevé de score_snapshots (premier prix
    vu par le scraping -- relevés disponibles depuis le 14/08/2026 seulement)
    et prix d'avant chaque baisse enregistrée (price_drops.prix_avant). Pour un
    article publié avant le 14/08 et baissé sans trace, ce prix reste une
    approximation basse du vrai prix de départ.
    """
    departs = {}
    for i in range(0, len(ids), 50):
        lot = ids[i:i + 50]
        lignes = db_client.table("score_snapshots").select("article_id, snapshot_hour, prix") \
            .in_("article_id", lot).execute().data or []
        for ligne in sorted(lignes, key=lambda l: l.get("snapshot_hour") or ""):
            if ligne.get("prix") is not None and ligne.get("snapshot_hour"):
                departs.setdefault(ligne["article_id"], float(ligne["prix"]))
        baisses = db_client.table("price_drops").select("article_id, prix_avant") \
            .in_("article_id", lot).execute().data or []
        for baisse in baisses:
            if baisse.get("prix_avant") is not None:
                departs[baisse["article_id"]] = max(departs.get(baisse["article_id"], 0), float(baisse["prix_avant"]))
    return departs


def evaluer_articles(inventaire):
    """Évaluation du jour de chaque article suivi (cf. docstring du module)."""
    from database import supabase as db_client

    candidats = [
        a for a in inventaire
        if not a.get("is_vendu") and not a.get("liquidation_phase")
        and (a.get("jours_en_ligne") or 0) <= FENETRE_JOURS
    ]
    departs = _prix_de_depart(db_client, [a["id"] for a in candidats])

    evaluations = []
    for a in candidats:
        prix_actuel = float(a.get("prix_vente") or 0)
        prix_depart = max(departs.get(a["id"], prix_actuel), prix_actuel)
        jours = a.get("jours_en_ligne") or 0
        fav_reels = favoris_reels(a.get("f_tot"))
        plancher = max(plancher_tranche(prix_depart), float(a.get("prix_plancher") or 0))
        jour_de_controle = jours >= JOUR_PREMIER_CONTROLE and (jours - JOUR_PREMIER_CONTROLE) % INTERVALLE_CONTROLES_JOURS == 0
        prochain_controle = (
            JOUR_PREMIER_CONTROLE if jours < JOUR_PREMIER_CONTROLE
            else jours + (-(jours - JOUR_PREMIER_CONTROLE)) % INTERVALLE_CONTROLES_JOURS
        )

        prix_conseille = None
        if jours < JOUR_PREMIER_CONTROLE:
            statut = "trop_tot"
        elif fav_reels >= SEUIL_FAVORIS_REELS:
            statut = "fonctionne"
        elif prix_actuel <= plancher + 0.01:
            statut = "plancher_atteint"
        else:
            statut = "ne_fonctionne_pas"
            prix_conseille = max(plancher, round(prix_actuel * (1 - PAS_MARCHE), 2))

        evaluations.append({
            "id": a["id"],
            "nom": a.get("nom"),
            "dressing": a.get("dressing"),
            "republie": not a.get("is_new"),
            "photo_url": a.get("photo_url"),
            "jours": jours,
            "prix_depart": prix_depart,
            "prix_actuel": prix_actuel,
            "plancher": plancher,
            "favoris": a.get("f_tot") or 0,
            "favoris_reels": fav_reels,
            "vues": a.get("v_tot") or 0,
            "statut": statut,
            "prix_conseille": prix_conseille,
            "jour_de_controle": jour_de_controle,
            "prochain_controle": prochain_controle,
        })
    ordre = {"ne_fonctionne_pas": 0, "plancher_atteint": 1, "fonctionne": 2, "trop_tot": 3}
    evaluations.sort(key=lambda e: (ordre[e["statut"]], -e["jours"]))
    return evaluations


async def get_observation():
    from services.supabase_service import SupabaseService
    inventaire = await SupabaseService().get_processed_inventory()
    return evaluer_articles(inventaire)


async def enregistrer_observation_du_jour():
    """
    Une ligne par article et par jour dans prix_boucher_observation.jsonl --
    base de la calibration (comparer, quelques jours plus tard, le statut
    observé au devenir réel de l'article : vendu, favoris arrivés...).
    Idempotente : ne réécrit pas un jour déjà enregistré.
    """
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(JOURNAL_OBSERVATION_FILE):
        with open(JOURNAL_OBSERVATION_FILE, "r", encoding="utf-8") as f:
            for ligne in f:
                if f'"date": "{aujourdhui}"' in ligne:
                    return 0
    evaluations = await get_observation()
    with open(JOURNAL_OBSERVATION_FILE, "a", encoding="utf-8") as f:
        for e in evaluations:
            f.write(json.dumps({"date": aujourdhui, **{k: v for k, v in e.items() if k != "photo_url"}},
                               ensure_ascii=False) + "\n")
    print(f"🥩 [PRIX BOUCHER] Observation du jour enregistrée : {len(evaluations)} article(s) suivi(s).")
    return len(evaluations)
