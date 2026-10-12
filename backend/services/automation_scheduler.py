import json
import os
import random
from datetime import datetime
from threading import Lock

_file_lock = Lock()

# Pointe vers backend/ (un niveau au-dessus de services/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_HISTORY_FILE = os.path.join(BASE_DIR, "task_history.json")
VM_LIFECYCLE_FILE = os.path.join(BASE_DIR, "vm_lifecycle_log.json")
MAX_VM_LIFECYCLE_ENTRIES = 30

# Plan du jour (hiérarchie convalescence > repos forcé > calendrier aléatoire),
# recalculé chaque jour à minuit par planification_republication.py -- un
# fichier par dressing, indépendants l'un de l'autre.
PLAN_DU_JOUR_FILES = {
    "Dressing 1": os.path.join(BASE_DIR, "plan_du_jour_d1.json"),
    "Dressing 2": os.path.join(BASE_DIR, "plan_du_jour_d2.json"),
}
# Historique append-only de toutes les décisions passées (cf. _archiver_plan_du_jour) --
# distinct des fichiers ci-dessus, qui n'exposent que le plan du jour courant.
PLAN_DU_JOUR_HISTORIQUE_FILE = os.path.join(BASE_DIR, "plan_du_jour_historique.jsonl")

MAX_HISTORY_ENTRIES = 200

# État de la routine du jour par dressing (cf. échange du 12/10/2026) : une
# chaîne scraping -> baisse_prix -> republication PAR dressing, chaque étape
# devant réussir pour débloquer la suivante. Remis à zéro au changement de date.
ROUTINE_DU_JOUR_FILE = os.path.join(BASE_DIR, "routine_du_jour.json")
# "succes" : au moins un article traité (succès partiel = OK) ; "rien_a_faire" :
# aucun article concerné ; "desactive" : bascule Maintenance coupée ; "repos" :
# dressing au repos aujourd'hui selon son plan du jour (aucune action Clemz) ;
# "arrete" : arrêtée à la demande de l'utilisateur (bouton Arrêter du Journal,
# décision définitive pour la journée) -- débloquent l'étape suivante.
# "echec"/"bloque"/"interrompue" (coupée par un redémarrage) l'arrêtent et
# attendent "Reprendre la routine".
STATUTS_ETAPE_OK = ("succes", "rien_a_faire", "desactive", "repos", "arrete")
STATUTS_ETAPE_A_REPRENDRE = ("echec", "bloque", "interrompue")

# Pause globale de la routine (bouton du Journal, 12/10/2026) -- {"mode":
# "jour"|"indefini", "date": "AAAA-MM-JJ", "depuis": iso}. Mode "jour" : levée
# d'elle-même au changement de date.
ROUTINE_PAUSE_FILE = os.path.join(BASE_DIR, "routine_pause.json")


def _read_json(file_path, default):
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ [SCHEDULER] Erreur lecture {os.path.basename(file_path)} : {e}")
        return default


def _write_json_locked(file_path, data):
    """
    Écrit SANS acquérir _file_lock — réservé aux appelants qui tiennent déjà le
    verrou pour tout le cycle lecture-modification-écriture. Ne jamais appeler
    directement en dehors d'un bloc `with _file_lock:` (Lock non réentrant :
    l'acquérir deux fois depuis le même thread ferait un deadlock).

    Écriture atomique : on écrit d'abord dans un fichier temporaire, puis on le
    renomme à la place du fichier final via os.replace(). Sur la plupart des
    OS (Windows inclus depuis Python 3.3+), ce renommage est atomique -- un
    lecteur concurrent (ex: route GET /automation/history) voit soit
    l'ancienne version complète du fichier, soit la nouvelle version complète,
    jamais un JSON tronqué en cas de crash/coupure pendant l'écriture.
    """
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def _write_json(file_path, data):
    """Écriture autonome (acquiert elle-même le verrou) — pour les cas simples
    où lecture et écriture ne partagent pas le même verrou (ex: plan du jour,
    où chaque fonction publique fait sa propre passe)."""
    with _file_lock:
        _write_json_locked(file_path, data)


DATE_DEBUT_HISTORIQUE = "2026-10-12"


