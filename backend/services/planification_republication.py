"""
Niveau 3 de la hiérarchie de décision anti-détection (calendrier aléatoire),
et orchestration des 3 niveaux dans leur ordre strict.

Hiérarchie (s'arrête au premier niveau qui se prononce) :
  1. Convalescence à paliers -- risk_guard.get_active_convalescence()
  2. Coupure totale sur volume glissant -- risk_guard.get_jour_pause_volume()
     (republication + baisse de prix additionnées sur JOURS_FENETRE_VOLUME
     jours, cf. risk_guard.py -- remplace l'ancien "jours actifs consécutifs")
  3. Calendrier aléatoire (ce module) :
     - 75% actif / 25% repos, tiré indépendamment chaque jour (pas de
       distinction semaine/week-end)
     - si actif : horaire aléatoire tiré dans CRENEAUX_HORAIRES (cf.
       ci-dessous) -- PAS un horaire fixe, un tirage dans le créneau choisi
     - volume cible tiré selon une loi triangulaire (favorise les valeurs
       moyennes) entre VOLUME_MIN_JOUR_ACTIF (5) et le plafond RÉEL du jour,
       obtenu en interrogeant risk_guard.get_allowed_quantity() -- jamais un
       plafond inventé ici.

D1 et D2 sont décidés indépendamment, puis, si les deux sont actifs le même
jour, le second est retiré jusqu'à obtenir un écart suffisant avec le premier
-- l'écart minimum lui-même est tiré aléatoirement chaque jour (jamais fixe),
cf. generer_plans_du_jour().
"""
import random
from datetime import datetime, time

from services.risk_guard import get_active_convalescence, get_jour_pause_volume, get_allowed_quantity
from services.automation_scheduler import save_plan_du_jour

PROBABILITE_JOUR_ACTIF = 0.75

# Créneaux de republication -- choisis le 07/10/2026 pour viser les heures de
# forte affluence acheteurs sur Vinted (pause déjeuner, soirée), DÉCISION
# BUSINESS explicite de l'utilisateur, pas une recalibration anti-détection.
#
# ATTENTION, compromis assumé : remplace l'ancienne fenêtre continue unique
# (11h-19h), elle-même conçue pour casser la prévisibilité mécanique des
# créneaux fixes historiques (11h30-12h30 / 17h00-18h00), jugés détectables
# par les systèmes de scoring comportemental type DataDome Account Protect.
# Revenir à 2 créneaux étroits et répétés tous les jours réintroduit une part
# de ce risque (atténué par le tirage aléatoire DANS chaque créneau, que
# l'ancien système fixe n'avait pas) -- accepté en connaissance de cause,
# visibilité jugée prioritaire ici.
CRENEAUX_HORAIRES = [
    (time(12, 0, 0), time(13, 0, 0)),
    (time(19, 0, 0), time(21, 0, 0)),
]

# Plancher ABSOLU du tirage triangulaire (cf. tirer_volume_triangulaire) --
# relevé de 1 à 5 le 15/09/2026 : avec un plancher à 1, ~16% des jours actifs
# tiraient un volume < 5 (quasi inutile en pratique). Sert de filet de
# sécurité (ex: si le plafond venait à repasser sous 10) -- en usage normal,
# c'est désormais _minimum_cible_calendrier() ci-dessous qui fixe le plancher
# réellement utilisé pour le calendrier (cf. échange du 05/10/2026).
VOLUME_MIN_JOUR_ACTIF = 5

# Écart entre le plancher du tirage calendrier et le plafond du jour (12-15,
# cf. risk_guard._plafond_republication_du_jour) -- resserré le 05/10/2026 :
# avec l'ancien plancher fixe à 5, la cible du jour (ex: "8 AUJ.") pouvait
# retomber très loin du plafond réellement autorisé (moyenne ~10 sur un
# plafond de 15, quasi la moitié d'écart), perçu comme un quota non respecté
# alors que le plafond lui-même était correct. Avec un écart de 3 : plafond
# 15 -> tirage entre 12 et 15 (moyenne ~13.5) ; plafond 12 -> tirage entre 9
# et 12 (moyenne ~10.5) -- la cible colle désormais au plafond tout en
# gardant un peu de variation jour à jour (pas un chiffre identique à chaque
# fois, cf. le raisonnement anti-détection du 15/09/2026 sur un volume
# systématiquement au max).
ECART_PLANCHER_CIBLE_CALENDRIER = 3


