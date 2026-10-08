"""
Circuit breaker anti-détection : limite le volume d'actions automatisées par
compte/jour, sur la base du pattern observé empiriquement (suspensions
survenues 2 à 7 jours après un rythme de 5-10 republications/jour sur un
compte). Trois mécanismes combinés :

1. Quota quotidien de base (DAILY_THRESHOLDS) -- plafond simple par compte/type.
2. Convalescence post-suspension, À PALIERS selon la récidive (nombre de
   suspensions du dressing dans les FENETRE_RECIDIVE_JOURS jours précédant la
   plus récente) : 1ère -> PALIERS_CONVALESCENCE_JOURS[1] jours, 2ème ->
   PALIERS_CONVALESCENCE_JOURS[2], 3ème ou plus -> DUREE_PALIER_MAX_JOURS.
   REPOS TOTAL (quota 0) pendant toute la durée du palier -- décision du
   06/09/2026 : une restriction Vinted réelle ne dure qu'environ 24h, le palier
   sert de marge de sécurité ADDITIONNELLE, pas d'un simple ralentissement --
   à partir du jour ET DE L'HEURE réels de la suspension (cf. report_suspension(),
   qui accepte un detected_at explicite plutôt que l'instant du signalement).
3. Coupure totale sur volume glissant -- révisé le 10/09/2026 après analyse de
   4 restrictions réelles (republication ET baisse de prix confondues) : dans
   3 cas sur 4, un pic de volume combiné (les deux types d'action, tous
   dressings comptés séparément) dans les JOURS_FENETRE_VOLUME jours précédant
   la restriction. Remplace l'ancien "jours actifs consécutifs" (qui ne
   comptait que des jours, jamais le volume, et séparément par type d'action --
   angle mort qui laissait par exemple passer 20 republications ET 15 baisses
   de prix le même jour sans réagir). Décision BINAIRE, pas de réduction
   partielle : si le cumul des JOURS_FENETRE_VOLUME derniers jours (republication
   + baisse de prix additionnées) atteint SEUIL_VOLUME_GLISSANT, quota total à
   0 aujourd'hui pour les deux types d'action -- objectif : se rapprocher d'un
   rythme humain (qui ne publie/baisse pas tous les jours de la semaine)
   plutôt qu'un lissage mécanique continu.

Seuils volontairement conservateurs au démarrage -- à remonter progressivement
une fois plusieurs semaines "propres" observées via la télémétrie
(automation_actions_log).
"""
import math
import random
from datetime import datetime, timedelta
from services.supabase_service import SupabaseService

# Seuils quotidiens par type d'action, par compte.
DAILY_THRESHOLDS = {
    "republication": {
        # Révisé le 15/09/2026 suite au post officiel du forum Clemz : Vinted
        # semble surveiller le VOLUME de publications récentes (pas Clemz en
        # tant que tel), et leur recommandation est de démarrer entre 10 et 20
        # MAX/jour. 20 (choisi le 01/09/2026 suite à un bug de doublon, jamais
        # redescendu depuis) était déjà au plafond haut de cette fourchette.
        # 15 choisi délibérément : Clemz signale que c'est autour de ce chiffre
        # que les premières alertes commencent à apparaître -- accepté comme
        # risque calculé pour obtenir un signal plus vite, plutôt que la valeur
        # la plus prudente (10).
        #
        # DEPUIS le 02/10/2026 : ces valeurs ne sont plus utilisées comme
        # plafond direct pour D1/D2 -- cf. _plafond_republication_du_jour(),
        # qui tire désormais un plafond entre PLAFOND_REPUBLICATION_MIN (12) et
        # PLAFOND_REPUBLICATION_MAX (15) chaque jour. Gardées comme borne haute
        # de référence et comme fallback pour un dressing hors D1/D2.
        "Dressing 1": 15,
        "Dressing 2": 15,
        "_default": 3,
    },
    "baisse_prix": {
        # Décision du 01/09/2026 : baisse_prix et republication sont deux
        # actions distinctes, chacune son propre quota, pour CHAQUE dressing
        # INDÉPENDAMMENT (get_volume_last_hours filtre par dressing -- aucun
        # pool commun entre D1 et D2 ; baisse_prix n'a juste pas de valeur PAR
        # COMPTE distincte dans ce dict, les deux tombent sur "_default").
        #
        # Divisé par 2 le 19/09/2026 (15 -> 7) pour réserver plus de marge à la
        # republication dans le budget PARTAGÉ du Niveau 2 (coupure sur volume
        # glissant, 18 articles/3j). Remonté à 10 le 02/10/2026 sur décision
        # explicite de l'utilisateur, après plusieurs jours d'observation sans
        # restriction directement attribuable à la baisse de prix.
        "_default": 10,
    },
}

