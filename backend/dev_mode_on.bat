@echo off
echo Passage en mode developpement...
schtasks /end /tn "VintedPro Backend" >nul 2>&1
schtasks /change /tn "VintedPro Backend" /disable
echo Tache planifiee arretee et desactivee.
echo Tu peux maintenant lancer "python main.py" depuis le terminal VS Code sans conflit.
pause