def _minimum_cible_calendrier(plafond: int) -> int:
    return max(VOLUME_MIN_JOUR_ACTIF, plafond - ECART_PLANCHER_CIBLE_CALENDRIER)

# Plafond HAUT du tirage triangulaire du mode "continuité" (cf.
# _garantir_un_dressing_actif) -- DISTINCT de VOLUME_MIN_JOUR_ACTIF ci-dessus,
# qui sert de PLANCHER aux deux tirages (calendrier normal ET continuité).
# Le volume du jour en mode continuité est donc TIRÉ entre VOLUME_MIN_JOUR_ACTIF
# et celui-ci (5-10), pas fixe -- décidé le 21/09/2026 après un premier essai
# à une valeur fixe de 10 : un tirage varie le volume injecté un jour où le
# dressing est déjà en dépassement de son seuil de volume glissant (donc déjà
# "à risque" par construction), au lieu d'imposer systématiquement le même
# chiffre à chaque activation continuité. Plus le volume tiré est élevé, plus
# la sortie du Niveau 2 les jours suivants est retardée (le volume reste plus
# longtemps dans la fenêtre glissante de 3 jours).
VOLUME_MAX_MODE_CONTINUITE = 10

# Écart minimum entre les horaires D1/D2 quand les deux sont actifs le même
# jour -- tiré aléatoirement chaque jour dans cette plage, jamais fixe.
ECART_MINIMUM_MIN_MINUTES = 30
ECART_MINIMUM_MAX_MINUTES = 120

# Très supérieur à tout seuil réel (DAILY_THRESHOLDS) -- sert uniquement à
# extraire le plafond effectif du jour via get_allowed_quantity(), sans jamais
# risquer de le limiter nous-mêmes par erreur.
QUANTITE_SENTINELLE_PLAFOND = 10_000


def _minutes_depuis_minuit(t: time) -> int:
    return t.hour * 60 + t.minute


