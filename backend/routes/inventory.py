import re
from fastapi import APIRouter, HTTPException, Body, BackgroundTasks, Request, UploadFile, File
import google.generativeai as genai

from services.supabase_service import SupabaseService

from services.automation_scheduler import (
    schedule_republish,
    get_scheduled_republish,
    get_task_history,
)
import database
import logging
from services.clemz_automation import ClemzAutomation

# Importez le nouveau service
from services.vinted_scraper import VintedScraper
from database import GEMINI_API_KEY
import traceback



genai.configure(api_key=GEMINI_API_KEY)

import json
import os
import time

from services.gemini_utils import wait_for_gemini_rate_limit as _wait_for_gemini_rate_limit

router = APIRouter(prefix="/api", tags=["Inventory"])
supabase_svc = SupabaseService()
scraper = VintedScraper()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 1. Configuration du log dédié à l'extension
ext_logger = logging.getLogger('extension_logger')
handler = logging.FileHandler('extension_errors.log')
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
ext_logger.addHandler(handler)
ext_logger.setLevel(logging.ERROR)
from datetime import datetime



@router.get("/state")
async def get_system_state():
    file_path = "cron_status.json"
    # Valeurs par défaut
    state = {
        "last_cron_14h": "---",
        "last_cron_22h": "---",
        "last_manual_scrap": "---",
        "last_clemz_sync": "---",
        "is_clemz_running": database.app_state.get("is_clemz_running", False), # Indicateur d'exécution
        "status_TEST": database.app_state.get("status_TEST", "En attente"),
        "has_error": database.app_state.get("has_error", False),
        "session_status": database.app_state.get(
            "session_status", {"dressing1": None, "dressing2": None}
        ),
        "reconnect_status": database.app_state.get(
            "reconnect_status", {"dressing1": None, "dressing2": None}
        ),
    }
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # Récupération de la dernière exécution brute
                last_exec = data.get("last_execution")
                
                if last_exec:
                    # Conversion de la string ISO en objet datetime
                    # Exemple: "2026-04-07T22:09:19.712214"
                    dt_exec = datetime.fromisoformat(last_exec)
                    time_str = dt_exec.strftime("%H:%M") # "22:09"
                    
                    # Logique d'attribution selon l'heure
                    hour = dt_exec.hour
                    if 13 <= hour <= 15:
                        state["last_cron_14h"] = time_str
                    elif 21 <= hour <= 23:
                        state["last_cron_22h"] = time_str
                    else:
                        # Si c'est hors créneau auto, on le met en manuel
                        state["last_manual_scrap"] = time_str

        except Exception as e:
            logger.error(f"⚠️ Erreur lecture cron_status.json : {e}")
            
    return state

@router.get("/check-all-sessions")
async def check_all_sessions_route():
    """
    Vérifie l'état réel des 4 sessions Vinted (scraping D1/D2 + Clemz D1/D2)
    en mode invisible, SANS déclencher de reconnexion. Diagnostic ponctuel,
    à appeler manuellement pour savoir où on en est avant de relancer quoi
    que ce soit.
    """
    from services.session_manager import check_all_sessions
    results = await check_all_sessions()
    return {"session_status": results}


@router.post("/reconnect-session/{dressing}")
async def reconnect_session_route(dressing: str, background_tasks: BackgroundTasks):
    """
    Déclenche manuellement la reconnexion d'un dressing (D1 ou D2) sans lancer
    tout le scraping. Ouvre le navigateur visible + notification Windows si la
    session est expirée, attend la reconnexion manuelle (jusqu'à ~3 min).
    Appelée par le bouton "Reconnecter" affiché quand une session est déco.
    """
    account_map = {"d1": "chrome_clemz", "d2": "edge_clemz"}
    state_key_map = {"d1": "dressing1", "d2": "dressing2"}

    account_key = account_map.get(dressing)
    if not account_key:
        raise HTTPException(status_code=400, detail="Dressing inconnu (attendu : d1 ou d2)")

    state_key = state_key_map[dressing]

    async def run_reconnect():
        from services.session_manager import ensure_session
        database.app_state["reconnect_status"][state_key] = "en_cours"
        try:
            ok = await ensure_session(account_key)
            database.app_state["session_status"][state_key] = ok
            database.app_state["reconnect_status"][state_key] = "succes" if ok else "echec"
            database.app_state["has_error"] = any(
                v is False for v in database.app_state["session_status"].values()
            )
        except Exception as e:
            logger.error(f"❌ Erreur reconnexion {dressing} : {e}")
            database.app_state["reconnect_status"][state_key] = "echec"

    background_tasks.add_task(run_reconnect)
    return {"status": "started", "dressing": dressing}

@router.post("/automation/schedule-republish")
async def schedule_republish_route(payload: dict = Body(...)):
    """
    Enregistre la liste d'articles à republier dans la fenêtre 14h-19h
    (scheduled_republish_midi.json). Appelée depuis le dashboard quand
    l'utilisateur clique sur "Programmer".

    Payload attendu :
    {
        "items": [
            {"id": "...", "nom": "...", "dressing": "Dressing 1"},
            ...
        ]
    }
    """
    items = payload.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="La liste d'articles est vide.")

    task = schedule_republish(items)
    return {
        "status": "scheduled",
        "message": f"{len(task['midi']['items'])} article(s) Dressing 1 (11h30-12h30) et {len(task['soir']['items'])} article(s) Dressing 2 (17h00-18h00) programmés.",
        "task": task,
    }

@router.get("/automation/scheduled")
async def get_scheduled_route():
    """
    Retourne la tâche de republication programmée pour aujourd'hui, ou null si aucune.
    Utilisée par le dashboard pour afficher l'état de la programmation en cours.
    """
    task = get_scheduled_republish()
    return {"task": task}