def purger_historique_anterieur():
    """
    Purge unique demandée le 12/10/2026, appelée au démarrage : efface de
    task_history.json (page "Historique des automatisations", Journal des
    jours passés) toutes les tâches démarrées avant DATE_DEBUT_HISTORIQUE --
    l'historique d'avant la refonte de la routine mélangeait anciens types et
    statuts. Ne touche à AUCUNE donnée de calcul (quota anti-détection
    Supabase, plans du jour, price_drops). Idempotente ; à supprimer avec
    migrer_anciennes_republications une fois la VM redémarrée.
    """
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        conservees = [h for h in history if (h.get("started_at") or "") >= DATE_DEBUT_HISTORIQUE]
        nb = len(history) - len(conservees)
        if nb:
            _write_json_locked(TASK_HISTORY_FILE, conservees)
    return nb


def migrer_anciennes_republications():
    """
    Migration unique (12/10/2026), appelée au démarrage : l'ancien système de
    créneaux fixes enregistrait les republications automatiques sous les types
    "republication_midi" (Dressing 1) / "republication_soir" (Dressing 2) et
    gardait une liste "programmée" dans scheduled_republish_*.json. Désormais :
    type "republication", sous-type "routine", et plus aucune programmation
    manuelle. Idempotente -- ne fait plus rien une fois l'historique converti ;
    à supprimer quand la VM et le PC de dev ont tous deux redémarré une fois.
    """
    anciens_types = ("republication_midi", "republication_soir")
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        nb = 0
        for entry in history:
            if entry.get("type") in anciens_types:
                entry["type"] = "republication"
                entry["sous_type"] = "routine"
                nb += 1
        if nb:
            _write_json_locked(TASK_HISTORY_FILE, history)
    for nom in ("scheduled_republish_midi.json", "scheduled_republish_soir.json"):
        chemin = os.path.join(BASE_DIR, nom)
        if os.path.exists(chemin):
            os.remove(chemin)
    return nb


def get_routine_du_jour():
    """{"date": "AAAA-MM-JJ", "Dressing 1": {etape: {...}}, "Dressing 2": {...}}
    -- vide (mais daté du jour) si rien n'a encore tourné aujourd'hui."""
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    etat = _read_json(ROUTINE_DU_JOUR_FILE, default={})
    if etat.get("date") != aujourdhui:
        return {"date": aujourdhui, "Dressing 1": {}, "Dressing 2": {}}
    return etat


def get_statut_etape(dressing, etape):
    """Statut du jour d'une étape pour ce dressing, ou None si pas encore passée."""
    return (get_routine_du_jour().get(dressing) or {}).get(etape, {}).get("statut")


def get_pause_routine():
    """La pause en vigueur ({"mode", "date", "depuis"}) ou None."""
    pause = _read_json(ROUTINE_PAUSE_FILE, default=None)
    if not pause:
        return None
    if pause.get("mode") == "jour" and pause.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return None
    return pause


def set_pause_routine(mode):
    """mode : "jour" (jusqu'à minuit), "indefini" (jusqu'à reprise), None (levée)."""
    if mode is None:
        if os.path.exists(ROUTINE_PAUSE_FILE):
            os.remove(ROUTINE_PAUSE_FILE)
        return None
    pause = {"mode": mode, "date": datetime.now().strftime("%Y-%m-%d"), "depuis": datetime.now().isoformat()}
    _write_json(ROUTINE_PAUSE_FILE, pause)
    return pause


def noter_etape_routine(dressing, etape, statut, raison=None):
    with _file_lock:
        etat = get_routine_du_jour()
        etat.setdefault(dressing, {})[etape] = {
            "statut": statut,
            "heure": datetime.now().isoformat(),
            "raison": raison,
        }
        _write_json_locked(ROUTINE_DU_JOUR_FILE, etat)


def save_plan_du_jour(dressing, plan):
    """Persiste le plan du jour généré par planification_republication.generer_plan_du_jour()
    pour ce dressing. Écrase silencieusement tout plan précédent (un seul plan
    actif par dressing à la fois, celui du jour courant)."""
    from services.plan_source import est_satellite
    if est_satellite():
        # Filet de sécurité : le satellite (PC de dev) ne persiste jamais de
        # plan -- la VM est la seule source de vérité (cf. plan_source.py).
        print(f"⏭️  [PLAN DU JOUR] Satellite : plan {dressing} non enregistré (la VM fait foi).")
        return
    file = PLAN_DU_JOUR_FILES.get(dressing)
    if not file:
        print(f"⚠️ [SCHEDULER] Dressing inconnu pour le plan du jour : {dressing}")
        return
    _write_json(file, plan)
    _archiver_plan_du_jour(dressing, plan)


