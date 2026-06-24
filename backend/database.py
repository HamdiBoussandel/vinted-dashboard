import os
import json
from dotenv import load_dotenv

load_dotenv()

# 2. Récupération des secrets Notion
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")
PURCHASE_DB_ID = os.getenv("PURCHASE_ID")
GOALS_DB_ID = os.getenv("GOALS_DB_ID")
FILTERS_DB_ID = os.getenv("FILTERS_DB_ID")
SALES_DB_ID = os.getenv("SALES_DB_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# État global minimaliste
app_state = {
    "status_14h": "En attente",    # Alignement du nom avec Dashboard.jsx
    "status_22h": "En attente",
    "is_clemz_running": False,
    "status_TEST": "En attente",
    "last_run_slot": None,
    "has_error": False,

    # --- PARTIE SOURCING (SNIPER) ---
    "sniper_active": False,        # Pilotage via Sourcing.jsx
    "current_feed": [],           # Flux de produits triés
    "active_filters": [],         # Cache des filtres Notion
    "last_scan_time": None         # Timestamp pour le Radar
}

class SystemConfig:
    def __init__(self):
        self.brands_db = self.load_brands()
    
        self.auth_data = {
                "token": os.getenv("VINTED_TOKEN"),
                "session": os.getenv("VINTED_SESSION"),
                "ua": os.getenv("VINTED_UA")
            }

    def load_brands(self):
        try:
            with open("brand.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"🚨 Erreur chargement brand.json : {e}")
            return {}

config = SystemConfig()