# new_str
@router.get("/automation/history")
async def get_task_history_route(limit: int = 50, task_type: str = None):
    """
    Retourne l'historique des tâches d'automatisation (republication, baisse de
    prix, partage vues/favoris, scraping). Utilisée par la section de suivi
    TaskHistory du dashboard React.

    Paramètres optionnels :
    - limit : nombre maximum d'entrées retournées (défaut 50)
    - task_type : filtre par type exact ("republication", "baisse_prix",
      "partage_vues_favoris", "scraping", etc. — comparaison stricte sur le
      champ "type" stocké dans l'historique)
    """
    history = get_task_history(limit=limit, task_type=task_type)
    return {"history": history}

@router.post("/test-extension")
async def test_script_python(background_tasks: BackgroundTasks):
    async def run_and_report():
        try:
            # ERREUR ICI : On appelait scrap_vinted() au lieu de run_full_sync()
            # On utilise une petite astuce pour récupérer le nombre d'articles
            # tout en lançant la synchronisation complète.
            
            logger.info("🧪 Lancement du test manuel (Scrap + Supabase)...")
            
            # On lance la procédure complète
            await scraper.run_full_sync() #
            
            # Pour l'affichage sur le Dashboard
            database.app_state["status_TEST"] = "Synchronisation Réussie"
            database.app_state["has_error"] = False
            database.app_state["last_run_slot"] = "TEST"
            
        except Exception as e:
            logger.error(f"❌ Erreur Test : {e}")
            database.app_state["status_TEST"] = "Erreur Système"
            database.app_state["has_error"] = True

    background_tasks.add_task(run_and_report)
    return {"status": "started"}

@router.get("/inventory")
async def get_inventory():
    """Récupère l'inventaire traité avec le Real Score."""
    return await supabase_svc.get_processed_inventory()

