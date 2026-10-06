"""
Logique de clustering des photos par article, partagée entre le script
diagnostic (photo_clustering_diag.py) et le watcher de production
(photo_watcher.py).
"""
import os
import re
from datetime import datetime
from PIL import Image

EXTENSIONS_VALIDES = (".jpg", ".jpeg", ".png")

# Écart (en secondes) entre deux photos consécutives (triées par heure de prise
# de vue) au-delà duquel on considère qu'on change d'article.
SEUIL_ECART_GROUPE_SECONDES = 90

# Fusion auto d'un petit groupe "mesures" avec le groupe précédent (pattern
# observé : vêtement photographié, pause, puis photos des mesures séparément).
SEUIL_FUSION_MESURES_SECONDES = 420  # 7 min, marge au-dessus du cas réel observé (5:49)
TAILLE_MAX_GROUPE_MESURES = 5

# Format caméra Android standard : 20260802_184625.jpg
PATTERN_NOM_FICHIER = re.compile(r'(?<!\d)(20\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?!\d)')


def get_filename_datetime(chemin_fichier):
    """Extrait l'heure de prise de vue encodée dans le nom (le plus fiable)."""
    match = PATTERN_NOM_FICHIER.search(os.path.basename(chemin_fichier))
    if not match:
        return None
    annee, mois, jour, heure, minute, seconde = (int(g) for g in match.groups())
    try:
        return datetime(annee, mois, jour, heure, minute, seconde)
    except ValueError:
        return None


def get_exif_datetime(chemin_fichier):
    """Extrait DateTimeOriginal depuis l'EXIF si présent."""
    try:
        img = Image.open(chemin_fichier)
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)
        raw = exif_ifd.get(0x9003) or exif.get(0x0132)
        if raw:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def get_photo_datetime(chemin_fichier):
    """
    Résout l'heure de prise de vue en cascade de fiabilité :
    nom de fichier > EXIF > mtime disque.
    Retourne (heure, source) — source indique la fiabilité utilisée.
    """
    heure = get_filename_datetime(chemin_fichier)
    if heure:
        return heure, "filename"

    heure = get_exif_datetime(chemin_fichier)
    if heure:
        return heure, "exif"

    return datetime.fromtimestamp(os.path.getmtime(chemin_fichier)), "mtime"


def fusionner_groupes_mesures(groupes):
    """
    Fusionne un petit groupe "mesures" avec le groupe précédent quand :
    - le groupe est petit (<= TAILLE_MAX_GROUPE_MESURES photos)
    - il est plus petit que le groupe précédent
    - l'écart avec la fin du groupe précédent est <= SEUIL_FUSION_MESURES_SECONDES
    Fusion silencieuse (pas de confirmation dashboard), par choix explicite de Nini.
    """
    if not groupes:
        return groupes

    resultat = [groupes[0]]
    for groupe in groupes[1:]:
        precedent = resultat[-1]
        ecart = (groupe[0][1] - precedent[-1][1]).total_seconds()

        est_petit_groupe_mesures = (
            len(groupe) <= TAILLE_MAX_GROUPE_MESURES
            and len(groupe) < len(precedent)
            and ecart <= SEUIL_FUSION_MESURES_SECONDES
        )

        if est_petit_groupe_mesures:
            resultat[-1] = precedent + groupe
        else:
            resultat.append(groupe)

    return resultat


def grouper_photos_par_article(chemins_fichiers, seuil_secondes=SEUIL_ECART_GROUPE_SECONDES):
    """
    Regroupe une liste de chemins de photos par article, en se basant sur l'écart
    entre heures de prise de vue successives (nom de fichier > EXIF > mtime).
    Les photos non-datables de façon fiable (mtime seul, ex: WhatsApp) sont
    retournées à part dans "a_trier_manuellement".

    Retourne (groupes, a_trier_manuellement) :
      - groupes : liste de groupes, chaque groupe = liste de (chemin, heure, source)
      - a_trier_manuellement : liste de (chemin, heure)
    """
    fiables = []
    a_trier_manuellement = []

    for f in chemins_fichiers:
        heure, source = get_photo_datetime(f)
        if source == "mtime":
            a_trier_manuellement.append((f, heure))
        else:
            fiables.append((f, heure, source))

    fiables.sort(key=lambda x: x[1])

    groupes = []
    groupe_courant = []
    for chemin, heure, source in fiables:
        if groupe_courant and (heure - groupe_courant[-1][1]).total_seconds() > seuil_secondes:
            groupes.append(groupe_courant)
            groupe_courant = []
        groupe_courant.append((chemin, heure, source))
    if groupe_courant:
        groupes.append(groupe_courant)

    groupes = fusionner_groupes_mesures(groupes)

    return groupes, a_trier_manuellement