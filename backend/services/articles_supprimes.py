"""
Registre des articles SUPPRIMÉS de Vinted par la tâche de suppression Clemz
(clemz_auto_completion, 19/10/2026), en attente de leur republication.

Sans lui, le scraping suivant les marquait "vendus" (absents du dressing
Vinted) et ils disparaissaient de la liste "à republier" du dashboard avant
même d'avoir été republiés. Le scraping (vinted_scraper.sync_to_supabase,
étape C) ne marque donc pas vendu un article présent ici ; la republication
assistée l'en retire dès que sa republication est enregistrée.

Fichier local (hors git) plutôt qu'une colonne Supabase, faute de colonne
existante -- valable pour les scrapings lancés depuis CE poste.
Clé : nom de l'article en minuscules (même clé que l'étape C du scraping).
"""
import json
import os
import threading
from datetime import datetime

_FICHIER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "articles_supprimes_en_attente.json")
_verrou = threading.Lock()


def _lire():
    if not os.path.exists(_FICHIER):
        return {}
    try:
        with open(_FICHIER, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _ecrire(donnees):
    with open(_FICHIER, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)


def proteger(dressing, noms):
    """Inscrit des articles qui viennent d'être supprimés de Vinted."""
    with _verrou:
        donnees = _lire()
        for nom in noms:
            donnees[nom.strip().lower()] = {
                "nom": nom,
                "dressing": dressing,
                "supprime_le": datetime.now().isoformat(timespec="seconds"),
            }
        _ecrire(donnees)


def retirer(nom):
    """Retire un article du registre (republié)."""
    with _verrou:
        donnees = _lire()
        if donnees.pop(nom.strip().lower(), None) is not None:
            _ecrire(donnees)


def est_protege(nom_lower):
    return nom_lower in _lire()


def lister():
    return list(_lire().values())