# Plafond BRUT quotidien de republication, tiré au hasard chaque jour (stable
# pour la journée, indépendant par dressing) -- décidé le 02/10/2026 pour
# casser encore la prévisibilité mécanique d'un plafond fixe à 15, en plus du
# tirage triangulaire du volume CIBLE (Niveau 3, planification_republication.py)
# qui choisit déjà entre VOLUME_MIN_JOUR_ACTIF (5) et ce plafond.
PLAFOND_REPUBLICATION_MIN = 12
PLAFOND_REPUBLICATION_MAX = 15


def _plafond_republication_du_jour(dressing: str) -> int:
    """
    Tire le plafond brut de republication du jour pour ce dressing, entre
    PLAFOND_REPUBLICATION_MIN et PLAFOND_REPUBLICATION_MAX. Utilise un
    générateur dédié (random.Random), SEEDÉ par (dressing, date du jour) --
    PAS le module random global -- pour deux raisons : (1) rester stable à
    chaque appel du même jour (get_allowed_quantity est interrogé en continu
    par le dashboard, un tirage différent à chaque appel rendrait le quota
    incohérent dans la même journée), et (2) ne jamais désynchroniser les
    autres tirages aléatoires du système (calendrier Niveau 3, volume
    triangulaire, horaires...), qui eux doivent rester réellement
    imprévisibles d'un jour à l'autre.
    """
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    rng = random.Random(f"{dressing}-{aujourdhui}-plafond_republication")
    return rng.randint(PLAFOND_REPUBLICATION_MIN, PLAFOND_REPUBLICATION_MAX)


# Convalescence à paliers -- sévérité croissante selon la récidive (révisé le
# 06/09/2026 : les 14j/30j/60j initiaux étaient disproportionnés par rapport à
# une restriction Vinted réelle, qui ne dure qu'environ 24h -- le palier reste
# une marge de sécurité volontairement plus longue, mais pas d'un ordre de
# grandeur x10-60). Repos TOTAL (quota 0) sur toute la durée, pas de cycle
# actif/pause -- un palier court est censé être une vraie pause.
FENETRE_RECIDIVE_JOURS = 90            # fenêtre glissante pour compter les récidives
PALIERS_CONVALESCENCE_JOURS = {1: 2, 2: 5}  # durée du palier selon le rang de récidive
DUREE_PALIER_MAX_JOURS = 10            # 3e récidive ou plus (rangs non listés ci-dessus)

# Ramp-up progressif après la fin d'un palier de convalescence -- ajouté le
# 15/09/2026 suite au post officiel du forum Clemz : jusqu'ici, le quota
# revenait instantanément au régime normal dès la fin du palier, alors que
# Clemz recommande explicitement de redémarrer petit (10-20/jour) et de
# remonter PROGRESSIVEMENT sur une semaine si ça tient, plutôt qu'un retour
# brutal au plein régime. S'applique aux DEUX types d'action (republication
# ET baisse_prix), comme la convalescence elle-même. Rampe linéaire de
# RAMP_QUOTA_DEPART à 100% du seuil normal sur RAMP_DUREE_JOURS jours.
RAMP_DUREE_JOURS = 7
RAMP_QUOTA_DEPART = 5

