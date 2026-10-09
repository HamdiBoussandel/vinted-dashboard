"""
Source du plan du jour (10/10/2026) -- la VM est la SEULE source de vérité
pour le tirage anti-détection (actif/repos, horaire, volume cible).

Jusqu'ici, chaque PC tirait son propre plan à 00:05 et le stockait localement
(plan_du_jour_d1/d2.json) : la VM et le PC de dev avaient des plans
différents le même jour, et le PC de dev appliquait le sien à ses actions
manuelles. Les quotas consommés et les convalescences, eux, sont déjà
partagés (Supabase), et le plafond quotidien 12-15 est seedé par
(dressing, date) donc identique partout -- seul le plan restait local.

Rôle de la machine, décidé par backend/.env :
  - PLAN_SOURCE_URL absent  -> machine de référence (la VM) : tire, génère,
    corrige et persiste le plan comme avant.
  - PLAN_SOURCE_URL présent -> "satellite" (PC de dev) : ne tire, ne génère
    et ne modifie JAMAIS de plan ; lit celui de la VM via
    GET {PLAN_SOURCE_URL}/risk-guard/plan-du-jour (cache 60 s).

VM injoignable sans copie du jour en cache : plan synthétique "repos"
(niveau PLAN_INDISPONIBLE) -- bloque toute republication manuelle depuis le
satellite plutôt que d'ignorer le calendrier (choix explicite du 10/10/2026).
"""
import os
import time
from datetime import datetime

import httpx

PLAN_INDISPONIBLE = "plan_vm_indisponible"

DUREE_CACHE_SECONDES = 60
DUREE_CACHE_ECHEC_SECONDES = 20  # évite de bloquer chaque appel 3 s quand la VM est éteinte
TIMEOUT_SECONDES = 3

_cache = {"plans": None, "lu_a": 0.0, "echec_a": 0.0}


def url_source():
    """URL de l'API de la VM (ex. http://192.168.1.42:8000/api), ou "" sur la VM elle-même."""
    return os.getenv("PLAN_SOURCE_URL", "").strip().rstrip("/")


def est_satellite():
    return bool(url_source())


def _plan_indisponible(dressing, aujourdhui):
    return {
        "date": aujourdhui,
        "dressing": dressing,
        "niveau": PLAN_INDISPONIBLE,
        "actif": False,
        "horaire": None,
        "volume_cible": 0,
        "convalescence": None,
        "generated_at": None,
    }


def get_plan_distant(dressing):
    """
    Plan du jour de la VM pour ce dressing. Toujours un dict sur le satellite :
    le vrai plan de la VM, ou un plan synthétique de repos (PLAN_INDISPONIBLE)
    si la VM est injoignable / n'a pas encore de plan pour aujourd'hui.
    """
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    maintenant = time.monotonic()
    plans = _cache["plans"]
    cache_valide = plans is not None and plans.get("date") == aujourdhui

    doit_relire = (
        (not cache_valide or maintenant - _cache["lu_a"] >= DUREE_CACHE_SECONDES)
        and maintenant - _cache["echec_a"] >= DUREE_CACHE_ECHEC_SECONDES
    )
    if doit_relire:
        try:
            reponse = httpx.get(f"{url_source()}/risk-guard/plan-du-jour", timeout=TIMEOUT_SECONDES)
            reponse.raise_for_status()
            plans = reponse.json()
            _cache.update(plans=plans, lu_a=maintenant)
            cache_valide = plans.get("date") == aujourdhui
        except Exception as e:
            _cache["echec_a"] = maintenant
            print(f"⚠️ [PLAN DU JOUR] VM injoignable ({url_source()}) : {str(e)[:120]}"
                  f"{' — dernière copie du jour conservée.' if cache_valide else ' — republication manuelle bloquée.'}")

    if not cache_valide:
        return _plan_indisponible(dressing, aujourdhui)
    plan = plans.get(dressing)
    if not plan or plan.get("date") != aujourdhui:
        return _plan_indisponible(dressing, aujourdhui)
    return plan