def _archiver_plan_du_jour(dressing, plan):
    """
    Historise CHAQUE décision persistée (append-only, JSONL) -- contrairement
    au fichier ci-dessus, écrasé à chaque appel, celui-ci garde la trace de
    toutes les décisions passées, y compris plusieurs par jour si le plan est
    corrigé en cours de journée (ex: rafraichir_pause_si_necessaire). Sans ça,
    aucune nouvelle règle de décision (ex: rotation équitable entre dressings
    évoquée le 21/09/2026) ne peut être validée "par le calcul sur des
    exemples réels" comme convenu -- il n'existait jusqu'ici aucun historique
    des niveaux passés, seulement l'état du jour courant.
    """
    entree = {**plan, "archived_at": datetime.now().isoformat()}
    with _file_lock:
        try:
            with open(PLAN_DU_JOUR_HISTORIQUE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entree, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"⚠️ [SCHEDULER] Échec archivage historique plan du jour ({dressing}) : {e}")


def get_plan_du_jour(dressing):
    """
    Plan du jour courant pour ce dressing (niveau ayant tranché, actif/pause,
    horaire et volume prévus si actif) -- cf. save_plan_du_jour(). Retourne
    None si aucun plan n'a encore été généré, OU si le plan stocké date d'un
    jour précédent (ex: redémarrage backend après minuit sans catch-up) --
    mieux vaut ne rien afficher qu'un plan périmé.

    Sur le satellite (PLAN_SOURCE_URL défini, cf. plan_source.py) : plan de
    la VM, jamais le fichier local -- toujours un dict (plan de repos
    synthétique si la VM est injoignable).
    """
    from services.plan_source import est_satellite, get_plan_distant
    if est_satellite():
        return get_plan_distant(dressing)

    file = PLAN_DU_JOUR_FILES.get(dressing)
    if not file:
        return None
    plan = _read_json(file, default=None)
    if not plan or plan.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return None
    return plan


def start_partage_vues_favoris_run():
    """
    Démarre une entrée d'historique pour la tâche 'partage_vues_favoris'.
    Contrairement à start_task_run (republication/baisse de prix, par produit),
    cette tâche travaille par COMPTE (Chrome/Edge) : les 'results' contiennent un
    statut vues + favoris par compte, pas une liste de produits.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    timestamp = datetime.now().strftime("%H%M%S")
    task_id = f"partage_vues_favoris_{today_str}_{timestamp}"

    entry = {
        "task_id": task_id,
        "type": "partage_vues_favoris",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "global_status": "running",
        "results": [],
    }

    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        history.insert(0, entry)
        history = history[:MAX_HISTORY_ENTRIES]
        _write_json_locked(TASK_HISTORY_FILE, history)
    return task_id


def finish_partage_vues_favoris_run(task_id, account_results):
    """
    Clôture l'entrée d'historique de la tâche 'partage_vues_favoris'.
    account_results : liste de {"account": str, "vues": {...}, "favoris": {...}},
    chaque sous-dict ayant au moins une clé "status" ("success"/"skipped"/"failed").
    global_status : "success" si tout est success/skipped, "failed" si tout a
    échoué, "partial_success" sinon.

    Idempotent : un second appel pour un task_id déjà clôturé (retry après
    timeout réseau côté appelant, double déclenchement) est ignoré plutôt que
    d'écraser un résultat déjà finalisé.
    """
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        for entry in history:
            if entry["task_id"] != task_id:
                continue

            if entry["global_status"] != "running":
                print(f"⚠️ [SCHEDULER] finish_partage_vues_favoris_run rappelé pour "
                      f"une tâche déjà terminée (task_id={task_id}, "
                      f"status={entry['global_status']}) — ignoré.")
                break

            entry["results"] = account_results

            statuses = []
            for acc_result in account_results:
                statuses.append(acc_result.get("vues", {}).get("status"))
                statuses.append(acc_result.get("favoris", {}).get("status"))

            ok_statuses = {"success", "skipped"}
            if all(s in ok_statuses for s in statuses):
                global_status = "success"
            elif all(s == "failed" for s in statuses):
                global_status = "failed"
            else:
                global_status = "partial_success"

            entry["global_status"] = global_status
            entry["finished_at"] = datetime.now().isoformat()
            break

        _write_json_locked(TASK_HISTORY_FILE, history)
        return history[0] if history else None


def get_task_history(limit=50, task_type=None):
    history = _read_json(TASK_HISTORY_FILE, default=[])
    if task_type:
        history = [h for h in history if h.get("type") == task_type]
    return history[:limit]


def cleanup_stale_running_tasks():
    """
    À appeler UNE SEULE FOIS au démarrage du backend (main.py, événement
    startup). Un redémarrage du process (crash, arrêt manuel, VS Code qui tue
    le terminal intégré...) ne peut jamais laisser une vraie tâche "en cours" --
    aucun asyncio.Task ne survit à la fin du process Python qui l'exécutait.
    Toute entrée encore à "running" au démarrage est donc forcément une tâche
    interrompue brutalement lors du run précédent, jamais clôturée proprement
    par finish_task_run(). On la marque "failed" pour ne plus l'afficher comme
    "En cours" indéfiniment dans TaskHistory.

    Retourne le nombre d'entrées nettoyées.
    """
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        nb_cleaned = 0
        for entry in history:
            if entry.get("global_status") != "running":
                continue
            entry["global_status"] = "failed"
            entry["finished_at"] = datetime.now().isoformat()
            # Raison visible même sans résultat par article (ex. partage
            # vues/favoris, dont les résultats ne sont écrits qu'à la fin --
            # sinon "Échec 0/0" sans explication, cf. 12/10/2026).
            entry["interruption"] = "Interrompue par un redémarrage du serveur."
            for result in entry.get("results", []):
                if result.get("status") in ("pending", "running"):
                    result["status"] = "failed"
                    result["reason"] = "Tâche interrompue par un redémarrage du serveur."
            nb_cleaned += 1
        if nb_cleaned:
            _write_json_locked(TASK_HISTORY_FILE, history)
    return nb_cleaned


def start_task_run(task_type, items, sous_type=None):
    """
    sous_type : précision affichée sous le badge de type dans TaskHistory.jsx
    (ex: "mauvaise_performance" pour une baisse_prix issue du cron Mauvaise
    Performance) -- cf. échange du 08/10/2026, purement un libellé d'affichage,
    aucune incidence sur la logique de sélection/exécution. None = pas affiché.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    timestamp = datetime.now().strftime("%H%M%S")
    task_id = f"{task_type}_{today_str}_{timestamp}"

    entry = {
        "task_id": task_id,
        "type": task_type,
        "sous_type": sous_type,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "global_status": "running",
        "results": [
            {
                "id": item.get("id"),
                "nom": item.get("nom"),
                "dressing": item.get("dressing"),
                "taux": item.get("taux"),  # sous-groupe -10/-20 (baisse_prix uniquement, None sinon)
                "status": "pending"
            }
            for item in items
        ],
    }

    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        history.insert(0, entry)
        history = history[:MAX_HISTORY_ENTRIES]
        _write_json_locked(TASK_HISTORY_FILE, history)
    return task_id


