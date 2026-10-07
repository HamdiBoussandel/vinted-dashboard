@echo off
REM demarrer_backend.bat -- a utiliser comme commande de la tache planifiee
REM "VintedPro Backend" (declenchee a la connexion, cf. echange du 04/10/2026).
REM
REM Fixe explicitement le dossier de travail sur backend/ avant de lancer
REM Python -- load_dotenv() (database.py) cherche .env depuis le repertoire
REM courant, pas depuis l'emplacement du script : sans ce "cd", lance depuis
REM n'importe quel autre dossier par le Planificateur de taches, le backend
REM demarrerait sans configuration (Supabase, Gemini, Gmail...).
REM
REM Sortie redirigee vers logs\demarrage.log (cf. echange du 07/10/2026) --
REM ni un double-clic (fenetre fermee instantanement en cas d'erreur) ni la
REM tache planifiee (aucune fenetre visible du tout, declenchee "onlogon"
REM sans personne devant l'ecran) ne permettent de lire une erreur de
REM demarrage autrement. Fichier ECRASE a chaque lancement (>, pas >>) --
REM c'est le journal du demarrage le plus recent, pas un historique cumule.

cd /d "%~dp0"
if not exist "logs" mkdir "logs"
"%~dp0venv\Scripts\python.exe" "%~dp0run.py" > "%~dp0logs\demarrage.log" 2>&1
