@echo off
echo Retour en mode production (tache de fond)...
echo Ferme d'abord le terminal VS Code qui fait tourner main.py, si ce n'est pas deja fait.
pause
schtasks /change /tn "VintedPro Backend" /enable
schtasks /run /tn "VintedPro Backend"
echo Tache planifiee reactivee et relancee immediatement (pas besoin d'attendre une reconnexion).
pause