# Niveau 3 (coupure sur volume glissant) -- calibré le 10/09/2026 sur 4 restrictions
# réelles : dans les cas les plus "immédiats" (D1 27-28/08, D2 22-23/08), le cumul
# des JOURS_FENETRE_VOLUME jours précédents (republication + baisse de prix
# additionnées) approchait ou dépassait 18-21 articles.
#
# Remonté le 05/10/2026 de 18 à 21 (décision explicite de l'utilisateur, après
# avoir été prévenu que 18-21 est précisément la fourchette où les 4 restrictions
# réelles ci-dessus se sont produites) -- la marge de sécurité d'origine (couper
# un jour avant cette zone) est donc entièrement rognée : ce seuil colle
# maintenant à la borne HAUTE déjà associée à de vraies suspensions, plus à la
# borne basse. Risque accru assumé, pas une recalibration prudente.
#
# Remonté une 2e fois le 07/10/2026, de 21 à 24 -- démarche explicite de
# l'utilisateur : monter PAR PALIERS mesurés pour chercher empiriquement le
# volume réel qui déclenche une restriction, plutôt qu'un saut direct à la
# valeur "confortable" (36-45) qui permettrait de tenir indéfiniment le
# plafond quotidien (12-15, cf. PLAFOND_REPUBLICATION_MIN/MAX) sans aucune
# coupure -- refusé comme trop éloigné d'un coup de la fourchette 18-21 à
# risque. 24 = exactement 2 jours consécutifs à 12 republications sans
# déclencher le Niveau 2, le minimum visé par l'utilisateur. Chaque palier
# s'éloigne davantage de la marge de sécurité d'origine -- à ne RE-remonter
# que sur nouvelle décision explicite, jamais par extrapolation automatique.
JOURS_FENETRE_VOLUME = 3
SEUIL_VOLUME_GLISSANT = 24

_supabase = SupabaseService()


def log_action(dressing: str, action_type: str, quantity: int):
    """Enregistre une action APRÈS décision de quota (donc déjà tronquée si besoin) --
    reflète le volume réellement envoyé à Vinted, pas la demande brute."""
    if quantity <= 0:
        return
    _supabase.db.table("automation_actions_log").insert({
        "dressing": dressing,
        "action_type": action_type,
        "quantity": quantity,
    }).execute()


def get_volume_last_hours(dressing: str, action_type: str, hours: int = 24) -> int:
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    res = (
        _supabase.db.table("automation_actions_log")
        .select("quantity")
        .eq("dressing", dressing)
        .eq("action_type", action_type)
        .gte("created_at", since)
        .execute()
    )
    return sum(r["quantity"] for r in res.data)


def get_volume_depuis_minuit(dressing: str, action_type: str) -> int:
    """
    Volume réellement loggé depuis MINUIT (heure locale) aujourd'hui -- DISTINCT
    de get_volume_last_hours(..., hours=24), qui est une fenêtre ROULANTE et
    inclut donc mécaniquement une partie de la veille. Nécessaire pour comparer
    à plan_du_jour["volume_cible"], qui est une cible CALENDAIRE (tirée à
    00:05, sensée repartir de zéro chaque jour) : la comparer à un cumul
    roulant sur 24h annule le nouveau tirage du jour dès qu'il y a eu de
    l'activité en fin de journée précédente -- bug constaté le 12/09/2026 (8
    articles faits la veille au soir bloquaient totalement les 8 nouveaux
    articles tirés le lendemain, alors qu'aucun n'avait encore été fait ce
    jour-là).
    """
    local_now = datetime.now()
    local_minuit = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    minuit_utc_equivalent = datetime.utcnow() - (local_now - local_minuit)
    res = (
        _supabase.db.table("automation_actions_log")
        .select("quantity")
        .eq("dressing", dressing)
        .eq("action_type", action_type)
        .gte("created_at", minuit_utc_equivalent.isoformat())
        .execute()
    )
    return sum(r["quantity"] for r in res.data)


def get_volume_glissant_combine(dressing: str, jours: int = JOURS_FENETRE_VOLUME) -> int:
    """Cumul RÉEL envoyé sur les `jours` derniers jours, republication ET
    baisse de prix ADDITIONNÉES -- contrairement à get_volume_last_hours, ne
    filtre pas par action_type : c'est le volume total qui compte pour ce
    dressing, peu importe le mélange entre les deux types d'action."""
    hours = jours * 24
    return (
        get_volume_last_hours(dressing, "republication", hours=hours)
        + get_volume_last_hours(dressing, "baisse_prix", hours=hours)
    )