def tirer_horaire_aleatoire() -> time:
    """
    Tire d'abord UN créneau au hasard parmi CRENEAUX_HORAIRES (chance égale
    entre créneaux, peu importe leur durée respective), puis une minute/seconde
    au hasard À L'INTÉRIEUR -- jamais un horaire fixe, même en ciblant des
    plages de visibilité précises.
    """
    debut, fin = random.choice(CRENEAUX_HORAIRES)
    minutes_min = _minutes_depuis_minuit(debut)
    minutes_max = _minutes_depuis_minuit(fin)
    minutes_tirees = random.randint(minutes_min, minutes_max)
    secondes = random.randint(0, 59)
    return time(minutes_tirees // 60, minutes_tirees % 60, secondes)


def tirer_volume_triangulaire(plafond: int, minimum: int = VOLUME_MIN_JOUR_ACTIF) -> int:
    """
    Distribution triangulaire centrée entre `minimum` et `plafond` -- favorise
    les valeurs moyennes plutôt que les extrêmes, pour qu'un volume
    systématiquement au max ou au min ne devienne pas lui-même un signal
    mécanique. Retourne 0 si le plafond est déjà épuisé (plafond < minimum).
    """
    if plafond < minimum:
        return 0
    if plafond == minimum:
        return minimum
    mode = (minimum + plafond) / 2
    return round(random.triangular(minimum, plafond, mode))


def _eviter_chevauchement(horaire: time, horaires_a_eviter: list, ecart_minimum_minutes: int, max_tentatives: int = 30) -> time:
    """
    Retire un nouvel horaire tant que l'écart avec CHAQUE horaire à éviter
    n'atteint pas ecart_minimum_minutes. Best-effort après max_tentatives
    (ne bloque jamais indéfiniment) -- en pratique, avec une plage de 8h et un
    écart demandé de 30-120 min, une collision persistante après 30 tirages
    est extrêmement improbable.
    """
    tentative = horaire
    for _ in range(max_tentatives):
        if all(
            abs(_minutes_depuis_minuit(tentative) - _minutes_depuis_minuit(h)) >= ecart_minimum_minutes
            for h in horaires_a_eviter
        ):
            return tentative
        tentative = tirer_horaire_aleatoire()
    return tentative


def decider_jour(dressing: str, action_type: str = "republication") -> dict:
    """
    Applique la hiérarchie à 3 niveaux pour UN dressing et retourne la
    décision brute (horaire en `datetime.time`, pas encore stringifiée --
    cf. generer_plan_du_jour() pour la persistance) :
      {"niveau": "convalescence"|"repos_force"|"calendrier",
       "actif": bool, "horaire": time|None, "volume_cible": int,
       "convalescence": {...}|None}
    """
    convalescence = get_active_convalescence(dressing)
    if convalescence is not None:
        actif = convalescence["jour_actif"]
        return {
            "niveau": "convalescence",
            "actif": actif,
            "horaire": tirer_horaire_aleatoire() if actif else None,
            "volume_cible": convalescence["quota_du_jour"] if actif else 0,
            "convalescence": convalescence,
        }

    if get_jour_pause_volume(dressing):
        return {"niveau": "repos_force", "actif": False, "horaire": None, "volume_cible": 0, "convalescence": None}

    actif = random.random() < PROBABILITE_JOUR_ACTIF
    if actif:
        plafond, _, _ = get_allowed_quantity(dressing, action_type, QUANTITE_SENTINELLE_PLAFOND)
        volume_cible = tirer_volume_triangulaire(plafond, minimum=_minimum_cible_calendrier(plafond))
        actif = volume_cible > 0  # plafond déjà épuisé (fenêtre glissante 24h) malgré le tirage "actif"
    else:
        volume_cible = 0

    return {
        "niveau": "calendrier",
        "actif": actif,
        "horaire": tirer_horaire_aleatoire() if actif else None,
        "volume_cible": volume_cible if actif else 0,
        "convalescence": None,
    }


def generer_plan_du_jour(dressing: str, action_type: str = "republication", horaires_a_eviter=None, ecart_minimum_minutes=None) -> dict:
    """
    Décide et PERSISTE le plan du jour pour UN dressing (cf. decider_jour()).

    horaires_a_eviter / ecart_minimum_minutes : optionnel, utilisé par
    generer_plans_du_jour() pour le second dressing tiré, afin de garantir un
    écart suffisant avec l'horaire déjà retenu pour le premier.
    """
    decision = decider_jour(dressing, action_type)

    horaire = decision["horaire"]
    if horaire is not None and horaires_a_eviter and ecart_minimum_minutes:
        horaire = _eviter_chevauchement(horaire, horaires_a_eviter, ecart_minimum_minutes)

    plan = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "dressing": dressing,
        "niveau": decision["niveau"],
        "actif": decision["actif"],
        "horaire": horaire.strftime("%H:%M:%S") if horaire else None,
        "volume_cible": decision["volume_cible"],
        "convalescence": decision["convalescence"],
        "generated_at": datetime.now().isoformat(),
    }
    save_plan_du_jour(dressing, plan)
    return plan


