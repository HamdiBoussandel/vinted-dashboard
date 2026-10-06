import os
import json
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# --- SUPABASE (nouvelle base de données) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Notion — conservé pour clemz_scraper.py et vinted_scraper.py
NOTION_TOKEN = os.getenv("NOTION_TOKEN")

# Rotation de clés Gemini -- GEMINI_API_KEYS (séparées par virgules) prend le
# dessus si renseignée ; sinon repli sur l'unique GEMINI_API_KEY historique,
# pour rester fonctionnel tant qu'une seule clé est configurée.
_gemini_keys_raw = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_raw.split(",") if k.strip()]
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else None

# État global minimaliste
app_state = {
    "status_14h": "En attente",    # Alignement du nom avec Dashboard.jsx
    "status_22h": "En attente",
    "is_clemz_running": False,
    "status_TEST": "En attente",
    "last_run_slot": None,
    "has_error": False,

    # --- NOUVEAU : état de connexion par dressing ---
    # None = pas encore vérifié, True = session OK, False = session expirée
    "session_status": {
        "dressing1": None,
        "dressing2": None,
    },

    # --- NOUVEAU : reconnexion manuelle en cours (déclenchée par le bouton) ---
    # None = rien en cours, "en_cours" = navigateur ouvert / attente reco,
    # "succes" / "echec" = résultat de la dernière tentative
    "reconnect_status": {
        "dressing1": None,
        "dressing2": None,
    },

    # --- PARTIE SOURCING (SNIPER) ---
    "sniper_active": False,        # Pilotage via Sourcing.jsx
    "current_feed": [],           # Flux de produits triés
    "active_filters": [],         # Cache des filtres Supabase
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
            brand_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand.json")
            with open(brand_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"🚨 Erreur chargement brand.json : {e}")
            return {}

config = SystemConfig()