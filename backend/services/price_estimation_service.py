import asyncio
import json
import logging
import random
import statistics
import time

from curl_cffi import requests as curl_requests
import google.generativeai as genai

from services.gemini_utils import wait_for_gemini_rate_limit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VINTED_CATALOG_URL = "https://www.vinted.fr/api/v2/catalog/items"
VINTED_SVC_CATALOGUE_URL = "https://api.vinted.fr/svc-catalogue/items"

# --------------------------------------------------------------------------- #
# Cache d'authentification Vinted
# --------------------------------------------------------------------------- #
# fetch_vinted_auth() lance un navigateur headless éphémère (~6-10s, networkidle
# + sleep fixe de 5s) -- trop coûteux pour être rappelé à chaque clic sur le
# bouton d'estimation. Les tokens restent valides plusieurs minutes, d'où ce
# cache mémoire simple avec expiration (pas besoin de persistance disque : un
# redémarrage du serveur force naturellement un refresh).

_auth_cache = {"data": None, "fetched_at": 0}
AUTH_CACHE_TTL_SECONDS = 600  # 10 min
    
async def _get_auth_data(force_refresh=False):
    now = time.time()
    if not force_refresh and _auth_cache["data"] and (now - _auth_cache["fetched_at"] < AUTH_CACHE_TTL_SECONDS):
        return _auth_cache["data"]

    from services.auth_service import AuthService
    auth_manager = AuthService()
    auth_data = await auth_manager.fetch_vinted_auth()

    if auth_data.get("token") and auth_data.get("session"):
        _auth_cache["data"] = auth_data
        _auth_cache["fetched_at"] = now
    return auth_data


# --------------------------------------------------------------------------- #
# Recherche catalogue Vinted
# --------------------------------------------------------------------------- #

async def search_market_prices(query, auth_data, max_pages=3, per_page=96):
    """
    Recherche `query` sur le catalogue Vinted et retourne la liste des prix (float)
    des annonces actives trouvées. Réutilise le pattern d'appel de VintedMasterWorker
    (curl_cffi, impersonate chrome120, mêmes headers d'authentification). Tri par
    prix croissant (plutôt que 'relevance') pour capturer une distribution de prix
    représentative, indispensable au filtrage IQR qui suit.
    """
    session = curl_requests.AsyncSession(impersonate="chrome120")

    # Jar complet (Datadome + session Vinted) plutôt qu'un seul cookie isolé,
    # sinon le WAF détecte une requête "forgée" et renvoie 403 (cf. auth_service.py)
    cookie_header = "; ".join(
        f"{k}={v}" for k, v in auth_data.get("all_cookies", {}).items()
    )

    headers = {
        "Authorization": f"Bearer {auth_data['token']}",
        "Cookie": cookie_header,
        "User-Agent": auth_data["ua"],
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.vinted.fr/catalog",
    }

    prices = []
    page = 1
    for page in range(1, max_pages + 1):
        params = [
            ("search_text", query),
            ("order", "price_low_to_high"),
            ("currency", "EUR"),
            ("per_page", per_page),
            ("page", page),
        ]

        try:
            res = await session.get(VINTED_CATALOG_URL, headers=headers, params=params, timeout=10)
        except Exception as e:
            logger.error(f"❌ [PRICE ESTIMATION] Erreur requête page {page} : {e}")
            break

        if res.status_code == 401:
            raise PermissionError("Session Vinted expirée (401) — jetons à rafraîchir")
        if res.status_code == 429:
            logger.warning("🐢 [PRICE ESTIMATION] Rate limit Vinted — arrêt de la pagination.")
            break
        if res.status_code != 200:
            logger.warning(f"⚠️ [PRICE ESTIMATION] Statut inattendu {res.status_code} page {page}")
            logger.warning(f"   Content-Type : {res.headers.get('content-type')}")
            logger.warning(f"   Corps (500 premiers caractères) : {res.text[:500]}")
            break

        items = res.json().get("items", [])
        if not items:
            break  # plus de résultats, inutile de paginer davantage

        page_items = []
        for item in items:
            amount = item.get("price", {}).get("amount")
            if amount is None:
                continue
            try:
                price = float(amount)
            except (TypeError, ValueError):
                continue
            page_items.append({"titre": item.get("title", "Sans titre"), "prix": price})

        logger.info(f"   📄 Page {page} : {len(page_items)} annonces")
        for it in page_items:
            logger.info(f"      • {it['prix']:>6.2f} € — {it['titre']}")
        prices.extend(page_items)

        await asyncio.sleep(random.uniform(1.5, 3))  # anti-détection, même esprit que sourcing_worker.py

    logger.info(f"📦 [PRICE ESTIMATION] '{query}' → {len(prices)} annonces récupérées sur {page} page(s).")
    return prices


