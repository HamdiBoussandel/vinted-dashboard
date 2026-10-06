import time

_gemini_call_times = []

def wait_for_gemini_rate_limit(max_per_minute=15):
    """Bloque l'exécution si nécessaire pour respecter la limite de requêtes Gemini.
    Partagé entre tous les appelants (routes/inventory.py, price_estimation_service.py, ...)
    pour que le comptage reflète le VRAI total d'appels, pas un compteur par module."""
    now = time.time()
    global _gemini_call_times
    _gemini_call_times = [t for t in _gemini_call_times if now - t < 60]

    if len(_gemini_call_times) >= max_per_minute:
        oldest = _gemini_call_times[0]
        wait_time = 60 - (now - oldest) + 0.5
        if wait_time > 0:
            print(f"⏳ Limite Gemini atteinte ({max_per_minute}/min) -- attente de {wait_time:.1f}s avant le prochain appel.")
            time.sleep(wait_time)

    _gemini_call_times.append(time.time())


# --- Rotation de clés Gemini ---
# Chaque clé provient idéalement d'un PROJET Google Cloud distinct -- le quota
# journalier (voir is_daily_quota_error) est appliqué par projet, donc plusieurs
# clés du même projet ne changeraient rien.
_gemini_keys = []
_gemini_key_index = 0


def set_gemini_keys(keys):
    """À appeler une fois au chargement (depuis database.GEMINI_API_KEYS)."""
    global _gemini_keys, _gemini_key_index
    _gemini_keys = list(keys) or []
    _gemini_key_index = 0


def get_current_gemini_key():
    if not _gemini_keys:
        return None
    return _gemini_keys[_gemini_key_index % len(_gemini_keys)]


def rotate_gemini_key():
    """Passe à la clé suivante (cyclique). Retourne la nouvelle clé courante."""
    global _gemini_key_index
    if not _gemini_keys:
        return None
    _gemini_key_index = (_gemini_key_index + 1) % len(_gemini_keys)
    return get_current_gemini_key()


def nb_gemini_keys():
    return len(_gemini_keys)


_DAILY_QUOTA_MARKERS = ("PerDay", "GenerateRequestsPerDayPerProjectPerModel")


def is_daily_quota_error(exc):
    """
    Détecte spécifiquement un dépassement de quota JOURNALIER (par clé/projet),
    distinct d'un simple 429 de rythme par minute -- seul un dépassement
    journalier justifie de tourner vers une autre clé ; un 429 par minute se
    résorbe tout seul via wait_for_gemini_rate_limit, retenter sur une autre
    clé n'apporterait rien dans ce cas.
    """
    message = str(exc)
    return any(marker in message for marker in _DAILY_QUOTA_MARKERS)