def rafraichir_pause_si_necessaire(dressing: str, action_type: str = "republication"):
    """
    À appeler après une action MANUELLE (log_action) pour que le plan du jour
    déjà affiché sur le dashboard reflète immédiatement une nouvelle
    convalescence ou coupure de volume glissant (Niveaux 1/2) déclenchée par
    cette action -- sans jamais re-tirer le calendrier aléatoire (Niveau 3,
    non déterministe) si aucune pause ne s'impose : contrairement à
    generer_plan_du_jour(), qui rejouerait un tout nouvel horaire/volume à
    chaque appel, celle-ci ne touche au plan QUE pour le basculer en repos, et
    ne fait rien si le dressing est déjà en repos ou si aucune pause ne
    s'applique (cf. échange du 10/09/2026).
    """
    from services.risk_guard import get_active_convalescence, get_jour_pause_volume
    from services.automation_scheduler import get_plan_du_jour
    from services.plan_source import est_satellite

    if est_satellite():
        return  # la VM corrige elle-même son plan (vérification toutes les 15 min)

    convalescence = get_active_convalescence(dressing)
    pause_volume = convalescence is None and get_jour_pause_volume(dressing)
    if convalescence is None and not pause_volume:
        return

    plan_actuel = get_plan_du_jour(dressing)
    if plan_actuel is not None and not plan_actuel.get("actif"):
        return  # déjà en repos aujourd'hui, rien à corriger
    # Jour de continuité (Niveau 2 volontairement levé pour garantir un
    # dressing actif, cf. _garantir_un_dressing_actif) : sans cette garde, la
    # première action logguée remettrait aussitôt le plan en repos_force. Une
    # vraie convalescence reste, elle, toujours prioritaire.
    if plan_actuel is not None and plan_actuel.get("niveau") == "continuite" and convalescence is None:
        return

    decision_dressing = {
        "niveau": "convalescence" if convalescence else "repos_force",
        "actif": False,
        "horaire": None,
        "volume_cible": 0,
        "convalescence": convalescence,
    }

    # Cette bascule en repos peut faire tomber les DEUX dressings en repos le
    # même jour (cf. échange du 21/09/2026 : D1 déjà en repos_force depuis
    # minuit, cette action a fait passer D2 en repos_force aussi en cours de
    # journée) -- generer_plans_du_jour() applique la garantie de continuité
    # une seule fois, à minuit, donc un basculement en cours de journée comme
    # celui-ci la contourne complètement si on ne la rejoue pas ici.
    autre = "Dressing 2" if dressing == "Dressing 1" else "Dressing 1"
    plan_autre = get_plan_du_jour(autre)
    if plan_autre is not None and plan_autre.get("date") == datetime.now().strftime("%Y-%m-%d"):
        decision_autre = {
            "niveau": plan_autre.get("niveau"),
            "actif": plan_autre.get("actif", False),
            "horaire": None,  # toujours None si actif=False (format persisté) ; non lu si actif=True
            "volume_cible": plan_autre.get("volume_cible", 0),
            "convalescence": plan_autre.get("convalescence"),
        }
        nouvelle_dressing, nouvelle_autre = _garantir_un_dressing_actif(
            dressing, decision_dressing, autre, decision_autre, action_type
        )
        if nouvelle_autre is not decision_autre:
            plan_autre_persiste = _persister_decision(autre, nouvelle_autre)
            # La garantie de continuité vient de réactiver l'AUTRE dressing --
            # sans ceci, seul plan_du_jour_*.json changerait (affichage), mais
            # aucun job APScheduler ne serait jamais programmé pour exécuter
            # cette republication (contrairement au cron de minuit, qui le
            # fait toujours) : la journée "continuité" resterait purement
            # cosmétique (cf. échange du 21/09/2026).
            _replanifier_job_oneshot(autre, plan_autre_persiste)
        decision_dressing = nouvelle_dressing

    plan = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "dressing": dressing,
        "niveau": decision_dressing["niveau"],
        "actif": decision_dressing["actif"],
        "horaire": decision_dressing["horaire"].strftime("%H:%M:%S") if decision_dressing["horaire"] else None,
        "volume_cible": decision_dressing["volume_cible"],
        "convalescence": decision_dressing["convalescence"],
        "generated_at": datetime.now().isoformat(),
    }
    save_plan_du_jour(dressing, plan)
    if plan["actif"]:
        # Cas symétrique : c'est `dressing` lui-même (pas l'autre) que la
        # garantie de continuité a choisi de réactiver -- même besoin de
        # programmer réellement le job (cf. commentaire ci-dessus).
        _replanifier_job_oneshot(dressing, plan)


def _replanifier_job_oneshot(dressing: str, plan: dict):
    """
    Programme réellement le job APScheduler de republication à partir d'un
    plan du jour déjà persisté et actif -- utilisé quand un plan est modifié
    EN COURS DE JOURNÉE (garantie de continuité déclenchée hors du cron de
    minuit). Import différé : automation_service importe ce module au niveau
    module (generer_plans_du_jour), l'inverse au niveau module créerait un
    cycle (cf. échange du 15-16/09/2026 sur les imports circulaires risk_guard
    / planification_republication).
    """
    from services.automation_service import _planifier_republish_oneshot
    creneau = "midi" if dressing == "Dressing 1" else "soir"
    job_id = "republish_oneshot_d1" if dressing == "Dressing 1" else "republish_oneshot_d2"
    _planifier_republish_oneshot(dressing, plan, job_id, creneau)