def update_task_result(task_id, item_id, status, reason=None):
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        for entry in history:
            if entry["task_id"] != task_id:
                continue
            for result in entry["results"]:
                if str(result["id"]) == str(item_id):
                    result["status"] = status
                    if reason:
                        result["reason"] = reason
                    break
            break
        _write_json_locked(TASK_HISTORY_FILE, history)


def update_task_result_par_nom(task_id, nom, status, reason=None):
    """Comme update_task_result, mais par nom d'article (insensible à la casse)
    -- pour l'enregistrement au fil de l'eau, où seul le nom affiché par Clemz
    est connu. Sans effet si l'article n'est pas dans la tâche (ex. reste d'un
    run précédent dans la liste Clemz)."""
    cible = (nom or "").strip().lower()
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        for entry in history:
            if entry["task_id"] != task_id:
                continue
            for result in entry["results"]:
                if (result.get("nom") or "").strip().lower() == cible:
                    result["status"] = status
                    if reason:
                        result["reason"] = reason
                    _write_json_locked(TASK_HISTORY_FILE, history)
                    return
            return


def marquer_etapes_interrompues():
    """
    À appeler au démarrage, AVANT cleanup_stale_running_tasks() (12/10/2026) :
    une baisse de prix ou une republication de la routine encore "en cours"
    a été coupée par l'arrêt du serveur. L'étape est notée "interrompue" pour
    chaque dressing concerné -- elle ne sera PAS relancée automatiquement
    (plus de relance surprise au redémarrage), seulement via "Reprendre la
    routine" dans le Journal. Le scraping (lecture seule) n'est pas concerné :
    resté sans statut, il est simplement refait.
    """
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    heure = datetime.now().strftime("%H:%M")
    for entry in _read_json(TASK_HISTORY_FILE, default=[]):
        if entry.get("global_status") != "running" or not (entry.get("started_at") or "").startswith(aujourdhui):
            continue
        if entry.get("type") == "baisse_prix":
            etape = "baisse_prix"
        elif entry.get("type") == "republication" and entry.get("sous_type") == "routine":
            etape = "republication"
        else:
            continue
        dressings = {r.get("dressing") for r in entry.get("results", [])} & {"Dressing 1", "Dressing 2"}
        for dressing in dressings:
            noter_etape_routine(
                dressing, etape, "interrompue",
                f"Coupée par un redémarrage du serveur (détecté à {heure}) — à reprendre depuis le Journal.",
            )


