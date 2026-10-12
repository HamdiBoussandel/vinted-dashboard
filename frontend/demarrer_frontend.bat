@echo off
REM demarrer_frontend.bat -- lance le dashboard au demarrage de la VM et
REM l'ouvre dans Chrome (cf. echange du 12/10/2026). A declencher a
REM l'ouverture de session (raccourci dans shell:startup), comme le backend.
REM
REM Version de PRODUCTION (vite build + vite preview) plutot que "npm run dev" :
REM plus legere pour une machine qui tourne en continu, sans rechargement a
REM chaud inutile. Le build est refait a chaque demarrage pour inclure le
REM dernier "git pull" ; s'il echoue, l'ancien dist\ est servi tel quel.
REM
REM Chrome n'est ouvert qu'une fois le frontend ET le backend joignables (le
REM backend peut mettre un moment a demarrer apres l'ouverture de session) --
REM sinon les pages chargeraient en erreur. Au-dela de 3 min sans backend,
REM Chrome est ouvert quand meme. Fermer cette fenetre arrete le dashboard,
REM pas l'automatisation (qui ne depend que du backend).

cd /d "%~dp0"
title VintedPro Frontend - build...

call npm run build
if errorlevel 1 (
    echo.
    echo [FRONTEND] Echec du build -- utilisation du dernier dist\ disponible.
    if not exist "dist\index.html" (
        echo [FRONTEND] Aucun dist\ disponible : impossible de servir le dashboard.
        pause
        exit /b 1
    )
)

title VintedPro Frontend - http://localhost:4173

start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url='http://localhost:4173'; $fin=(Get-Date).AddMinutes(3);" ^
  "function Ok($u){ try { Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 3 | Out-Null; $true } catch { $false } };" ^
  "while(-not (Ok $url) -and (Get-Date) -lt $fin){ Start-Sleep 2 };" ^
  "while(-not (Ok 'http://127.0.0.1:8000/') -and (Get-Date) -lt $fin){ Start-Sleep 5 };" ^
  "try { Start-Process chrome $url -ErrorAction Stop } catch { Start-Process $url }"

call npm run preview -- --port 4173 --strictPort