def rafraichir_repos_si_leve(dressing: str, action_type: str = "republication"):
    """
    Symétrique de rafraichir_pause_si_necessaire() : le plan du jour n'est
    généré qu'une fois à 00:05, donc un repos Niveaux 1/2 (convalescence,
    coupure volume) mis en cache à ce moment-là reste affiché jusqu'à minuit
    même si la raison qui l'a motivé n'est PLUS vraie maintenant (palier de
    convalescence arrivé à échéance en cours de journée, volume glissant
    retombé sous le seuil...) -- incohérence constatée le 11/09/2026 :
    baisse_prix (jamais mis en cache, recalculé en direct) redevenait
    disponible immédiatement, pas la republication.

    Régénère alors un plan frais (donne enfin sa chance au Niveau 3 -- calendrier
    -- de trancher pour aujourd'hui), à appeler à chaque lecture "live" du
    statut (route /risk-guard/statut, bouton manuel) pour rester à jour.

    Idempotent : une fois régénéré, le nouveau plan porte niveau='calendrier'
    (ou de nouveau 'convalescence'/'repos_force' si une NOUVELLE restriction
    est entre-temps apparue) -- les appels suivants ne détectent plus aucune
    obsolescence et ne re-tirent donc plus rien tant que rien ne change.

    Ne touche jamais à un repos Niveau 3 (calendrier) : cette décision est
    indépendante du quota et reste valable toute la journée par conception.
    """
    from services.risk_guard import get_active_convalescence, get_jour_pause_volume
    from services.automation_scheduler import get_plan_du_jour
    from services.plan_source import est_satellite

    if est_satellite():
        return  # jamais de re-tirage sur le satellite : la VM fait foi

    plan = get_plan_du_jour(dressing)
    if plan is None or plan.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return  # rien à corriger -- le cron de minuit s'en chargera
    if plan.get("actif") or plan.get("niveau") not in ("convalescence", "repos_force"):
        return  # déjà actif, ou repos Niveau 3 -- toujours valable, on n'y touche pas

    if get_active_convalescence(dressing) is not None or get_jour_pause_volume(dressing):
        return  # la raison du repos mis en cache tient toujours -- rien à régénérer

    generer_plan_du_jour(dressing, action_type)