@router.post("/achats/lot")
async def create_lot_achat_route(payload: dict = Body(...)):
    """
    Crée un lot d'achat avec répartition automatique des frais (protection
    acheteur + port) au prorata du prix négocié de chaque pièce.

    Payload attendu :
    {
        "date_achat": "2026-08-11",
        "frais_protection_acheteur": 3.65,
        "frais_port": 4.65,
        "montant_porte_monnaie": 53.82,
        "vendeur_vinted": "nom_optionnel",
        "notes": "optionnel",
        "articles": [{"nom": "Lacoste S", "prix_brut": 8.00}, ...]
    }
    """
    try:
        result = supabase_svc.create_lot_achat(
            date_achat=payload.get("date_achat"),
            frais_protection_acheteur=payload.get("frais_protection_acheteur", 0),
            frais_port=payload.get("frais_port", 0),
            articles=payload.get("articles", []),
            montant_porte_monnaie=payload.get("montant_porte_monnaie", 0),
            vendeur_vinted=payload.get("vendeur_vinted"),
            notes=payload.get("notes"),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Erreur création lot d'achat : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/achats/lots")
async def get_lots_achats_route():
    """Retourne l'historique des lots d'achat avec leurs articles."""
    return {"lots": supabase_svc.get_lots_achats()}


@router.post("/ventes/import-csv")
async def import_ventes_csv_route(file: UploadFile = File(...)):
    """
    Importe un export CSV Clemz des ventes (colonnes : Numéro de commande,
    Date d'achat, Date d'encaissement, Dressing, Titre, Client,
    Pays de l'acheteur, Articles TTC, % Réduction). Pour chaque vente, le
    prix d'achat est recherché par correspondance de nom dans la table achats.
    """
    import pandas as pd
    import io

    try:
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8-sig")

        required_cols = {
            "Numéro de commande", "Date d'achat", "Date d'encaissement",
            "Dressing", "Titre", "Client", "Pays de l'acheteur",
            "Articles TTC", "% Réduction",
        }
        missing = required_cols - set(df.columns)
        if missing:
            raise HTTPException(status_code=400, detail=f"Colonnes manquantes dans le CSV : {missing}")

        rows = []
        for _, r in df.iterrows():
            rows.append({
                "numero_commande":       r["Numéro de commande"],
                "date_achat":            pd.to_datetime(r["Date d'achat"]).strftime("%Y-%m-%d"),
                "date_encaissement":     pd.to_datetime(r["Date d'encaissement"]).strftime("%Y-%m-%d") if pd.notna(r["Date d'encaissement"]) else None,
                "dressing":              r["Dressing"],
                "titre":                 str(r["Titre"]),
                "client":                r["Client"],
                "pays_acheteur":         r["Pays de l'acheteur"],
                "prix_vente":            float(r["Articles TTC"]),
                "pourcentage_reduction": float(r["% Réduction"]) if pd.notna(r["% Réduction"]) else 0,
            })

        result = supabase_svc.import_ventes_from_csv(rows)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Erreur import CSV ventes : {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ventes")
async def get_ventes_route():
    """Retourne l'historique complet des ventes."""
    return {"ventes": supabase_svc.get_ventes()}


@router.get("/price-intelligence/{article_id}")
async def get_price_intelligence(article_id: str):
    """
    Compare le prix_vente d'un article à la médiane du marché Vinted (recherche
    par titre via svc-catalogue). Appelée depuis le bouton "Analyser prix marché"
    sur ProductCard.jsx.
    """
    from services.price_intelligence import compare_article_to_market

    result = await compare_article_to_market(article_id)
    if result["status"] != "success":
        raise HTTPException(status_code=400, detail=result.get("message", "Erreur inconnue"))
    return result

@router.patch("/treat/{article_id}")
async def treat_article(article_id: str):
    """Marque un article comme traité."""
    return supabase_svc.update_article(article_id, {"est_traite": True})

@router.patch("/treat-multiple")
async def treat_multiple_articles(payload: dict = Body(...)):
    """Marque plusieurs articles comme traités."""
    article_ids = payload.get("ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="Aucun ID fourni.")
    
    success_count = 0
    for article_id in article_ids:
        try:
            # 🚨 CORRECTION : Ajout de l'enveloppe "properties" ici aussi
            supabase_svc.update_article(article_id, {"est_traite": True})
            success_count += 1
        except Exception as e:
            logger.error(f"Erreur lors du traitement groupé de {article_id} : {e}")
            
    return {"status": "success", "message": f"{success_count} articles traités."}

@router.patch("/exclusion-saisonniere/{article_id}")
async def toggle_exclusion_saisonniere(article_id: str, payload: dict = Body(...)):
    """
    Marque/démarque un article comme exclu manuellement de la liquidation pour
    raison saisonnière (ex: vêtement d'hiver en plein été -- redeviendra
    vendable normalement, pas besoin de le brader). Persisté en base pour
    rester effectif d'une session à l'autre.
    Payload attendu : { "exclu": true | false }
    """
    exclu = payload.get("exclu", True)
    return supabase_svc.update_article(article_id, {"exclu_liquidation_saisonnier": exclu})

@router.patch("/reporter-republication/{article_id}")
async def reporter_republication(article_id: str):
    """
    Reporte la republication de cet article au lendemain -- disparaît de la
    liste Republications aujourd'hui, réapparaît automatiquement demain sans
    action manuelle (contrairement aux exclusions saisonnière/perso qui restent
    jusqu'à réintégration manuelle). Date calculée côté serveur pour éviter tout
    décalage lié au fuseau horaire du navigateur.
    """
    from datetime import date, timedelta
    demain = (date.today() + timedelta(days=1)).isoformat()
    return supabase_svc.update_article(article_id, {"report_republication_jusqu_au": demain})

@router.patch("/republish-multiple")
async def republish_multiple_articles(payload: dict = Body(...)):
    """Met à jour la Date de republication à aujourd'hui pour plusieurs articles."""
    article_ids = payload.get("ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="Aucun ID fourni.")
    
    # On récupère la date du jour au format YYYY-MM-DD
    today = datetime.now().strftime("%Y-%m-%d")
    success_count = 0
    
    for article_id in article_ids:
        try:
            supabase_svc.update_article(article_id, {
                "date_republication": today
            })
            success_count += 1
        except Exception as e:
            logger.error(f"Erreur lors de la republication de {article_id} : {e}")
            
    return {"status": "success", "message": f"{success_count} articles republiés."}

@router.patch("/republish-by-name")
async def republish_by_name(payload: dict = Body(...)):
    """Met à jour la date de republication via le nom du produit."""
    item_name = payload.get("nom")
    if not item_name:
        raise HTTPException(status_code=400, detail="Nom manquant")
    result = supabase_svc.republish_by_name_logic(item_name)
    success = result.get("status") == "success"

    if success:
        return {"status": "success", "message": f"{item_name} mis à jour"}
    else:
        raise HTTPException(status_code=404, detail="Article non trouvé")

@router.delete("/clear-database")
async def clear_inventory_database():
    """Route pour vider toutes les données de la base."""
    try:
        result = supabase_svc.clear_articles_database()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/automation/baisse-prix")
async def start_baisse_prix_vinted(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    """
    Déclenche la baisse de prix SEGMENTÉE (-10%/-20% selon le cas, cooldown 7j,
    exclusion audit/plancher) sur la sélection faite depuis le dashboard --
    aligné sur la même logique que le job automatique quotidien (14h05), plutôt
    qu'un flat -20% sur toute la sélection comme avant.
    """
    from services.automation_service import run_baisse_prix_auto

    articles_bruts = payload.get("produits", [])
    if not articles_bruts:
        raise HTTPException(status_code=400, detail="La liste de produits est vide.")

    ids_restriction = [a["id"] for a in articles_bruts if isinstance(a, dict) and a.get("id")]
    background_tasks.add_task(run_baisse_prix_auto, ids_restriction)
    return {"status": "started", "count": len(ids_restriction)}

@router.post("/automation/run-now")
async def run_automation_now(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    """
    Déclenche IMMÉDIATEMENT une tâche Clemz (republication OU baisse de prix), sans
    passer par la programmation créneau midi/soir — pour tests manuels depuis le dashboard.
    payload: {"produits": [{"id", "nom", "dressing", "pourcentage"?}, ...], "task_type": "republication"|"baisse_prix"}

    Pour "baisse_prix" : aligné sur la logique segmentée (-10%/-20%, cooldown 7j,
    exclusion audit/plancher) -- redirige vers run_baisse_prix_auto() restreint à
    la sélection envoyée, plutôt qu'un flat -20% comme avant.

    Chaque produit du panier de baisse manuelle peut porter son PROPRE
    "pourcentage" (ex. tranches de prix pépite, cf. échange du 10/09/2026) --
    les articles sont alors regroupés par pourcentage effectif avant d'être
    découpés en lots, plutôt qu'un taux unique appliqué à tout le panier. Le
    champ "pourcentage" au niveau racine du payload reste un fallback global
    (rétro-compatibilité avec un éventuel appelant qui n'enverrait qu'un seul
    taux pour tout le lot).
    """
    from services.automation_service import run_baisse_prix_auto

    articles_bruts = payload.get("produits", [])
    task_type = payload.get("task_type")
    pourcentage_manuel = payload.get("pourcentage")  # fallback global, baisse de prix manuelle
    # Override manuel et délibéré du repos forcé Niveau 2 (coupure sur volume
    # glissant) -- test du 15/09/2026 avec la nouvelle extension Clemz, à
    # l'origine réservé à la republication. Étendu à baisse_prix le 26/09/2026
    # sur demande explicite et ponctuelle de l'utilisateur ("exceptionnellement").
    # Ne touche jamais à la convalescence Niveau 1 (vraie suspension constatée),
    # cf. get_allowed_quantity(ignorer_repos_niveau2=...) -- ce garde-fou reste
    # entier quel que soit task_type.
    forcer_niveau2 = bool(payload.get("forcer_niveau2"))

    if not articles_bruts:
        raise HTTPException(status_code=400, detail="La liste de produits est vide.")
    if task_type not in ("republication", "baisse_prix"):
        raise HTTPException(status_code=400, detail="task_type doit être 'republication' ou 'baisse_prix'.")

    a_un_pourcentage = task_type == "baisse_prix" and any(
        isinstance(a, dict) and a.get("pourcentage") is not None for a in articles_bruts
    )

    if task_type == "baisse_prix" and pourcentage_manuel is None and not a_un_pourcentage:
        # Flux automatique historique (bouton "Forcer baisse de prix", articles
        # is_low_perf détectés) -- reste inchangé, toujours redirigé vers
        # run_baisse_prix_auto avec sa propre logique de cooldown 7j. Le cas
        # "pourcentage fourni" (panier de baisse manuelle, global ou par
        # article) continue plus bas, vers run_now_task/ClemzAutomation.
        ids_restriction = [a["id"] for a in articles_bruts if isinstance(a, dict) and a.get("id")]
        background_tasks.add_task(run_baisse_prix_auto, ids_restriction)
        return {"status": "started", "count": len(ids_restriction)}

    # Regroupe par pourcentage EFFECTIF (celui de l'article, sinon le fallback
    # global) -- un seul groupe pour "republication" (pas de notion de taux) ou
    # pour l'ancien flux à taux unique, potentiellement plusieurs groupes pour
    # le panier de baisse manuelle où chaque article porte son propre taux.
    groupes_par_pourcentage = {}
    for a in articles_bruts:
        if not isinstance(a, dict):
            continue
        pct = a.get("pourcentage", pourcentage_manuel) if task_type == "baisse_prix" else None
        groupes_par_pourcentage.setdefault(pct, []).append(a)

    # Clemz ne supporte de façon fiable qu'un maximum de MAX_PAR_LOT articles par
    # dressing dans une même sélection/republication -- au-delà, risque
    # d'instabilité côté extension. Constaté le 27/08/2026 avec 97 republications
    # en attente. On découpe donc en lots de 10 max par dressing (par groupe de
    # pourcentage), traités SÉQUENTIELLEMENT (un lot entier terminé avant de
    # démarrer le suivant), sous un seul task_id pour garder un suivi
    # TaskHistory consolidé plutôt que ~10 entrées séparées.
    #
    # Le quota anti-détection (risk_guard) n'est pas couplé explicitement à ce
    # découpage : il s'applique simplement à CHAQUE lot au moment de son
    # traitement. Comme risk_guard se base sur le volume réellement loggé en
    # base (pas un compteur en mémoire propre à cet appel), un lot traité après
    # un autre déjà comptabilisé (même d'un groupe de pourcentage différent)
    # voit automatiquement moins de quota restant -- l'effet cumulatif est
    # naturel, sans lien de code entre les deux.
    MAX_PAR_LOT = 10

    def _chunk(lst, n):
        return [lst[i:i + n] for i in range(0, len(lst), n)] or [[]]

    # Liste de (pourcentage, lot1, lot2) à traiter dans l'ordre.
    lots_a_traiter = []
    for pct, items_groupe in groupes_par_pourcentage.items():
        produits_d1 = [a["nom"] for a in items_groupe if a.get("dressing") == "Dressing 1"]
        produits_d2 = [a["nom"] for a in items_groupe if a.get("dressing") == "Dressing 2"]
        lots_d1 = _chunk(produits_d1, MAX_PAR_LOT)
        lots_d2 = _chunk(produits_d2, MAX_PAR_LOT)
        for lot_index in range(max(len(lots_d1), len(lots_d2))):
            lot1 = lots_d1[lot_index] if lot_index < len(lots_d1) else []
            lot2 = lots_d2[lot_index] if lot_index < len(lots_d2) else []
            if lot1 or lot2:
                lots_a_traiter.append((pct, lot1, lot2))

    def _plafonner_par_plan_du_jour(dressing, task_type, allowed):
        """
        Réduit encore `allowed` (déjà limité par le quota brut DAILY_THRESHOLDS)
        selon le volume_cible du plan du jour actif -- le bouton manuel
        n'appliquait jusqu'ici QUE le quota brut, jamais le tirage aléatoire du
        calendrier anti-détection (Niveau 3), ce qui le rendait inopérant dès
        qu'on ne passait pas par le cron automatique de midi/soir (cf. échange
        du 11/09/2026 : republier au max du quota brut via le bouton manuel
        plusieurs jours de suite recrée exactement le pic de volume que le
        calendrier était censé éviter).

        Ne s'applique qu'à la republication : aucun plan_du_jour n'est jamais
        généré avec action_type='baisse_prix' (generer_plans_du_jour() ne
        tire que pour la republication), donc rien à plafonner ici pour
        baisse_prix au-delà du quota brut déjà appliqué par get_allowed_quantity.

        CORRIGÉ (11/09/2026) : le plan du jour n'est généré qu'une fois à
        00:05 -- si un repos Niveau 1 (convalescence) ou Niveau 2 (coupure
        volume) mis en cache à ce moment-là n'est PLUS vrai maintenant (palier
        arrivé à échéance en cours de journée, volume glissant retombé sous le
        seuil...), `allowed` reçu en paramètre le reflète déjà, puisqu'il vient
        de get_allowed_quantity() recalculé EN DIRECT juste avant cet appel :
        s'il est > 0 alors que le plan dit "repos" pour une raison Niveau 1/2,
        c'est que cette raison est devenue obsolète -- on ne bloque pas
        artificiellement jusqu'à minuit (incohérence constatée : baisse_prix,
        jamais mis en cache, redevenait dispo immédiatement, pas la
        republication). Le repos Niveau 3 (calendrier, tirage indépendant du
        quota) reste lui pleinement valable et n'est jamais remis en cause ici.
        """
        from services.automation_scheduler import get_plan_du_jour
        from services.risk_guard import get_volume_depuis_minuit
        from services.planification_republication import rafraichir_repos_si_leve

        if task_type != "republication" or allowed <= 0:
            return allowed, None

        rafraichir_repos_si_leve(dressing, task_type)
        plan = get_plan_du_jour(dressing)
        if plan is None or plan.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return allowed, None  # pas de plan pour aujourd'hui -- rien de plus à plafonner

        if not plan.get("actif"):
            if plan.get("niveau") in ("convalescence", "repos_force"):
                return allowed, None  # repos Niveau 1/2 mis en cache, mais plus vrai live -- plan obsolète
            from services.plan_source import PLAN_INDISPONIBLE
            if plan.get("niveau") == PLAN_INDISPONIBLE:
                return 0, (
                    f"Plan du jour de la VM illisible (VM éteinte, backend arrêté ou pas "
                    f"encore de plan aujourd'hui) -- republication manuelle de {dressing} "
                    f"bloquée depuis ce PC tant que le plan de référence n'est pas lisible."
                )
            return 0, (
                f"Jour de repos décidé par le calendrier anti-détection (niveau "
                f"'{plan.get('niveau')}') pour {dressing} -- aucune republication "
                f"manuelle aujourd'hui, même si le quota brut le permettrait."
            )

        deja_fait = get_volume_depuis_minuit(dressing, "republication")
        reste_cible = max(0, plan["volume_cible"] - deja_fait)
        if reste_cible < allowed:
            return reste_cible, (
                f"Volume cible du jour atteint pour {dressing} ({plan['volume_cible']} "
                f"article(s), calendrier anti-détection) -- le reste est reporté, "
                f"même si le quota brut n'est pas encore épuisé."
            )
        return allowed, None

    async def run_now_task(items, task_type, lots_a_traiter, forcer_niveau2=False):
        from services.automation_scheduler import start_task_run, update_task_result, finish_task_run, add_task_global_anomaly, fail_pending_results
        from services.risk_guard import get_allowed_quantity, log_action, get_jour_pause_volume

        task_id = start_task_run(task_type, items)
        if forcer_niveau2:
            for d in ("Dressing 1", "Dressing 2"):
                if get_jour_pause_volume(d):
                    logger.warning(
                        f"⚠️ [OVERRIDE TEST] Repos Niveau 2 outrepassé manuellement pour {d} "
                        f"(task_id={task_id}) -- test extension Clemz, cf. échange du 15/09/2026."
                    )
        try:
            for pct, lot1, lot2 in lots_a_traiter:
                allowed_d1, truncated_d1, reason_d1 = get_allowed_quantity("Dressing 1", task_type, len(lot1), ignorer_repos_niveau2=forcer_niveau2)
                allowed_d2, truncated_d2, reason_d2 = get_allowed_quantity("Dressing 2", task_type, len(lot2), ignorer_repos_niveau2=forcer_niveau2)

                allowed_d1, reason_calendrier_d1 = _plafonner_par_plan_du_jour("Dressing 1", task_type, allowed_d1)
                if reason_calendrier_d1:
                    truncated_d1, reason_d1 = True, reason_calendrier_d1
                allowed_d2, reason_calendrier_d2 = _plafonner_par_plan_du_jour("Dressing 2", task_type, allowed_d2)
                if reason_calendrier_d2:
                    truncated_d2, reason_d2 = True, reason_calendrier_d2

                excluded_d1 = lot1[allowed_d1:]
                excluded_d2 = lot2[allowed_d2:]
                lot1 = lot1[:allowed_d1]
                lot2 = lot2[:allowed_d2]

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

                if not lot1 and not lot2:
                    continue

                bot_kwargs = {"pourcentage_baisse_prix": pct} if (task_type == "baisse_prix" and pct is not None) else {}
                bot = ClemzAutomation(lot1, lot2, task_type=task_type, **bot_kwargs)
                results = await bot.run()

                # CORRIGÉ (11/09/2026) : le quota compte le nombre d'items ayant
                # RÉELLEMENT reçu un résultat de Clemz (présents dans
                # selection_results), pas la taille brute du lot envoyé --
                # une interruption en cours d'exécution (ex: navigateur fermé
                # manuellement pendant le run) peut faire revenir des
                # selection_results VIDES sans lever d'exception (catchée en
                # interne par ClemzAutomation) : décompter tout le lot dans ce
                # cas gaspillerait du quota anti-détection pour un envoi qui
                # n'a concrètement rien délivré à Vinted.
                #
                # RE-CORRIGÉ (21/09/2026) : "présent dans selection_results" ne
                # veut pas dire "réussi" -- chaque item y apparaît avec son
                # propre status "success"/"failed" (cf. docstring de
                # ClemzAutomation.run()), y compris les échecs individuels en
                # cours de lot (ex: panneau Clemz perdu à mi-parcours, qui a
                # fait compter 7 republications alors que seules 4 avaient
                # réellement été faites sur Vinted, bloquant à tort les
                # tentatives suivantes du même jour). Ne compter que les
                # succès réels, pas la taille de la liste de résultats.
                traites_d1 = traites_d2 = 0
                for account_result in results:
                    nb_traites = sum(
                        1 for r in account_result.get("selection_results", [])
                        if r.get("status") == "success"
                    )
                    if "Dressing 1" in account_result.get("account", ""):
                        traites_d1 += nb_traites
                    elif "Dressing 2" in account_result.get("account", ""):
                        traites_d2 += nb_traites

                log_action("Dressing 1", task_type, traites_d1)
                log_action("Dressing 2", task_type, traites_d2)

                ids_avec_resultat = set()
                for account_result in results:
                    for item_result in account_result.get("selection_results", []):
                        matched = next((it for it in items if it["nom"] == item_result["nom"]), None)
                        item_id = matched["id"] if matched else item_result["nom"]
                        ids_avec_resultat.add(item_id)
                        update_task_result(
                            task_id,
                            item_id,
                            status=item_result["status"],
                            reason=item_result.get("reason"),
                        )

                    # Erreurs majeures Clemz ignorées en cours de route (ancienne annonce
                    # supprimée, nouvelle jamais créée) -- remontées séparément des
                    # selection_results puisqu'elles ne correspondent pas forcément à un
                    # article de CETTE sélection (résidu de liste possible).
                    repost = account_result.get("repost_result") or {}
                    for erreur in repost.get("erreurs_majeures", []):
                        # account_result["account"] est le nom INTERNE du compte Clemz
                        # (ex: "Chrome - Dressing 2"), pas le libellé normalisé utilisé
                        # partout ailleurs dans le dashboard -- extrait juste "Dressing N"
                        # pour un badge cohérent avec le reste de TaskHistory (cf. échange
                        # du 18/09/2026), en retombant sur la valeur brute si jamais le nom
                        # de compte ne suit pas ce format.
                        match_dressing = re.search(r"Dressing\s*\d+", account_result["account"], re.IGNORECASE)
                        dressing_normalise = match_dressing.group(0) if match_dressing else account_result["account"]
                        add_task_global_anomaly(
                            task_id,
                            label=f"Erreur majeure ignorée : {erreur['nom']}",
                            dressing=dressing_normalise,
                            reason=f"Ancienne annonce supprimée, nouvelle non créée — {erreur.get('url') or 'URL inconnue'}. Intervention manuelle nécessaire.",
                            category="erreur_clemz",
                        )

                # Items du lot n'ayant reçu AUCUN résultat (ex: navigateur fermé
                # manuellement avant que Clemz ne rende son verdict) -- sans ce
                # rattrapage, ils restent "pending" indéfiniment dans l'historique,
                # laissant croire qu'un traitement est encore en cours.
                for nom in (lot1 + lot2):
                    matched = next((it for it in items if it["nom"] == nom), None)
                    item_id = matched["id"] if matched else nom
                    if item_id not in ids_avec_resultat:
                        update_task_result(
                            task_id, item_id, status="failed",
                            reason="Aucun résultat reçu de Clemz — la session a probablement été interrompue en cours de route.",
                        )
        except Exception as e:
            # Trace complète (jusqu'ici seul le message était loggé : impossible de
            # savoir quel appel avait levé WinError 10035 le 20/09/2026), et seuls les
            # items encore en attente passent en échec -- les résultats déjà reçus de
            # Clemz (success/skipped) ne doivent pas être écrasés par cette erreur.
            logger.exception(f"❌ Erreur run-now ({task_type}) : {e}")
            fail_pending_results(task_id, str(e)[:120])
        finally:
            finish_task_run(task_id)
            # Action manuelle déclenchée hors du cycle minuit -- si elle vient de
            # provoquer une convalescence ou une coupure de volume glissant, le
            # dashboard doit le refléter immédiatement plutôt que d'attendre le
            # prochain plan généré à minuit (cf. échange du 10/09/2026).
            from services.planification_republication import rafraichir_pause_si_necessaire
            rafraichir_pause_si_necessaire("Dressing 1")
            rafraichir_pause_si_necessaire("Dressing 2")

    background_tasks.add_task(run_now_task, articles_bruts, task_type, lots_a_traiter, forcer_niveau2)
    return {
        "status": "started",
        "task_type": task_type,
        "counts": {
            "d1": sum(len(l1) for _, l1, _ in lots_a_traiter),
            "d2": sum(len(l2) for _, _, l2 in lots_a_traiter),
        },
        "lots": {
            "total": len(lots_a_traiter),
            "pourcentages": sorted({pct for pct, _, _ in lots_a_traiter if pct is not None}),
        },
    }


MIN_ARTICLES_PAR_GROUPE_LIQUIDATION = 3  # abaissé de 5 à 3 le 05/10/2026 (stock dormant trop dispersé par prix pour atteindre 5) -- sous ce seuil, on attend d'avoir plus d'articles au même prix fixe plutôt que d'ouvrir un cycle Playwright pour 1-2 articles

# Formule déplacée dans SupabaseService.calculer_prix_fixe_liquidation (03/10/2026) --
# seule source de vérité, réutilisée aussi par liquidation_needs_action côté
# get_processed_inventory() pour savoir quand un article sort de la liste
# "à traiter" après un passage par ce regroupement.


@router.post("/liquidation/run-now")
async def run_liquidation_now(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    """
    Déclenche la baisse de prix de liquidation pour le stock dormant (45j+,
    phases 0 à 3 -- cf. SupabaseService._get_liquidation_target).
    Reçoit une liste de produits avec leur prix cible (liquidation_prix_cible),
    et applique l'Option 3 (remplace l'Option 2 par pourcentage, abandonnée le
    03/10/2026) : regroupement par PRIX FIXE -- cf. calculer_prix_fixe_liquidation().
    Chaque groupe est envoyé à Clemz en UNE SEULE action "prix fixe" (mode natif
    "set", pas une baisse relative en %), donc tous les articles d'un groupe se
    retrouvent au MÊME prix final, pas juste au même pourcentage de baisse.

    Seuil MIN_ARTICLES_PAR_GROUPE_LIQUIDATION : les groupes trop petits ne
    déclenchent PAS de cycle Playwright -- pas d'erreur, ils sont simplement
    laissés de côté. Comme rien n'est stocké en état (liquidation_phase/
    liquidation_prix_cible recalculés à chaque fois depuis jours_en_vente),
    ils seront réévalués naturellement au prochain clic, sans action requise.

    Groupes formés par (dressing, prix fixe) depuis le 08/10/2026 -- le seuil
    s'applique à CHAQUE dressing séparément : avant, 2 articles D1 + 1 article
    D2 au même prix franchissaient le seuil ensemble, puis l'exécution (déjà
    séparée par dressing) ouvrait quand même un cycle Playwright pour 1 seul
    article D2, exactement ce que le seuil est censé éviter.
    payload["dressing"] optionnel ("Dressing 1"/"Dressing 2") : ne lance que ce
    dressing (absent = les deux, D1 puis pause 5-10 min puis D2).
    payload["forcer_seuil"] optionnel (08/10/2026, choix manuel et délibéré de
    l'utilisateur) : lance aussi les groupes sous le seuil -- un cycle Clemz par
    prix fixe, même pour 1 seul article.
    """
    produits = payload.get("produits", [])
    dressing_demande = payload.get("dressing")
    forcer_seuil = bool(payload.get("forcer_seuil"))
    if dressing_demande not in (None, "Dressing 1", "Dressing 2"):
        raise HTTPException(status_code=400, detail=f"Dressing inconnu : {dressing_demande}")
    if dressing_demande:
        produits = [p for p in produits if p.get("dressing") == dressing_demande]
    if not produits:
        raise HTTPException(status_code=400, detail="La liste de produits est vide.")

    groupes_bruts = {}
    for p in produits:
        prix_cible = p.get("prix_cible")
        if not prix_cible or prix_cible <= 0 or p.get("dressing") not in ("Dressing 1", "Dressing 2"):
            continue
        prix_fixe = supabase_svc.calculer_prix_fixe_liquidation(prix_cible)
        groupes_bruts.setdefault((p["dressing"], prix_fixe), []).append(p)

    # groupes / groupes_reportes : (dressing, prix_fixe) -> items
    groupes = {}
    groupes_reportes = {}
    for cle, items in groupes_bruts.items():
        if forcer_seuil or len(items) >= MIN_ARTICLES_PAR_GROUPE_LIQUIDATION:
            groupes[cle] = items
        else:
            groupes_reportes[cle] = items

    if not groupes:
        nb_en_attente = sum(len(items) for items in groupes_reportes.values())
        raise HTTPException(
            status_code=400,
            detail=f"Aucun groupe n'atteint le seuil de {MIN_ARTICLES_PAR_GROUPE_LIQUIDATION} articles d'un même dressing pour l'instant "
                   f"({nb_en_attente} article(s) en attente, répartis sur {len(groupes_reportes)} groupe(s) trop petits)."
        )

    background_tasks.add_task(_run_liquidation_task, groupes)

    def _stats_dressing(dressing):
        return {
            "nb_articles": sum(len(items) for (d, _), items in groupes.items() if d == dressing),
            "nb_groupes": sum(1 for (d, _) in groupes if d == dressing),
        }

    return {
        "status": "started",
        "nb_articles": sum(len(items) for items in groupes.values()),
        "nb_groupes": len(groupes),
        "nb_articles_reportes": sum(len(items) for items in groupes_reportes.values()),
        "nb_groupes_reportes": len(groupes_reportes),
        "par_dressing": {d: _stats_dressing(d) for d in ("Dressing 1", "Dressing 2")},
    }


async def _run_liquidation_task(groupes):
    """groupes : (dressing, prix_fixe) -> items -- chaque groupe est mono-dressing

    Séparation stricte Dressing 1 / Dressing 2 (règle anti-détection : jamais
    les deux dressings dans le même appel ClemzAutomation, même en déclenchement
    manuel) -- même schéma que _traiter_lots_dressing() dans
    automation_service.run_baisse_prix_auto(). La liquidation reste volontairement
    hors quota risk_guard (pas de get_allowed_quantity/log_action) et hors cron :
    toujours déclenchée manuellement depuis le dashboard.
    """
    import asyncio
    import random
    from services.automation_scheduler import start_task_run, update_task_result, finish_task_run

    tous_les_items = [p for items in groupes.values() for p in items]
    task_id = start_task_run("liquidation", tous_les_items)

    async def _traiter_dressing(dressing):
        groupes_dressing = sorted(
            ((prix_fixe, items) for (d, prix_fixe), items in groupes.items() if d == dressing),
            key=lambda kv: kv[0],
        )
        for index, (prix_fixe, items_dressing) in enumerate(groupes_dressing):
            # Pause aléatoire 2-5 min entre deux groupes d'un même dressing
            # (08/10/2026) -- même fourchette que entre deux dressings dans
            # clemz_automation.py ; évite d'enchaîner les baisses de prix Clemz
            # d'affilée (séries associées aux captchas observés).
            if index > 0:
                pause_secondes = random.uniform(120, 300)
                logger.info(f"⏸️ Liquidation {dressing} : pause de {pause_secondes / 60:.1f} min avant le groupe {prix_fixe}€")
                await asyncio.sleep(pause_secondes)
            noms = [i["nom"] for i in items_dressing]
            produits_d1 = noms if dressing == "Dressing 1" else []
            produits_d2 = noms if dressing == "Dressing 2" else []

            bot = ClemzAutomation(produits_d1, produits_d2, task_type="baisse_prix", prix_fixe=prix_fixe)
            results = await bot.run()

            for account_result in results:
                for item_result in account_result.get("selection_results", []):
                    matched = next((i for i in items_dressing if i["nom"] == item_result["nom"]), None)
                    item_id = matched["id"] if matched else item_result["nom"]
                    update_task_result(task_id, item_id, status=item_result["status"], reason=item_result.get("reason"))
                    if item_result["status"] == "success" and matched:
                        supabase_svc.update_article(matched["id"], {"prix_vente": prix_fixe})

    try:
        d1_a_des_items = any(d == "Dressing 1" for (d, _) in groupes)
        d2_a_des_items = any(d == "Dressing 2" for (d, _) in groupes)

        await _traiter_dressing("Dressing 1")
        if d1_a_des_items and d2_a_des_items:
            pause_secondes = random.uniform(300, 600)
            logger.info(f"⏸️ Liquidation : pause de {pause_secondes / 60:.1f} min avant Dressing 2")
            await asyncio.sleep(pause_secondes)
        await _traiter_dressing("Dressing 2")
    except Exception as e:
        logger.error(f"❌ Erreur liquidation : {e}")
        for item in tous_les_items:
            update_task_result(task_id, item["id"], status="failed", reason=str(e)[:120])
    finally:
        finish_task_run(task_id)


@router.post("/generate-description-batch")

async def generate_description_batch(request: Request):
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        # Température basse pour rester factuel
        generation_config = genai.types.GenerationConfig(temperature=0.1)
        
        # On récupère le formulaire envoyé par React
        form_data = await request.form()
        
        # On prépare le contenu pour Gemini
        content = []
        
        prompt_instructions = """
        Tu es un expert en rachat-revente sur Vinted. Tu vas recevoir les photos de plusieurs articles, classés par numéro.
        Pour CHAQUE article, génère une annonce complète. Sépare chaque article par EXACTEMENT "---".

        ═══════════════════════════════════════
        RÈGLES SEO — TITRE (PRIORITÉ ABSOLUE)
        ═══════════════════════════════════════
        Le titre est le facteur SEO n°1 sur Vinted. Vinted utilise un moteur de recherche sémantique
        qui indexe chaque mot du titre. Optimise-le comme une requête de recherche, pas comme un slogan.

        - Format STRICT : [Type vêtement] [Marque] [Couleur/Matière] [Détail distinctif] [Taille]
        - Maximum 100 caractères, AUCUNE virgule, AUCUN emoji dans le titre
        - Marque : première lettre majuscule uniquement (Nike, Massimo Dutti — jamais NIKE)
        - Inclure le maximum de mots-clés naturels que les acheteurs tapent réellement
        - Exemples de bons mots-clés : coupe, matière, style (slim, oversize, laine, velours, vintage, workwear...)
        - Si une info est absente des photos, indique "Non précisé" — N'INVENTE RIEN

        ═══════════════════════════════════════
        RÈGLES SEO — DESCRIPTION
        ═══════════════════════════════════════
        La description est indexée mot par mot par Vinted. Elle doit être riche en synonymes et
        en termes de recherche naturels, tout en restant lisible et vendeuse.

        - 3 à 5 lignes maximum, ton direct et efficace, sans superlatifs creux ("magnifique", "superbe")
        - Inclure des synonymes du type de vêtement (ex: pour un manteau : "veste longue", "pardessus", "overcoat")
        - Mentionner la matière, la coupe, et l'occasion de port si déductibles des photos
        - Laisser UNE ligne vide entre le titre et la description

        ═══════════════════════════════════════
        HASHTAGS (RÔLE SECONDAIRE)
        ═══════════════════════════════════════
        Les hashtags ont un poids limité sur Vinted en 2026. Génère uniquement les langues
        de tes vrais marchés, proportionnellement au volume de ventes :

        - 5 hashtags FR  (marché principal, 64% des ventes)
        - 3 hashtags EN  (Irlande + Pays-Bas cherchent souvent en anglais)
        - 3 hashtags NL  (Pays-Bas, 2ème marché réel à 11%)
        - 2 hashtags IT  (Italie, 10% des ventes)
        - 2 hashtags DE  (Allemagne, panier moyen élevé)

        Total : 15 hashtags maximum, 0 doublon entre les langues.
        Saute une ligne entre chaque groupe de langue.
        NE mets PAS les initiales des langues devant les groupes.

        ═══════════════════════════════════════
        STRUCTURE EXACTE À RESPECTER
        ═══════════════════════════════════════
        [Titre SEO — max 100 caractères, sans virgule]

        [Description vendeuse 3-5 lignes, riche en synonymes et mots-clés naturels]

        - [Type de vêtement et Marque]
        - Taille : [Déduite de l'étiquette]
        - Couleur : [Déduite]
        - Matières : [Composition exacte si visible]
        - Etat : [Déduit visuellement]
        - Mesures en photos
        - Envoi rapide et soigné sous 24/48h
        - Remarques : [UNIQUEMENT si défaut visible ou coupe particulière]

        #tag1FR #tag2FR #tag3FR #tag4FR #tag5FR

        #tag1EN #tag2EN #tag3EN

        #tag1NL #tag2NL #tag3NL

        #tag1IT #tag2IT

        #tag1DE #tag2DE

        ⚠️ POINTS DE VIGILANCE (UNIQUEMENT SI NÉCESSAIRE) :
        - Taille : [Si coupe enfant/ado trompeuse]
        - Authenticité : [Si anomalie logo, couture suspecte, finitions pauvres]

        [PRICING]
        Prix de revente potentiel : [Prix] €
        Prix de mise en ligne conseillé : [Prix psychologique] €
        💡 JUSTIFICATION : [Une phrase — critères déterminants : état, rareté, marché actuel]
        """
        content.append(prompt_instructions)
        
        # On boucle sur les 5 zones de dépôt possibles
        articles_count = 0
        for i in range(5):
            files = form_data.getlist(f'product_{i}')
            if files:
                articles_count += 1
                content.append(f"\nPHOTOS DE L'ARTICLE {articles_count} :")
                for file in files:
                    bytes_data = await file.read()
                    content.append({"mime_type": file.content_type, "data": bytes_data})

        if articles_count == 0:
            raise HTTPException(status_code=400, detail="Aucune image n'a été reçue.")

        # Appel à Gemini
        _wait_for_gemini_rate_limit()
        response = model.generate_content(content, generation_config=generation_config)
        
        return {"descriptions": response.text}

    except Exception as e:
        print(f"Erreur Gemini: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/automation/scheduled-both")
async def get_both_scheduled():
    from services.automation_scheduler import get_both_scheduled
    return get_both_scheduled()

@router.post("/test-republish")
async def test_republish(background_tasks: BackgroundTasks):
    from services.automation_service import _execute_republish
    # "midi" porte désormais tous les articles de la fenêtre unique 14h-19h
    # (cf. schedule_republish) -- "soir" reste toujours vide depuis ce changement.
    background_tasks.add_task(_execute_republish, "midi")
    return {"status": "launched"}

@router.post("/test-partage-vues-favoris")
async def test_partage_vues_favoris(background_tasks: BackgroundTasks, payload: dict = Body(default={})):
    """
    Route de test manuel : déclenche l'automatisation Clemz de partage des vues
    et des favoris, indépendamment du repost soir. payload optionnel :
    {"dressing": "Dressing 1"} ou {"dressing": "Dressing 2"} pour ne traiter
    qu'un seul compte -- omis ou vide, traite les deux (comportement historique).
    """
    from services.automation_service import run_partage_vues_favoris_task
    dressing = payload.get("dressing")
    background_tasks.add_task(run_partage_vues_favoris_task, dressing)
    return {"status": "launched", "dressing": dressing or "les deux"}

@router.get("/score-trends")
async def get_score_trends_route(days: int = 14):
    """
    Historique récent de score par article, groupé -- alimente l'indicateur de
    tendance (flèche + mini-graphique) sur chaque carte produit du dashboard.
    """
    return supabase_svc.get_score_trends(days)