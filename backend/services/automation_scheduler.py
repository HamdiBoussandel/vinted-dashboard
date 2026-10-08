import json
import os
import random
from datetime import datetime
from threading import Lock

_file_lock = Lock()

# Pointe vers backend/ (un niveau au-dessus de services/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULED_REPUBLISH_MIDI_FILE = os.path.join(BASE_DIR, "scheduled_republish_midi.json")
SCHEDULED_REPUBLISH_SOIR_FILE = os.path.join(BASE_DIR, "scheduled_republish_soir.json")
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
    lecteur concurrent (ex: route GET /automation/scheduled-both) voit soit
    l'ancienne version complète du fichier, soit la nouvelle version complète,
    jamais un JSON tronqué en cas de crash/coupure pendant l'écriture.
    """
    tmp_path = f"{file_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def _write_json(file_path, data):
    """Écriture autonome (acquiert elle-même le verrou) — pour les cas simples
    où lecture et écriture ne partagent pas le même verrou (ex: fichiers de
    créneaux midi/soir, où chaque fonction publique fait sa propre passe)."""
    with _file_lock:
        _write_json_locked(file_path, data)


def schedule_republish(items):
    """
    Répartit les articles par DRESSING, plus par créneau horaire mélangé :
    "midi" = Dressing 1 (fenêtre aléatoire ~11h30-12h30), "soir" = Dressing 2
    (fenêtre aléatoire ~17h00-18h00). Remplace le système précédent qui
    mélangeait les deux comptes dans un seul lot tiré à une heure aléatoire
    commune -- décision du 29/08 : republication jamais simultanée entre
    comptes, chacun sa propre fenêtre, jamais superposée avec l'autre.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    items_d1 = [i for i in items if i.get("dressing") == "Dressing 1"]
    items_d2 = [i for i in items if i.get("dressing") == "Dressing 2"]

    task_midi = {
        "date": today_str,
        "created_at": datetime.now().isoformat(),
        "creneau": "midi",
        "dressing": "Dressing 1",
        "window_start": "11:30",
        "window_end": "12:30",
        "scheduled_run_time": None,
        "status": "pending",
        "items": items_d1,
    }
    task_soir = {
        "date": today_str,
        "created_at": datetime.now().isoformat(),
        "creneau": "soir",
        "dressing": "Dressing 2",
        "window_start": "17:00",
        "window_end": "18:00",
        "scheduled_run_time": None,
        "status": "pending",
        "items": items_d2,
    }

    _write_json(SCHEDULED_REPUBLISH_MIDI_FILE, task_midi)
    _write_json(SCHEDULED_REPUBLISH_SOIR_FILE, task_soir)

    print(f"📋 [SCHEDULER] {len(items_d1)} article(s) Dressing 1 → fenêtre 11h30-12h30 | {len(items_d2)} article(s) Dressing 2 → fenêtre 17h00-18h00")
    return {"midi": task_midi, "soir": task_soir}


def get_scheduled_republish(creneau="soir"):
    file = SCHEDULED_REPUBLISH_MIDI_FILE if creneau == "midi" else SCHEDULED_REPUBLISH_SOIR_FILE
    return _read_json(file, default=None)


def get_both_scheduled():
    """Retourne les deux créneaux pour affichage dashboard."""
    return {
        "midi": _read_json(SCHEDULED_REPUBLISH_MIDI_FILE, default=None),
        "soir": _read_json(SCHEDULED_REPUBLISH_SOIR_FILE, default=None),
    }


def mark_scheduled_republish_status(status, creneau="soir"):
    task = get_scheduled_republish(creneau)
    if not task:
        return None
    task["status"] = status
    file = SCHEDULED_REPUBLISH_MIDI_FILE if creneau == "midi" else SCHEDULED_REPUBLISH_SOIR_FILE
    _write_json(file, task)
    return task


def update_scheduled_run_time(run_time_str, creneau="soir"):
    task = get_scheduled_republish(creneau)
    if not task:
        return None
    task["scheduled_run_time"] = run_time_str
    file = SCHEDULED_REPUBLISH_MIDI_FILE if creneau == "midi" else SCHEDULED_REPUBLISH_SOIR_FILE
    _write_json(file, task)
    return task


def save_plan_du_jour(dressing, plan):
    """Persiste le plan du jour généré par planification_republication.generer_plan_du_jour()
    pour ce dressing. Écrase silencieusement tout plan précédent (un seul plan
    actif par dressing à la fois, celui du jour courant)."""
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
    """
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