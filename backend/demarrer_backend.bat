@echo off
REM demarrer_backend.bat -- a utiliser comme commande de la tache planifiee
REM "VintedPro Backend" (declenchee a la connexion, cf. echange du 04/10/2026).
REM
REM Fixe explicitement le dossier de travail sur backend/ avant de lancer
REM Python -- load_dotenv() (database.py) cherche .env depuis le repertoire
REM courant, pas depuis l'emplacement du script : sans ce "cd", lance depuis
REM n'importe quel autre dossier par le Planificateur de taches, le backend
REM demarrerait sans configuration (Supabase, Gemini, Gmail...).

cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0run.py"
