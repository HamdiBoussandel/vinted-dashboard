"""
Notification par email (Gmail SMTP) en cas d'échec d'une tâche automatisée
sur le VPS cloud -- remplace les notifications Windows (plyer/PowerShell)
utilisées en local, qui n'existent pas sous Linux.
"""
import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
NOTIFY_TO = os.getenv("NOTIFY_EMAIL_TO", GMAIL_ADDRESS)  # par défaut, s'envoie à soi-même


def envoyer_notification_echec(sujet, corps):
    """
    Envoie un email de notification. Ne lève jamais d'exception vers l'appelant
    (une notification qui échoue ne doit jamais faire planter le script qui
    l'appelle) -- log l'erreur localement à la place.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        logger.error("⚠️ GMAIL_ADDRESS/GMAIL_APP_PASSWORD non configurés — notification email impossible.")
        return False

    msg = MIMEText(corps, "plain", "utf-8")
    msg["Subject"] = f"[VintedPro Cloud] {sujet}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [NOTIFY_TO], msg.as_string())
        logger.info(f"📧 Notification envoyée : {sujet}")
        return True
    except Exception as e:
        logger.error(f"❌ Échec envoi notification email : {e}")
        return False