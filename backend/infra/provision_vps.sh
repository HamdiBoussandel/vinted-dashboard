#!/bin/bash
# Provisioning initial du VPS Oracle Cloud pour le scraping VintedPro.
# À exécuter une seule fois, après la toute première connexion SSH.
set -e

echo "=== Mise à jour du système ==="
sudo apt update && sudo apt upgrade -y

echo "=== Installation Python + outils de base ==="
sudo apt install -y python3 python3-venv python3-pip git unzip curl

echo "=== Installation Xvfb + VNC (nécessaire UNE FOIS pour la connexion Vinted initiale) ==="
sudo apt install -y xvfb x11vnc fluxbox

echo "=== Création du dossier projet ==="
mkdir -p ~/vintedpro-scraper
cd ~/vintedpro-scraper

echo "=== Création de l'environnement virtuel Python ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Installation des dépendances Python ==="
# Transfère d'abord requirements.txt sur le VPS (scp) avant de lancer ce script,
# ou colle ici les paquets minimaux nécessaires au scraping seul :
pip install --upgrade pip
pip install playwright supabase python-dotenv google-generativeai requests

echo "=== Installation de Chromium + dépendances système Playwright ==="
playwright install --with-deps chromium

echo "=== Ouverture du port SSH uniquement (firewall local Ubuntu) ==="
sudo ufw allow 22/tcp
sudo ufw --force enable

echo ""
echo "=== Provisioning terminé ==="
echo "Prochaine étape : transférer le code (session_manager.py, vinted_scraper.py,"
echo "database.py, services/, .env) vers ~/vintedpro-scraper/, puis lancer"
echo "create_cloud_session.py pour la connexion Vinted initiale (voir instructions)."