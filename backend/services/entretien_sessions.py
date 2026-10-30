"""
Entretien des sessions Vinted des profils automatisés (26/10/2026).

Constat (cookies des 4 profils, 26/10/2026) : la connexion Vinted repose sur
le cookie refresh_token_web, valable 7 JOURS et réémis à chaque visite de
vinted.fr. Un profil utilisé au moins une fois par semaine reste connecté
indéfiniment ; un profil inutilisé plus de 7 jours est déconnecté. Avec les
tâches automatiques coupées (routine en pause), plus rien n'ouvrait les
profils depuis le 22-23/10 : expiration prévue les 29-30/10.

Principe : une fois par jour, lecture de la date d'expiration de
refresh_token_web dans le fichier de cookies de chaque profil (sans ouvrir
le navigateur). Seuls les profils qui expirent dans moins de
SEUIL_RENOUVELLEMENT_JOURS (= inutilisés depuis plus de ~2 jours) sont
ouverts, via ensure_session() -- simple visite de vinted.fr qui renouvelle
les jetons, et notification + fenêtre de reconnexion si la session est déjà
perdue. Aucune action sur les annonces.

Volontairement HORS de la pause de routine : son but est justement de garder
les sessions vivantes pendant que les automatisations sont arrêtées.
Jamais pendant une autre tâche : profil verrouillé = ignoré cette fois-ci.
"""
import asyncio
import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta

from services.session_manager import ACCOUNTS, ensure_session, _send_notification

logger = logging.getLogger(__name__)

ENTRETIEN_SESSIONS_ACTIF = True
# Renouvelle un profil dont le refresh_token expire dans moins de 5 jours
# (dernière visite il y a plus de ~2 jours) -- marge large sur les 7 jours.
SEUIL_RENOUVELLEMENT_JOURS = 5
# Profils Clemz : le watchdog "messages automatiques" peut les tenir ouverts
_PROFILS_CLEMZ = ("chrome_clemz", "edge_clemz")

_EPOQUE_CHROME = datetime(1601, 1, 1)


def expiration_session(account_key):
    """Date d'expiration (heure UTC, naïve) du cookie refresh_token_web du
    profil, lue dans une COPIE de son fichier de cookies (Chrome le garde
    ouvert). None si absent ou illisible (profil jamais connecté...)."""
    dossier = ACCOUNTS[account_key]["session"]
    for relatif in (os.path.join("Default", "Network", "Cookies"), os.path.join("Default", "Cookies")):
        source = os.path.join(dossier, relatif)
        if not os.path.exists(source):
            continue
        copie = os.path.join(tempfile.gettempdir(), f"vintedpro_cookies_{account_key}.db")
        try:
            shutil.copyfile(source, copie)
            with sqlite3.connect(copie) as db:
                ligne = db.execute(
                    "SELECT MAX(expires_utc) FROM cookies WHERE host_key LIKE '%vinted.fr' AND name = 'refresh_token_web'"
                ).fetchone()
            if ligne and ligne[0]:
                return _EPOQUE_CHROME + timedelta(microseconds=ligne[0])
        except Exception as e:
            logger.warning(f"⚠️ [ENTRETIEN SESSIONS] Cookies de {account_key} illisibles ({e}).")
        finally:
            try:
                os.remove(copie)
            except OSError:
                pass
        return None
    return None


def etat_sessions():
    """{account_key: {nom, expire_le, jours_restants}} -- pour la route de statut."""
    maintenant = datetime.utcnow()
    etat = {}
    for cle, compte in ACCOUNTS.items():
        expire = expiration_session(cle)
        etat[cle] = {
            "nom": compte["name"],
            "expire_le": expire.isoformat(timespec="minutes") + "Z" if expire else None,
            "jours_restants": round((expire - maintenant).total_seconds() / 86400, 1) if expire else None,
        }
    return etat


async def _renouveler(account_key):
    """Ouvre le profil (ensure_session) sous son verrou. Retourne un libellé de résultat."""
    from services.clemz_auto_message import get_profile_lock, request_preemption, release_preemption

    verrou = get_profile_lock(account_key)
    if verrou.locked():
        return "ignoré (profil utilisé par une autre tâche)"
    async with verrou:
        clemz = account_key in _PROFILS_CLEMZ
        if clemz:
            await request_preemption(account_key)
        try:
            ok = await ensure_session(account_key)
        finally:
            if clemz:
                await release_preemption(account_key)
    if not ok:
        return "ÉCHEC — session perdue, reconnexion non faite"
    apres = expiration_session(account_key)
    return f"renouvelée jusqu'au {apres.strftime('%d/%m %H:%M')} UTC" if apres else "visitée (expiration non relue)"


async def entretenir_sessions(forcer=False):
    """Renouvelle les sessions proches de l'expiration (toutes si forcer).
    Retourne {account_key: résultat}."""
    if not ENTRETIEN_SESSIONS_ACTIF and not forcer:
        return {}
    maintenant = datetime.utcnow()
    resultats = {}
    for cle, compte in ACCOUNTS.items():
        expire = expiration_session(cle)
        restant = (expire - maintenant) if expire else None
        if not forcer and restant is not None and restant > timedelta(days=SEUIL_RENOUVELLEMENT_JOURS):
            resultats[cle] = f"rien à faire (expire dans {restant.days} j)"
            continue
        motif = "aucun cookie de session" if expire is None else f"expire dans {restant.total_seconds() / 86400:.1f} j"
        logger.info(f"🍪 [ENTRETIEN SESSIONS] {compte['name']} : {motif} — ouverture pour renouvellement.")
        try:
            resultats[cle] = await _renouveler(cle)
        except Exception as e:
            resultats[cle] = f"ÉCHEC — {e}"
        # Pas deux navigateurs Vinted d'affilée sans pause
        await asyncio.sleep(20)
    for cle, resultat in resultats.items():
        logger.info(f"🍪 [ENTRETIEN SESSIONS] {ACCOUNTS[cle]['name']} : {resultat}")
    echecs = [ACCOUNTS[c]["name"] for c, r in resultats.items() if r.startswith("ÉCHEC")]
    if echecs:
        _send_notification("⚠️ VintedPro — Sessions Vinted",
                           f"Renouvellement impossible : {', '.join(echecs)}. Reconnecte ces profils depuis Maintenance.")
    return resultats