def _garantir_un_dressing_actif(dressing_a: str, decision_a: dict, dressing_b: str, decision_b: dict, action_type: str):
    """
    Garantit qu'au moins un des deux dressings republie chaque jour --
    exigence de continuité business posée le 20/09/2026 ("quitte à lâcher du
    lest"), à 3 paliers du moins au plus risqué. Ne fait rien si l'un des
    deux est déjà actif.

    Palier 1 -- au moins un repos CALENDRIER (Niveau 3, simple tirage à 25%) :
    on réactive celui-là, tirage normal (volume triangulaire + horaire).
    Coût en risque nul : aucun signal de sécurité derrière ce repos. Si les
    deux sont en repos calendrier, départage sur le plus petit cumul de
    republication des JOURS_FENETRE_VOLUME derniers jours.

    Palier 2 -- aucun repos calendrier, au moins un repos Niveau 2 (coupure
    sur volume glissant) : on lève le Niveau 2 pour UN seul dressing (le plus
    petit cumul combiné, donc le moins en dépassement) en MODE DÉGRADÉ --
    volume plafonné à VOLUME_MAX_MODE_CONTINUITE au lieu d'un tirage complet,
    et niveau='continuite' pour retrouver ces jours-là dans l'analyse. Les
    chemins d'exécution (risk_guard.get_allowed_quantity) honorent ce plan.

    Palier 3 -- il ne reste que des convalescences : RIEN n'est forcé. La
    convalescence (Niveau 1) est une vraie suspension constatée par Vinted,
    jamais outrepassée (choix explicite du 20/09/2026).
    """
    if decision_a["actif"] or decision_b["actif"]:
        return decision_a, decision_b

    from services.risk_guard import get_volume_last_hours, get_volume_glissant_combine, JOURS_FENETRE_VOLUME

    decisions = {dressing_a: decision_a, dressing_b: decision_b}
    en_repos_calendrier = [d for d, dec in decisions.items() if dec["niveau"] == "calendrier"]
    en_repos_niveau2 = [d for d, dec in decisions.items() if dec["niveau"] == "repos_force"]

    if en_repos_calendrier:
        heures_fenetre = JOURS_FENETRE_VOLUME * 24
        choisi = min(en_repos_calendrier, key=lambda d: get_volume_last_hours(d, "republication", hours=heures_fenetre))
        plafond, _, _ = get_allowed_quantity(choisi, action_type, QUANTITE_SENTINELLE_PLAFOND)
        volume_cible = tirer_volume_triangulaire(plafond, minimum=_minimum_cible_calendrier(plafond))
        niveau = "calendrier"
    elif en_repos_niveau2:
        choisi = min(en_repos_niveau2, key=get_volume_glissant_combine)
        plafond, _, _ = get_allowed_quantity(choisi, action_type, QUANTITE_SENTINELLE_PLAFOND, ignorer_repos_niveau2=True)
        # Tiré, pas fixe (cf. échange du 21/09/2026) -- même logique de tirage
        # triangulaire que le calendrier normal (favorise les valeurs
        # moyennes), entre VOLUME_MIN_JOUR_ACTIF (5) et VOLUME_MAX_MODE_CONTINUITE
        # (10) au lieu d'un volume identique à chaque activation continuité.
        volume_cible = tirer_volume_triangulaire(min(VOLUME_MAX_MODE_CONTINUITE, plafond), minimum=VOLUME_MIN_JOUR_ACTIF)
        niveau = "continuite"
    else:
        return decision_a, decision_b  # convalescence(s) uniquement -- intouchable

    if volume_cible <= 0:
        return decision_a, decision_b

    nouvelle_decision = {
        "niveau": niveau,
        "actif": True,
        "horaire": tirer_horaire_aleatoire(),
        "volume_cible": volume_cible,
        "convalescence": None,
    }
    if choisi == dressing_a:
        return nouvelle_decision, decision_b
    return decision_a, nouvelle_decision


def _persister_decision(dressing: str, decision: dict) -> dict:
    """Stringifie l'horaire (time -> "HH:MM:SS") et persiste -- même format que generer_plan_du_jour()."""
    horaire = decision["horaire"]
    plan = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "dressing": dressing,
        "niveau": decision["niveau"],
        "actif": decision["actif"],
        "horaire": horaire.strftime("%H:%M:%S") if horaire else None,
        "volume_cible": decision["volume_cible"],
        "convalescence": decision["convalescence"],
        "generated_at": datetime.now().isoformat(),
    }
    save_plan_du_jour(dressing, plan)
    return plan


def generer_plans_du_jour(action_type: str = "republication"):
    """
    Point d'entrée du job de minuit : décide les DEUX dressings
    indépendamment, garantit qu'au moins un des deux reste actif s'ils
    tombent tous les deux en repos calendrier par pur hasard (cf.
    _garantir_un_dressing_actif), ajuste l'horaire de
    Dressing 2 si besoin pour garantir un écart suffisant avec Dressing 1
    quand les deux sont actifs le même jour (écart lui-même tiré
    aléatoirement chaque jour), puis persiste. Retourne (plan_d1, plan_d2).
    """
    decision_d1 = decider_jour("Dressing 1", action_type)
    decision_d2 = decider_jour("Dressing 2", action_type)

    decision_d1, decision_d2 = _garantir_un_dressing_actif(
        "Dressing 1", decision_d1, "Dressing 2", decision_d2, action_type
    )

    if decision_d1["actif"] and decision_d2["actif"] and decision_d1["horaire"] and decision_d2["horaire"]:
        ecart_minimum = random.randint(ECART_MINIMUM_MIN_MINUTES, ECART_MINIMUM_MAX_MINUTES)
        decision_d2["horaire"] = _eviter_chevauchement(decision_d2["horaire"], [decision_d1["horaire"]], ecart_minimum)

    plan_d1 = _persister_decision("Dressing 1", decision_d1)
    plan_d2 = _persister_decision("Dressing 2", decision_d2)

    return plan_d1, plan_d2