# --------------------------------------------------------------------------- #
# Recherche catalogue Vinted — variante filtrée (structurée, sans texte libre)
# --------------------------------------------------------------------------- #

async def search_market_prices_by_filters(
    auth_data,
    catalog_ids,
    brand_ids,
    size_ids,
    status_ids=None,
    color_ids=None,
    search_text="",
    max_pages=3,
    per_page=96,
):
    """
    Variante filtrée -- utilise le microservice svc-catalogue (api.vinted.fr), distinct
    de l'API www.vinted.fr utilisée par search_market_prices. Découvert via inspection
    du trafic réseau réel : les facettes passent par attribute_ids[xxx] en CSV (valeurs
    jointes par virgules), pas en clés répétées catalog[]=1&catalog[]=2. Auth par cookie
    uniquement (pas de header Authorization), plus x-anon-id / x-csrf-token requis.
    """
    session = curl_requests.AsyncSession(impersonate="chrome120")

    cookie_header = "; ".join(
        f"{k}={v}" for k, v in auth_data.get("all_cookies", {}).items()
    )
    anon_id = auth_data.get("all_cookies", {}).get("anon_id", "")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_header,
        "User-Agent": auth_data["ua"],
        "Referer": "https://www.vinted.fr/",
        "Origin": "https://www.vinted.fr",
        "Locale": "fr-FR",
        "Platform": "web",
        "X-Next-App": "marketplace-web",
        "X-Anon-Id": anon_id,
        "X-Csrf-Token": auth_data.get("csrf_token") or "",
    }

    prices = []
    page = 1
    for page in range(1, max_pages + 1):
        params = [("page", page), ("per_page", per_page), ("search_text", search_text or ""), ("order", "relevance")]
        if catalog_ids:
            params.append(("attribute_ids[catalog]", ",".join(str(x) for x in catalog_ids)))
        if brand_ids:
            params.append(("attribute_ids[brand]", ",".join(str(x) for x in brand_ids)))
        if size_ids:
            params.append(("attribute_ids[size]", ",".join(str(x) for x in size_ids)))
        if status_ids:
            params.append(("attribute_ids[status]", ",".join(str(x) for x in status_ids)))
        if color_ids:
            params.append(("attribute_ids[color]", ",".join(str(x) for x in color_ids)))

        try:
            res = await session.get(VINTED_SVC_CATALOGUE_URL, headers=headers, params=params, timeout=10)
        except Exception as e:
            logger.error(f"❌ [PRICE ESTIMATION] Erreur requête page {page} : {e}")
            break

        if res.status_code == 401:
            raise PermissionError("Session Vinted expirée (401) — jetons à rafraîchir")
        if res.status_code == 403:
            logger.warning(f"⚠️ [PRICE ESTIMATION] 403 -- x-csrf-token probablement absent/expiré. Corps : {res.text[:300]}")
            break
        if res.status_code == 429:
            logger.warning("🐢 [PRICE ESTIMATION] Rate limit Vinted — arrêt de la pagination.")
            break
        if res.status_code != 200:
            logger.warning(f"⚠️ [PRICE ESTIMATION] Statut inattendu {res.status_code} page {page}")
            logger.warning(f"   Corps (500 premiers caractères) : {res.text[:500]}")
            break

        items = res.json().get("items", [])
        if not items:
            break

        page_items = []
        for item in items:
            amount = item.get("price", {}).get("amount")
            if amount is None:
                continue
            try:
                price = float(amount)
            except (TypeError, ValueError):
                continue
            page_items.append({"titre": item.get("title", "Sans titre"), "prix": price})

        logger.info(f"   📄 Page {page} : {len(page_items)} annonces")
        for it in page_items:
            logger.info(f"      • {it['prix']:>6.2f} € — {it['titre']}")
        prices.extend(page_items)

        await asyncio.sleep(random.uniform(1.5, 3))

    # Dédoublonnage : le tri par pertinence n'étant pas stable entre deux appels
    # de pagination successifs, Vinted peut renvoyer les mêmes annonces à cheval
    # sur deux pages (ex: fin de page 1 = début de page 2). On déduplique sur
    # (titre, prix) tout en conservant l'ordre d'apparition.
    seen = set()
    deduped = []
    for item in prices:
        key = (item["titre"], item["prix"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    nb_doublons = len(prices) - len(deduped)
    if nb_doublons:
        logger.info(f"🧹 [PRICE ESTIMATION FILTRÉE] {nb_doublons} doublon(s) écarté(s) (tri pertinence non stable entre pages).")

    logger.info(f"📦 [PRICE ESTIMATION FILTRÉE] → {len(deduped)} annonces récupérées sur {page} page(s).")
    return deduped


# --------------------------------------------------------------------------- #
# Statistiques robustes (filtrage outliers)
# --------------------------------------------------------------------------- #

def compute_robust_median(prices):
    """
    Calcule une médiane robuste : élimine les valeurs aberrantes (méthode IQR,
    bornes Q1-1.5×IQR / Q3+1.5×IQR) avant de calculer médiane/moyenne, pour
    neutraliser les prix anormalement bas (bradage) ou hauts (surestimation).
    """
    if not prices:
        return {
            "median": None, "mean": None, "min": None, "max": None,
            "count_total": 0, "count_after_filter": 0, "prices_filtered": [],
        }

    sorted_prices = sorted(prices)
    count_total = len(sorted_prices)

    if count_total < 4:
        # Pas assez de points pour un IQR fiable -> pas de filtrage
        filtered = sorted_prices
    else:
        q1, _, q3 = statistics.quantiles(sorted_prices, n=4)
        iqr = q3 - q1
        if iqr == 0:
            # Distribution trop concentrée (ex: pic massif Vinted à 5€) -- l'IQR
            # dégénère et ne garderait qu'une seule valeur, ce qui fausse la
            # médiane. On bascule sur un trim par percentile (10%/90%) pour
            # écarter les extrêmes sans effondrer l'échantillon à un point.
            trim_n = max(1, int(count_total * 0.1))
            filtered = sorted_prices[trim_n:-trim_n] if count_total > 2 * trim_n else sorted_prices
        else:
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            filtered = [p for p in sorted_prices if lower_bound <= p <= upper_bound]
            if not filtered:
                filtered = sorted_prices

    return {
        "median": round(statistics.median(filtered), 2),
        "mean": round(statistics.mean(filtered), 2),
        "min": min(filtered),
        "max": max(filtered),
        "count_total": count_total,
        "count_after_filter": len(filtered),
        "prices_filtered": filtered,
    }


# --------------------------------------------------------------------------- #
# Estimation Gemini
# --------------------------------------------------------------------------- #

async def _ask_gemini_price_estimate(query, stats):
    """
    Demande à Gemini un prix de revente conseillé en se basant UNIQUEMENT sur les
    statistiques déjà calculées (pas de scraping brut envoyé) : reste factuel,
    prompt court, et cohérent avec le filtrage outlier déjà effectué côté serveur.
    """
    prompt = f"""Tu es un expert en estimation de prix de revente sur Vinted.

Produit recherché : "{query}"

Données de marché observées (annonces actives, valeurs aberrantes déjà écartées) :
- Prix médian : {stats['median']} €
- Prix moyen : {stats['mean']} €
- Prix minimum (après filtrage) : {stats['min']} €
- Prix maximum (après filtrage) : {stats['max']} €
- Nombre d'annonces prises en compte : {stats['count_after_filter']} (sur {stats['count_total']} trouvées)

Sur la SEULE base de ces chiffres (n'invente aucune autre donnée, ne suppose rien
sur l'état ou la marque au-delà de la requête ci-dessus), propose :
1. Un prix de mise en ligne conseillé, en euros.
2. Une justification en une phrase.

Réponds UNIQUEMENT en JSON strict, sans backticks ni texte autour, au format :
{{"prix_conseille": <nombre>, "justification": "<texte>"}}"""

    wait_for_gemini_rate_limit()
    model = genai.GenerativeModel("gemini-2.5-flash")
    generation_config = genai.types.GenerationConfig(temperature=0.1)
    response = model.generate_content(prompt, generation_config=generation_config)

    try:
        clean = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        logger.warning(f"⚠️ [PRICE ESTIMATION] Réponse Gemini non parsable : {e}")
        return {"prix_conseille": None, "justification": response.text[:300]}


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #

async def estimate_price(query, max_pages=3):
    """Auth (cache 10 min) -> recherche -> filtrage IQR -> estimation Gemini."""
    auth_data = await _get_auth_data()
    if not auth_data.get("token") or not auth_data.get("session"):
        return {"status": "error", "message": "Échec authentification Vinted"}

    try:
        listings = await search_market_prices(query, auth_data, max_pages=max_pages)
    except PermissionError:
        logger.info("🔄 [PRICE ESTIMATION] 401 reçu — refresh forcé des jetons.")
        auth_data = await _get_auth_data(force_refresh=True)
        if not auth_data.get("token") or not auth_data.get("session"):
            return {"status": "error", "message": "Échec authentification Vinted (après refresh)"}
        try:
            listings = await search_market_prices(query, auth_data, max_pages=max_pages)
        except PermissionError as e:
            return {"status": "error", "message": str(e)}

    if not listings:
        return {"status": "error", "message": "Aucune annonce trouvée pour cette recherche."}

    prices = [l["prix"] for l in listings]
    stats = compute_robust_median(prices)
    gemini_estimate = await _ask_gemini_price_estimate(query, stats)

    return {
        "status": "success",
        "query": query,
        "stats": stats,
        "gemini_estimate": gemini_estimate,
    }