from fastapi import APIRouter, HTTPException, Body,BackgroundTasks, Request,UploadFile, File, Form # Ajout BackgroundTasks
import google.generativeai as genai
from services.notion_service import NotionService

from services.automation_scheduler import (
    schedule_republish,
    get_scheduled_republish,
    get_task_history,
)

#from database import GOALS_DB_ID, DATABASE_ID, database.app_state # On utilise l'état partagé
import database
import logging
from services.clemz_automation import ClemzAutomation

from datetime import datetime
# Importez le nouveau service
from services.vinted_scraper import VintedScraper
from database import GEMINI_API_KEY
import traceback



genai.configure(api_key=GEMINI_API_KEY)

import json
import os

router = APIRouter(prefix="/api", tags=["Inventory"])
notion = NotionService()
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
        "status_TEST": database.app_state.get("status_TEST", "En attente")
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

@router.post("/automation/schedule-republish")
async def schedule_republish_route(payload: dict = Body(...)):
    """
    Enregistre la liste d'articles à republier ce soir dans scheduled_republish.json.
    Appelée depuis le dashboard quand l'utilisateur clique sur "Programmer".

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
        "message": f"{len(items)} article(s) programmé(s) pour ce soir (18h-19h).",
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

@router.get("/automation/history")
async def get_task_history_route(limit: int = 50, task_type: str = None):
    """
    Retourne l'historique des tâches d'automatisation (republication + baisse de prix).
    Utilisée par la future section de suivi dans le dashboard React.

    Paramètres optionnels :
    - limit : nombre maximum d'entrées retournées (défaut 50)
    - task_type : filtre par type ("republication" ou "baisse_prix")
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
            
            logger.info("🧪 Lancement du test manuel (Scrap + Notion)...")
            
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
    return await notion.get_processed_inventory()

@router.get("/goals")
async def get_sales_goals():
    """Récupère la progression des objectifs mensuels."""
    return await notion.get_sales_goals(database.GOALS_DB_ID)

@router.get("/stats")
async def get_stats_comptables():
    return await notion.get_stats_comptables(database.GOALS_DB_ID)

@router.patch("/treat/{article_id}")
async def treat_article(article_id: str):
    """Marque un article comme traité dans Notion."""
    return await notion.update_page(article_id, {"Traité": {"checkbox": True}})