def get_jour_pause_volume(dressing: str) -> bool:
    """
    Niveau 2 de la hiérarchie anti-détection (coupure sur volume glissant) --
    remplace l'ancien get_consecutive_active_days/get_jour_repos_force. Décision
    binaire : True si le cumul combiné (republication + baisse de prix) des
    JOURS_FENETRE_VOLUME derniers jours atteint déjà SEUIL_VOLUME_GLISSANT.
    """
    return get_volume_glissant_combine(dressing) >= SEUIL_VOLUME_GLISSANT


def report_suspension(dressing: str, note: str = None, detected_at: datetime = None):
    """
    À appeler manuellement dès qu'une suspension/limitation Vinted est constatée
    sur un compte. Déclenche une période de convalescence automatique.

    detected_at : date ET HEURE réelles de la restriction Vinted EN HEURE LOCALE
    (naturel pour un humain qui rapporte "vu vers 13h"), si connues -- le palier
    démarre à partir de ce moment, PAS de l'instant où ce signalement est
    enregistré en base (souvent constaté a posteriori, parfois plusieurs heures
    après le début réel de la restriction). None = maintenant.
    Convertie en UTC avant stockage (cf. échange du 08/10/2026 -- un premier
    appel avait stocké une heure locale telle quelle, alors que
    get_active_convalescence() la compare à datetime.utcnow() : le palier
    calculé partait "dans le futur", produisant un jours_ecoules négatif
    incohérent) -- jamais passer une heure déjà en UTC ici, sous peine de la
    décaler une deuxième fois.
    """
    payload = {"dressing": dressing, "note": note}
    if detected_at is not None:
        decalage_local_utc = datetime.now() - datetime.utcnow()
        payload["detected_at"] = (detected_at - decalage_local_utc).isoformat()
    _supabase.db.table("account_suspensions").insert(payload).execute()


def _parse_detected_at(valeur: str) -> datetime:
    return datetime.fromisoformat(valeur.replace("Z", "+00:00")).replace(tzinfo=None)


def _dernier_palier(dressing: str):
    """
    Calcule le palier de convalescence le plus récent pour ce dressing (rang de
    récidive + durée), qu'il soit encore actif ou déjà écoulé -- factorisé pour
    être réutilisé par get_active_convalescence() (palier en cours) ET
    get_ramp_post_convalescence() (fenêtre de ramp-up juste après la fin d'un
    palier). Retourne None si aucune suspension n'a jamais été signalée.
    """
    res = (
        _supabase.db.table("account_suspensions")
        .select("detected_at")
        .eq("dressing", dressing)
        .order("detected_at", desc=True)
        .execute()
    )
    toutes = res.data or []
    if not toutes:
        return None

    derniere_dt = _parse_detected_at(toutes[0]["detected_at"])
    fenetre_debut = derniere_dt - timedelta(days=FENETRE_RECIDIVE_JOURS)
    recentes = [s for s in toutes if _parse_detected_at(s["detected_at"]) >= fenetre_debut]
    rang = len(recentes)
    duree_palier_jours = PALIERS_CONVALESCENCE_JOURS.get(rang, DUREE_PALIER_MAX_JOURS)

    return {
        "derniere_dt": derniere_dt,
        "rang": rang,
        "duree_palier_jours": duree_palier_jours,
        "fin_palier": derniere_dt + timedelta(days=duree_palier_jours),
        "recentes": recentes,
    }


def get_ramp_post_convalescence(dressing: str):
    """
    Ramp-up progressif après la fin d'un palier de convalescence (cf.
    RAMP_DUREE_JOURS/RAMP_QUOTA_DEPART) -- retourne le quota plafond du jour
    (int) si le dressing est encore dans la fenêtre de ramp, None sinon (pas
    de convalescence récente, palier encore actif -- géré par
    get_active_convalescence -- ou ramp déjà terminé depuis longtemps).

    Rampe LINÉAIRE de RAMP_QUOTA_DEPART à `seuil_normal` sur RAMP_DUREE_JOURS
    jours pleins après la fin du palier (jour 1 = lendemain de la fin).
    """
    palier = _dernier_palier(dressing)
    if palier is None:
        return None

    now = datetime.utcnow()
    if now < palier["fin_palier"]:
        return None  # palier encore actif -- pas du ressort du ramp-up

    jours_depuis_fin = (now - palier["fin_palier"]).total_seconds() / 86400
    if jours_depuis_fin >= RAMP_DUREE_JOURS:
        return None  # ramp terminé, quota normal applicable

    def quota_pour_seuil(seuil_normal: int) -> int:
        progression = min(1.0, jours_depuis_fin / RAMP_DUREE_JOURS)
        return max(RAMP_QUOTA_DEPART, round(RAMP_QUOTA_DEPART + (seuil_normal - RAMP_QUOTA_DEPART) * progression))

    return quota_pour_seuil


