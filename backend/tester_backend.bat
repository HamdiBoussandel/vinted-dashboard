@echo off
REM tester_backend.bat -- lancement MANUEL pour tester en direct (cf. echange
REM du 07/10/2026). Fenetre VISIBLE et laissee OUVERTE, contrairement a
REM demarrer_backend.bat qui redirige tout vers logs\demarrage.log -- celui-la
REM reste volontairement silencieux car la tache planifiee "onlogon" qui
REM l'utilise n'a personne devant l'ecran, une pause y resterait bloquee pour
REM toujours. Ce script-ci est a double-cliquer soi-meme pour observer le
REM demarrage en direct (succes ou erreur), jamais a mettre dans une tache
REM planifiee.

cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" "%~dp0run.py"

echo.
echo ============================================================
echo Le serveur s'est arrete (Ctrl+C, ou erreur affichee ci-dessus).
echo ============================================================
pause