@router.patch("/treat-multiple")
async def treat_multiple_articles(payload: dict = Body(...)):
    """Marque plusieurs articles comme traités dans Notion."""
    article_ids = payload.get("ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="Aucun ID fourni.")
    
    success_count = 0
    for article_id in article_ids:
        try:
            # 🚨 CORRECTION : Ajout de l'enveloppe "properties" ici aussi
            await notion.update_page(article_id, {"Traité": {"checkbox": True}})
            success_count += 1
        except Exception as e:
            logger.error(f"Erreur lors du traitement groupé de {article_id} : {e}")
            
    return {"status": "success", "message": f"{success_count} articles traités."}

@router.patch("/republish-multiple")
async def republish_multiple_articles(payload: dict = Body(...)):
    """Met à jour la Date de republication à aujourd'hui pour plusieurs articles."""
    article_ids = payload.get("ids", [])
    if not article_ids:
        raise HTTPException(status_code=400, detail="Aucun ID fourni.")
    
    # On récupère la date du jour au format attendu par Notion (YYYY-MM-DD)
    today = datetime.now().strftime("%Y-%m-%d")
    success_count = 0
    
    for article_id in article_ids:
        try:
            # On met à jour la propriété de type "Date" dans Notion
            await notion.update_page(article_id, {
                "Date de republication": {"date": {"start": today}}
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
    notion = NotionService()
    # On appelle TA fonction existante que l'on vient de corriger
    success = await notion.republish_by_name_logic(item_name)

    if success:
        return {"status": "success", "message": f"{item_name} mis à jour"}
    else:
        raise HTTPException(status_code=404, detail="Article non trouvé ou erreur Notion")

@router.delete("/clear-database")
async def clear_inventory_database():
    """Route pour vider toutes les données de la base Notion du scraper."""
    try:
        result = await notion.clear_scraper_database(database.DATABASE_ID)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/automation/baisse-prix")
async def start_baisse_prix_vinted(background_tasks: BackgroundTasks, payload: dict = Body(...)):
    """
    Déclenche l'automatisation Clemz pour une liste de baisses de prix.
    Exécution immédiate (pas de créneau différé).
    """
    articles_bruts = payload.get("produits", [])

    if not articles_bruts:
        raise HTTPException(status_code=400, detail="La liste de produits est vide.")

    produits_d1 = [a["nom"] for a in articles_bruts if isinstance(a, dict) and a.get("dressing") == "Dressing 1"]
    produits_d2 = [a["nom"] for a in articles_bruts if isinstance(a, dict) and a.get("dressing") == "Dressing 2"]

    async def run_baisse_prix_task(list1, list2, items):
        from services.automation_scheduler import start_task_run, update_task_result, finish_task_run
        task_id = start_task_run("baisse_prix", items)
        try:
            bot = ClemzAutomation(list1, list2)
            results = await bot.run()

            for account_result in results:
                for item_result in account_result.get("selection_results", []):
                    matched = next((i for i in items if i["nom"] == item_result["nom"]), None)
                    item_id = matched["id"] if matched else item_result["nom"]
                    update_task_result(
                        task_id,
                        item_id,
                        status=item_result["status"],
                        reason=item_result.get("reason"),
                    )
        except Exception as e:
            logger.error(f"❌ Erreur baisse de prix : {e}")
            for item in items:
                update_task_result(task_id, item.get("id", item["nom"]), status="failed", reason=str(e)[:120])
        finally:
            finish_task_run(task_id)

    background_tasks.add_task(run_baisse_prix_task, produits_d1, produits_d2, articles_bruts)
    return {
        "status": "started",
        "counts": {"d1": len(produits_d1), "d2": len(produits_d2)},
    }

def format_brand_name(text):
    # Transforme "NIKE AIR" en "Nike Air"
    return " ".join(word.capitalize() for word in text.split())

@router.post("/generate-description-batch")
async def generate_description_batch(request: Request):
    try:
        model = genai.GenerativeModel('gemini-2.5-pro')
        
        # Température basse pour rester factuel
        generation_config = genai.types.GenerationConfig(temperature=0.1)
        
        # On récupère le formulaire envoyé par React
        form_data = await request.form()
        
        # On prépare le contenu pour Gemini
        content = []
        
        prompt_instructions = """
        Tu es un expert Vinted. Tu vas recevoir les photos de plusieurs articles, classés par numéro.
        Pour CHAQUE article, génère une annonce complète ultra-vendeuse.

        RÈGLES STRICTES :
        1. SÉPARATEUR : Sépare chaque article par EXACTEMENT "---".
        2. MARQUE : Uniquement la première lettre en majuscule (Ex: 'Nike', 'Massimo Dutti'. Jamais 'NIKE').
        3. TITRE : Le titre doit faire MAXIMUM 100 caractères au total. Il ne doit contenir AUCUNE VIRGULE. Tu dois respecter scrupuleusement ce format : [Type de vêtement] [Marque] [Couleur] [Mots-clés accrocheurs] Taille [Taille déduite]. 
           -> IMPORTANT : Pour la partie [Mots-clés accrocheurs], déduis de ton analyse visuelle les détails pertinents qui font vendre (ex: logo brodé, modèle iconique, collection particulière, vintage).
        4. DESCRIPTION ACCROCHEUSE : Rédige une petite description vendeuse et enthousiaste de 3 lignes maximum, à placer juste en dessous du titre. Laisse IMPÉRATIVEMENT une ligne vide (un saut de ligne) entre le Titre et cette description.
        5. HASHTAGS : Tu dois générer exactement 10 hashtags par langue (FR, EN, ES, IT, NL). Ne mets AUCUN crochet autour des hashtags. Tu dois OBLIGATOIREMENT sauter une ligne vide entre chaque langue. NE METS PAS les initiales des langues devant les groupes. NE METS SURTOUT PAS le mot "Hashtags :" avant les listes.Place-les impérativement JUSTE APRÈS la liste des caractéristiques techniques. Saute une ligne vide entre chaque groupe de langue..
        6. INFORMATIONS MANQUANTES (CRUCIAL) : Si tu ne trouves pas une information sur les photos (comme la Taille, la Couleur, la Matière ou la Marque), N'INVENTE RIEN. Indique explicitement "Non précisé" ou "Non visible" à la place.
        7. AUCUN DOUBLON DE HASHTAGS (CRUCIAL) : Les 50 hashtags générés doivent être STRICTEMENT UNIQUES. Ne répète jamais le même mot. Les noms de marque (ex: #uniqlo) ou les termes universels (ex: #vintage, #y2k) ne doivent apparaître qu'UNE SEULE FOIS (place-les dans le premier groupe). Pour les autres langues, utilise des termes différents, des détails du vêtement ou des synonymes locaux.
        8. ESTIMATION PRIX (NOUVEAU) : Basée sur ton analyse (état, rareté, matières, coupe, marque), Compare avec les prix de vente RÉELS constatés sur Vinted pour des articles similaires dans le même état. Ta fourchette de prix doit être cohérente et stable, basée sur des données de marché factuelles et propose :
           - Prix de revente estimé : Le prix "juste" du marché.
           - Prix psychologique conseillé : Un prix légèrement plus élevé (marge de négociation de 15-20%) pour finir sur un chiffre psychologique (ex: 29€ au lieu de 25€).
        8. VIGILANCE TAILLE (PIÈGE) : Analyse si la taille indiquée (ex: M) correspond à un vêtement ENFANT/ADOLESCENT (ex: Ralph Lauren Kids, Lacoste Kids). Si c'est le cas, mentionne-le TRÈS CLAIREMENT dans la zone de vigilance.
        9. VIGILANCE AUTHENTICITÉ : Analyse les logos, coutures et étiquettes. Si tu repères une anomalie (logo mal brodé, police d'écriture suspecte, finitions pauvres), indique tes doutes.
        10. JUSTIFICATION DU PRIX : Sous chaque estimation de prix, explique en une phrase courte quels critères ont été déterminés (ex: rareté du modèle, état impeccable, forte demande pour cette marque, comparaison avec les prix du marché actuel).
        
        STRUCTURE EXACTE À RESPECTER :
        [Ton titre respectant le format exact, sans virgule, max 100 caractères]

        [Description accrocheuse de 1 à 3 lignes maximum, donnant envie d'acheter]

        - [Type de vêtement et Marque]
        - Taille : [Déduite de l'étiquette]
        - Couleur : [Déduite]
        - Matières : [Composition exacte]
        - Etat : [Déduit visuellement]
        - Mesures en photos
        - Envoi rapide et soigné sous 24/48h
        - Remarques : [À AJOUTER UNIQUEMENT SI DÉFAUT VISIBLE OU COUPE PARTICULIÈRE]

        (Saute une ligne ici)
        #tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10 (Groupe FR)

        #tag11 #tag12 #tag13 #tag14 #tag15 #tag16 #tag17 #tag18 #tag19 #tag20 (Groupe EN)

        #tag21 #tag22 #tag23 #tag24 #tag25 #tag26 #tag27 #tag28 #tag29 #tag30 (Groupe ES)

        #tag31 #tag32 #tag33 #tag34 #tag35 #tag36 #tag37 #tag38 #tag39 #tag40 (Groupe IT)

        #tag41 #tag42 #tag43 #tag44 #tag45 #tag46 #tag47 #tag48 #tag49 #tag50 (Groupe NL)

        ⚠️ POINTS DE VIGILANCE (À AJOUTER UNIQUEMENT SI BESOIN) :
        - Taille : [Mentionne si c'est une coupe enfant/ado trompeuse]
        - Authenticité : [Mentionne tes doutes sur le logo ou l'étiquette]

        [PRICING]
        Prix de revente potentiel : [Prix] €
        Prix de mise en ligne conseillé : [Prix psychologique] €
        💡 JUSTIFICATION : 
        [Ton explication ici]

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
        response = model.generate_content(content, generation_config=generation_config)
        
        return {"descriptions": response.text}

    except Exception as e:
        print(f"Erreur Gemini: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))