def fail_pending_results(task_id, reason):
    """
    Marque en échec UNIQUEMENT les résultats encore "pending"/"running" d'une tâche.
    Utilisé quand une exception interrompt le run-now en cours de route : les items
    déjà résolus (success/skipped/failed avec un vrai motif) doivent rester intacts,
    sinon une simple erreur de bookkeeping fait passer pour échoués des articles bel
    et bien republiés dans Vinted (cf. échange du 20/09/2026, WinError 10035).
    """
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        for entry in history:
            if entry["task_id"] != task_id:
                continue
            for result in entry["results"]:
                if result.get("status") in ("pending", "running"):
                    result["status"] = "failed"
                    result["reason"] = reason
            break
        _write_json_locked(TASK_HISTORY_FILE, history)


def add_task_global_anomaly(task_id, label, dressing, reason, category="erreur"):
    """
    Ajoute une entrée d'anomalie GLOBALE (pas liée à un produit précis) dans les
    résultats d'une tâche déjà démarrée — ex. échec de navigation 'Voir mes listes'
    après une constitution de liste par ailleurs réussie. Réutilise directement
    l'affichage existant de TaskHistory.jsx (items avec id/nom/dressing/status/reason),
    sans modification du frontend.

    category : "quota" (blocage risk_guard, pas une vraie erreur Clemz) ou
    "erreur_clemz" (échec Clemz réel) -- affiché avec un badge distinct dans
    TaskHistory.jsx (cf. échange du 08/10/2026). "status" reste "failed" dans
    les deux cas pour ne pas changer le calcul de global_status existant.
    """
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        for entry in history:
            if entry["task_id"] != task_id:
                continue
            entry["results"].append({
                "id": f"anomaly_{datetime.now().strftime('%H%M%S')}",
                "nom": f"⚠️ {label}",
                "dressing": dressing,
                "status": "failed",
                "category": category,
                "reason": reason,
            })
            break
        _write_json_locked(TASK_HISTORY_FILE, history)


def finish_task_run(task_id):
    """
    Idempotent : un second appel pour un task_id déjà clôturé (retry après
    timeout côté appelant, double déclenchement) est ignoré plutôt que
    d'écraser un résultat déjà finalisé.
    """
    with _file_lock:
        history = _read_json(TASK_HISTORY_FILE, default=[])
        for entry in history:
            if entry["task_id"] != task_id:
                continue

            if entry["global_status"] != "running":
                print(f"⚠️ [SCHEDULER] finish_task_run rappelé pour une tâche déjà "
                      f"terminée (task_id={task_id}, status={entry['global_status']}) — ignoré.")
                break

            statuses = [r["status"] for r in entry["results"]]
            if all(s == "success" for s in statuses):
                global_status = "success"
            elif all(s == "failed" for s in statuses):
                global_status = "failed"
            else:
                global_status = "partial_success"

            entry["global_status"] = global_status
            entry["finished_at"] = datetime.now().isoformat()
            break

        _write_json_locked(TASK_HISTORY_FILE, history)
        return history[0] if history else None


def log_vm_lifecycle_event(event, status, detail=None):
    """
    Journalise un événement de cycle de vie du VM (démarrage/extinction) --
    distinct de task_history (qui suit des tâches d'automatisation, pas le
    process backend lui-même). Ajouté le 08/10/2026 : jusqu'ici, un échec au
    démarrage (ex: crash import) ou à l'extinction n'était visible que dans
    des logs fichier (logs/demarrage.log, console), jamais dans le dashboard.

    event : "demarrage" | "extinction"
    status : "succes" | "echec"
    detail : message libre (ex: raison d'un échec), optionnel.
    """
    entry = {
        "event": event,
        "status": status,
        "detail": detail,
        "timestamp": datetime.now().isoformat(),
    }
    with _file_lock:
        log = _read_json(VM_LIFECYCLE_FILE, default=[])
        log.insert(0, entry)
        log = log[:MAX_VM_LIFECYCLE_ENTRIES]
        _write_json_locked(VM_LIFECYCLE_FILE, log)
    return entry


def get_vm_lifecycle_log(limit=14):
    log = _read_json(VM_LIFECYCLE_FILE, default=[])
    return log[:limit]