def get_active_convalescence(dressing: str):
    """
    Retourne le détail de la convalescence en cours pour ce compte, ou None si
    aucun palier n'est actif (pas de suspension récente, ou palier déjà écoulé).

    Le palier appliqué dépend du RANG de récidive : nombre de suspensions de ce
    dressing dans les FENETRE_RECIDIVE_JOURS jours précédant (et incluant) la
    plus récente. Repos TOTAL (quota 0) pendant toute la durée du palier, à
    partir du jour ET DE L'HEURE réels de cette suspension (detected_at) --
    jours_ecoules compte des jours calendaires entiers, donc le palier se
    termine bien duree_palier_jours après l'heure réelle de la restriction,
    pas juste sa date.

    Retourne :
      {
        "rang": int,                        # 1ère, 2ème, 3ème récidive ou plus
        "duree_palier_jours": int,           # durée totale du palier appliqué
        "jours_ecoules": int,                # depuis la suspension la plus récente
        "jours_restants": int,               # avant la fin du palier
        "jour_actif": bool,                  # toujours False pendant un palier (repos total)
        "quota_du_jour": int,                # toujours 0 pendant un palier
        "ecart_jours_precedente": int|None,  # écart avec la récidive précédente (None si rang == 1)
      }
    """
    palier = _dernier_palier(dressing)
    if palier is None:
        return None

    derniere_dt = palier["derniere_dt"]
    rang = palier["rang"]
    recentes = palier["recentes"]
    duree_palier_jours = palier["duree_palier_jours"]
    duree_ecoulee = datetime.utcnow() - derniere_dt
    duree_palier = timedelta(days=duree_palier_jours)
    if duree_ecoulee >= duree_palier:
        return None  # Palier écoulé (jour ET heure), plus aucune restriction active

    # Arrondi au jour supérieur : tant qu'il reste ne serait-ce qu'une minute
    # de palier, on l'affiche comme "1 jour restant", jamais "0".
    jours_restants = math.ceil((duree_palier - duree_ecoulee).total_seconds() / 86400)

    ecart_jours_precedente = None
    if rang >= 2:
        precedente_dt = _parse_detected_at(recentes[1]["detected_at"])
        ecart_jours_precedente = (derniere_dt.date() - precedente_dt.date()).days

    return {
        "rang": rang,
        "duree_palier_jours": duree_palier_jours,
        "jours_ecoules": duree_ecoulee.days,
        "jours_restants": jours_restants,
        "jour_actif": False,
        "quota_du_jour": 0,
        "ecart_jours_precedente": ecart_jours_precedente,
    }


def _plan_continuite_actif(dressing: str) -> bool:
    """
    True si le plan du jour de ce dressing est un jour de CONTINUITÉ (niveau
    'continuite', cf. planification_republication._garantir_un_dressing_actif) :
    le Niveau 2 a été volontairement levé pour garantir qu'au moins un
    dressing republie chaque jour, avec un volume plafonné par le plan. Import
    différé pour éviter une dépendance circulaire au chargement du module.
    """
    from services.automation_scheduler import get_plan_du_jour

    plan = get_plan_du_jour(dressing)
    return (
        plan is not None
        and plan.get("niveau") == "continuite"
        and bool(plan.get("actif"))
        and plan.get("date") == datetime.now().strftime("%Y-%m-%d")
    )


