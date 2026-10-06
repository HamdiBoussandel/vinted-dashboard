"""
price_intelligence.py — Comparaison prix_vente actuel vs médiane marché Vinted.

Chantier roadmap #2 : recherche par mot-clé (titre de l'article) via le
microservice svc-catalogue déjà validé dans price_estimation_service.py, sans
filtres structurés (marque/taille/catégorie) puisque la table `articles` ne
stocke actuellement que le titre libre (`nom`) et les prix -- pas d'IDs
référentiel par article.

Première version : fonction cœur testable sur UN seul article. Le job batch
(tous les articles, throttling, cron hebdomadaire) sera ajouté dans une
itération suivante, une fois cette fonction validée.
"""

import logging

from database import supabase
from services.price_estimation_service import (
    _get_auth_data,
    search_market_prices_by_filters,
    compute_robust_median,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Seuils de comparaison prix_vente vs médiane marché.
# En dessous de -SEUIL_ECART : article probablement sous-évalué (marge perdue).
# Au-dessus de +SEUIL_ECART : article probablement surestimé (risque invendu).
SEUIL_ECART_PCT = 15.0


def _get_article(article_id):
    """
    Récupère un article par ID. Utilise une correspondance explicite par liste
    plutôt que .maybe_single(), pour distinguer proprement zéro/un/plusieurs
    résultats (cf. principe de discipline Supabase du projet).
    """
    result = supabase.table("articles").select("id, nom, prix_vente").eq("id", article_id).execute()
    rows = result.data or []

    if len(rows) == 0:
        return None
    if len(rows) > 1:
        logger.warning(f"⚠️ [PRICE INTELLIGENCE] Plusieurs articles trouvés pour id={article_id}, on prend le premier.")
    return rows[0]


def _compute_recommandation(prix_vente, mediane):
    """
    Compare le prix de vente actuel à la médiane marché et retourne un statut
    + l'écart en pourcentage. Écart positif = prix_vente au-dessus du marché.
    """
    if mediane is None or mediane == 0:
        return {"statut": "indetermine", "ecart_pct": None}

    ecart_pct = round(((prix_vente - mediane) / mediane) * 100, 1)

    if ecart_pct > SEUIL_ECART_PCT:
        statut = "surestime"
    elif ecart_pct < -SEUIL_ECART_PCT:
        statut = "sous_evalue"
    else:
        statut = "aligne"

    return {"statut": statut, "ecart_pct": ecart_pct}


async def compare_article_to_market(article_id, max_pages=3):
    """
    Point d'entrée cœur : compare le prix_vente d'un article à la médiane
    du marché Vinted pour ce même titre. Interface JSON structurée
    (task_type implicite ici = "price_intelligence") pour compatibilité
    future avec la couche agent IA (function calling).

    Retour :
    {
        "status": "success" | "error",
        "article_id": ...,
        "nom": ...,
        "prix_vente_actuel": ...,
        "marche": {"median": ..., "mean": ..., "min": ..., "max": ...,
                   "count_total": ..., "count_after_filter": ...},
        "ecart_pct": ...,
        "recommandation": "surestime" | "sous_evalue" | "aligne" | "indetermine",
        "message": ... (uniquement si status == "error")
    }
    """
    article = _get_article(article_id)
    if article is None:
        return {"status": "error", "message": f"Article introuvable (id={article_id})"}

    nom = article.get("nom")
    prix_vente = article.get("prix_vente")

    if not nom:
        return {"status": "error", "message": f"Article {article_id} sans titre (nom) exploitable."}

    auth_data = await _get_auth_data()
    if not auth_data.get("token") or not auth_data.get("session"):
        return {"status": "error", "message": "Échec authentification Vinted"}

    try:
        listings = await search_market_prices_by_filters(
            auth_data,
            catalog_ids=[],
            brand_ids=[],
            size_ids=[],
            status_ids=[],
            color_ids=[],
            search_text=nom,
            max_pages=max_pages,
        )
    except PermissionError:
        logger.info("🔄 [PRICE INTELLIGENCE] 401 reçu — refresh forcé des jetons.")
        auth_data = await _get_auth_data(force_refresh=True)
        if not auth_data.get("token") or not auth_data.get("session"):
            return {"status": "error", "message": "Échec authentification Vinted (après refresh)"}
        try:
            listings = await search_market_prices_by_filters(
                auth_data,
                catalog_ids=[],
                brand_ids=[],
                size_ids=[],
                status_ids=[],
                color_ids=[],
                search_text=nom,
                max_pages=max_pages,
            )
        except PermissionError as e:
            return {"status": "error", "message": str(e)}

    if not listings:
        return {
            "status": "error",
            "message": f"Aucune annonce marché trouvée pour '{nom}'.",
            "article_id": article_id,
            "nom": nom,
            "prix_vente_actuel": prix_vente,
        }

    prices = [l["prix"] for l in listings]
    stats = compute_robust_median(prices)
    recommandation = _compute_recommandation(prix_vente, stats["median"])

    return {
        "status": "success",
        "article_id": article_id,
        "nom": nom,
        "prix_vente_actuel": prix_vente,
        "marche": {
            "median": stats["median"],
            "mean": stats["mean"],
            "min": stats["min"],
            "max": stats["max"],
            "count_total": stats["count_total"],
            "count_after_filter": stats["count_after_filter"],
        },
        "ecart_pct": recommandation["ecart_pct"],
        "recommandation": recommandation["statut"],
    }