@echo off
REM arret_nocturne.bat -- a planifier dans le Planificateur de taches Windows
REM pour s'executer chaque jour a 23h00 sur le VM dedie (cf. echange du
REM 04/10/2026 : PC eteint entre 23h et 10h, pas une simple mise en veille).
REM
REM Attend (via l'API backend, /api/maintenance/preparer-arret) qu'aucune
REM automatisation Clemz ne soit en cours (republication, baisse de prix,
REM liquidation) avant de couper l'alimentation -- un arret brutal en pleine
REM ecriture d'un profil Chrome (IndexedDB/leveldb) peut le corrompre.

echo [%date% %time%] Demande d'extinction nocturne...

REM --max-time doit rester AU-DESSUS du timeout_secondes envoye ci-dessous
REM (2700s = 45 min), sans quoi curl coupe la connexion avant que le serveur
REM ait fini d'attendre et ne reponde.
curl -s -X POST http://127.0.0.1:8000/api/maintenance/preparer-arret ^
     -H "Content-Type: application/json" ^
     -d "{\"timeout_secondes\": 2700}" ^
     --max-time 2820

echo [%date% %time%] Arret de la tache planifiee "VintedPro Backend"...
schtasks /end /tn "VintedPro Backend" >nul 2>&1

timeout /t 5 /nobreak >nul

echo [%date% %time%] Extinction du PC (annulable avec "shutdown /a" dans les 10s)...
shutdown /s /t 10 /c "VintedPro : extinction nocturne planifiee (23h-10h)."