def get_allowed_quantity(dressing: str, action_type: str, requested_quantity: int, ignorer_repos_niveau2: bool = False):
    """
    Retourne (allowed_qty, was_truncated, reason).
    allowed_qty : nombre d'items qu'on peut effectivement envoyer aujourd'hui.
    was_truncated : True si requested_quantity dépasse ce qui reste de quota.

    ignorer_repos_niveau2 : override MANUEL et délibéré du repos forcé Niveau 2
    (coupure sur volume glissant) -- test du 15/09/2026 pour vérifier si le
    rythme reste tenable avec la nouvelle version de l'extension Clemz. Ne
    touche JAMAIS à la convalescence (Niveau 1, suspension réellement
    constatée) : si convalescence est active, threshold reste plafonné à 0
    quoi qu'il arrive -- seul le repos statistique Niveau 2 peut être
    outrepassé, jamais un vrai signal de restriction Vinted. Le quota brut
    (DAILY_THRESHOLDS) continue lui aussi de s'appliquer normalement.
    """
    thresholds_for_type = DAILY_THRESHOLDS.get(action_type)
    if thresholds_for_type is None:
        return requested_quantity, False, None

    if action_type == "republication" and dressing in ("Dressing 1", "Dressing 2"):
        # Plafond tiré au hasard du jour (12-15) depuis le 02/10/2026 -- cf.
        # _plafond_republication_du_jour(). Le dict DAILY_THRESHOLDS["republication"]
        # ne sert plus que de fallback pour un dressing hors D1/D2.
        threshold = _plafond_republication_du_jour(dressing)
    else:
        threshold = thresholds_for_type.get(dressing, thresholds_for_type.get("_default"))
    if threshold is None:
        return requested_quantity, False, None

    convalescence = get_active_convalescence(dressing)
    if convalescence is not None:
        threshold = min(threshold, convalescence["quota_du_jour"])

    # Ramp-up progressif post-convalescence (cf. RAMP_DUREE_JOURS) -- réduit le
    # seuil normal les jours suivant la fin d'un palier au lieu d'un retour
    # instantané au plein régime. N'a de sens que si aucune convalescence n'est
    # active (sinon déjà à 0 ci-dessus) et qu'aucun repos Niveau 2 n'est en
    # cours (le repos statistique reste prioritaire, cf. bloc suivant).
    ramp_en_cours = False
    if convalescence is None:
        ramp_fn = get_ramp_post_convalescence(dressing)
        if ramp_fn is not None:
            quota_ramp = ramp_fn(threshold)
            if quota_ramp < threshold:
                threshold = quota_ramp
                ramp_en_cours = True

    forced_rest = False
    if (
        convalescence is None
        and get_jour_pause_volume(dressing)
        and not ignorer_repos_niveau2
        and not (action_type == "republication" and _plan_continuite_actif(dressing))
    ):
        threshold = 0
        forced_rest = True
        ramp_en_cours = False  # le repos total prime sur le ramp-up dans le message d'explication

    already_done = get_volume_last_hours(dressing, action_type, hours=24)
    remaining = max(0, threshold - already_done)
    allowed = min(requested_quantity, remaining)
    was_truncated = allowed < requested_quantity

    reason = None
    if was_truncated:
        if forced_rest:
            volume_cumule = get_volume_glissant_combine(dressing)
            reason = (
                f"Coupure totale pour {dressing} : {volume_cumule} article(s) cumulés "
                f"(republication + baisse de prix) sur les {JOURS_FENETRE_VOLUME} derniers jours "
                f"(seuil {SEUIL_VOLUME_GLISSANT}) -- pause imposée pour casser le pic de volume."
            )
        else:
            contexte_convalescence = ""
            if convalescence is not None:
                contexte_convalescence = (
                    f" (convalescence -- {convalescence['rang']}e récidive, "
                    f"palier {convalescence['duree_palier_jours']}j, "
                    f"{convalescence['jours_restants']}j restant(s))"
                )
            elif ramp_en_cours:
                contexte_convalescence = (
                    f" (ramp-up progressif post-convalescence -- quota réduit à {threshold} "
                    f"le temps de reprendre confiance, remontée sur {RAMP_DUREE_JOURS}j)"
                )
            reason = (
                f"Quota sécurité anti-détection atteint pour {action_type} sur {dressing}{contexte_convalescence} : "
                f"{already_done}/{threshold} déjà effectué(s) sur 24h glissantes. "
                f"{requested_quantity - allowed} article(s) reporté(s) au prochain cycle."
            )
    return allowed, was_truncated, reason


def get_quota_manuel_restant(dressing: str, action_type: str) -> int:
    """
    Nombre d'articles que le bouton MANUEL du dashboard pourrait encore
    envoyer aujourd'hui pour ce dressing/type d'action, tous niveaux
    anti-détection confondus -- quota brut (DAILY_THRESHOLDS), convalescence
    et coupure sur volume glissant via get_allowed_quantity(), PLUS pour la
    republication uniquement le plafonnement par le volume_cible du plan du
    jour (calendrier Niveau 3), exactement comme routes/inventory.py
    (_plafonner_par_plan_du_jour) l'applique réellement à l'envoi -- ici pour
    AFFICHAGE (barre d'état), cf. échange du 11/09/2026 : sans ce second
    plafond, le nombre affiché pour la republication aurait été le quota brut
    (ex: 20) au lieu du volume réellement tiré par le calendrier (ex: 11).

    baisse_prix n'a pas de plan_du_jour (jamais généré avec cet action_type),
    donc seul le quota brut + Niveaux 1/2 s'appliquent pour ce type.

    CORRIGÉ (11/09/2026) : si le plan en cache dit "repos" pour une raison
    Niveau 1 (convalescence) ou Niveau 2 (coupure volume) mais que `allowed`
    (recalculé EN DIRECT juste au-dessus) est déjà > 0, c'est que cette raison
    n'est plus vraie maintenant (palier arrivé à échéance en cours de journée,
    volume glissant retombé sous le seuil...) -- le plan mis en cache à 00:05
    est devenu obsolète, on ne l'affiche pas comme si le repos tenait encore
    jusqu'à minuit (incohérence constatée : baisse_prix, jamais mis en cache,
    redevenait dispo immédiatement, pas la republication). Le repos Niveau 3
    (calendrier, tirage indépendant du quota) reste lui pleinement valable.
    """
    allowed, _, _ = get_allowed_quantity(dressing, action_type, 9999)
    if action_type != "republication" or allowed <= 0:
        return allowed

    from services.automation_scheduler import get_plan_du_jour
    from services.planification_republication import rafraichir_repos_si_leve

    rafraichir_repos_si_leve(dressing, action_type)
    plan = get_plan_du_jour(dressing)
    if plan is None or plan.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return allowed
    if not plan.get("actif"):
        if plan.get("niveau") in ("convalescence", "repos_force"):
            return allowed  # repos Niveau 1/2 mis en cache, mais plus vrai live -- plan obsolète
        return 0  # Niveau 3 (calendrier) : vraie décision de repos du jour, indépendante du quota

    deja_fait = get_volume_depuis_minuit(dressing, "republication")
    return min(allowed, max(0, plan["volume_cible"] - deja_fait))


def get_status_summary(dressing: str):
    """
    Pour affichage dashboard (Maintenance, LED anti-détection ou autre) : volume
    actuel vs seuil EFFECTIF (déjà réduit par la convalescence en cours le cas
    échéant, comme get_allowed_quantity), par type d'action -- plus "restant_manuel"
    (ce que le bouton manuel autoriserait réellement là, maintenant, cf.
    get_quota_manuel_restant), le détail de la convalescence elle-même sous la
    clé "convalescence" (None si aucune), et le plan du jour (niveau ayant
    tranché, actif/pause, horaire/volume prévus) sous la clé "plan_du_jour"
    (None si pas encore généré aujourd'hui).

    Import différé de automation_scheduler pour éviter tout risque de
    dépendance circulaire au chargement du module (risk_guard est un service
    "bas niveau" consulté par beaucoup d'autres, dont potentiellement
    automation_scheduler à l'avenir).
    """
    from services.automation_scheduler import get_plan_du_jour

    convalescence = get_active_convalescence(dressing)
    summary = {}
    for action_type, thresholds_for_type in DAILY_THRESHOLDS.items():
        threshold = thresholds_for_type.get(dressing, thresholds_for_type.get("_default"))
        if convalescence is not None:
            threshold = min(threshold, convalescence["quota_du_jour"])
        summary[action_type] = {
            "used": get_volume_last_hours(dressing, action_type, hours=24),
            "threshold": threshold,
            "restant_manuel": get_quota_manuel_restant(dressing, action_type),
        }
    summary["convalescence"] = convalescence
    summary["plan_du_jour"] = get_plan_du_jour(dressing)
    return summary