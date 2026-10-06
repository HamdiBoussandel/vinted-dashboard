"""
Traite en chaîne TOUS les lots prêts (status='generation_ok') : crée un
brouillon Vinted par lot, avec une pause aléatoire de 2 à 5 min entre chaque
article pour ne pas créer plusieurs brouillons en rafale. Contrairement au
diagnostic, sauvegarde automatiquement chaque brouillon sans confirmation
manuelle -- le contrôle qualité se fait après coup, sur les brouillons déjà
en base (jamais publiés directement, cf. la règle "Sauvegarder le brouillon"
uniquement, jamais "Ajouter").

⚠️ MIGRÉ (08/09/2026) : utilise un vrai Chrome (channel="chrome"), PARTAGÉ avec
l'automatisation Clemz -- Dressing 3 (brouillons, Brave) a fusionné dans
Dressing 1, même compte Vinted. DEPUIS le 05/10/2026, le dressing est choisi
PAR LOT (sélecteur dans Préparer Annonces, défaut Dressing 1) plutôt que
toujours Dressing 1 : les lots sont regroupés par dressing et traités
dressing par dressing, jamais les deux en même temps (règle anti-détection).
Avant d'ouvrir le profil Chrome d'un dressing, on attend que son verrou de
profil soit libre (aucune automatisation Clemz -- republication/baisse de
prix/partage -- en cours sur ce même dressing), au lieu de risquer un conflit
d'accès.

Usage : python vinted_draft_creation_worker.py
"""
import asyncio
import os
import random
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from database import supabase
from services.automation_scheduler import start_task_run, update_task_result, finish_task_run
from services.vinted_draft_filler import (
    URL_NOUVELLE_ANNONCE,
    pause,
    clic_humain,
    remplir_formulaire_complet,
    forcer_fenetre_normale,
    fermer_modale_feedback_si_presente,
    fermer_modale_authenticite_si_presente,
)

_BASE = os.path.dirname(os.path.abspath(__file__))

# Un profil Chrome réel par dressing (même account_key que clemz_auto_message.ACCOUNTS,
# pour partager le même verrou de profil que l'automatisation Clemz).
SESSIONS = {
    "Dressing 1": {
        "path": os.path.join(_BASE, "clemz_session_chrome_d1_reel"),
        "account_key": "chrome_clemz",
    },
    "Dressing 2": {
        "path": os.path.join(_BASE, "clemz_session_chrome_reel"),
        "account_key": "edge_clemz",
    },
}
DRESSING_PAR_DEFAUT = "Dressing 1"

DELAI_ENTRE_LOTS_MIN_SECONDES = 120  # 2 min
DELAI_ENTRE_LOTS_MAX_SECONDES = 480  # 8 min

# Anti-corrélation entre deux dressings qui créent des brouillons l'un après
# l'autre dans le même lancement -- même logique que clemz_automation.py.
DELAI_ENTRE_DRESSINGS_MIN_SECONDES = 120  # 2 min
DELAI_ENTRE_DRESSINGS_MAX_SECONDES = 300  # 5 min

# Attente d'un profil occupé par une automatisation Clemz avant de renoncer
# (mêmes ordres de grandeur que /api/maintenance/preparer-arret).
VERROU_PROFIL_TIMEOUT_SECONDES = 15 * 60
VERROU_PROFIL_POLL_SECONDES = 15

# Plafond de brouillons créés en un seul lancement groupé (ex: 5 même si 20
# lots sont prêts) -- limite le volume d'actions automatisées d'un coup ;
# les lots restants repartent au prochain lancement (FIFO, cf. recuperer_lots_prets).
MAX_LOTS_PAR_LANCEMENT = 10

# Statut en mémoire, pour affichage de la progression sur le dashboard
# (Préparer Annonces) quand ce worker est déclenché depuis un bouton plutôt
# que lancé manuellement en terminal. Volontairement simple (pas de fichier/
# base) -- suffisant pour un run qui vit le temps d'une requête backend.
statut_worker = {
    "en_cours": False,
    "lot_actuel": 0,
    "total_lots": 0,
    "dernier_message": "",
}


def _formater_duree(secondes):
    """Formate une durée en 'XmYYs' (ex: 128s -> '2m08s') pour un affichage
    lisible du délai réellement tiré, plutôt qu'un arrondi à la minute près."""
    minutes = int(secondes // 60)
    reste = int(secondes % 60)
    return f"{minutes}m{reste:02d}s"


def recuperer_lots_prets():
    """
    Renvoie au plus MAX_LOTS_PAR_LANCEMENT lots 'generation_ok', les plus
    anciens d'abord (FIFO) -- s'il y en a plus de prêts, le surplus reste en
    base et sera repris au prochain lancement.
    """
    reponse = (
        supabase.table("brouillons_pending")
        .select("*")
        .eq("status", "generation_ok")
        .order("created_at")
        .limit(MAX_LOTS_PAR_LANCEMENT)
        .execute()
    )
    return reponse.data or []


def recuperer_lot_pret(lot_id):
    """Comme recuperer_lots_prets(), mais pour UN lot précis -- utilisé par la
    création de brouillon à l'unité (bouton sur une carte plutôt que le
    traitement en chaîne). Renvoie None si le lot n'existe pas ou n'est plus
    en 'generation_ok' (déjà traité, ou pas encore prêt)."""
    reponse = supabase.table("brouillons_pending").select("*").eq("id", lot_id).eq("status", "generation_ok").execute()
    data = reponse.data or []
    return data[0] if data else None


def _mettre_a_jour_lot(lot_id, status, error_message=None):
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    if error_message is not None:
        payload["error_message"] = error_message
    supabase.table("brouillons_pending").update(payload).eq("id", lot_id).execute()


def _titre_lot(lot):
    """Titre du lot pour affichage/historique -- repli si Gemini n'a pas
    (encore) produit de titre exploitable, plutôt qu'un KeyError."""
    try:
        return lot["gemini_output"]["raw"]["titre"]
    except Exception:
        return f"Lot {lot['id']}"


async def traiter_un_lot(page, lot):
    print(f"\n📦 Lot {lot['id']} — {_titre_lot(lot)}")
    print(f"   {len(lot['photos'])} photo(s)")

    _mettre_a_jour_lot(lot["id"], "creating_draft")

    await page.goto(URL_NOUVELLE_ANNONCE, wait_until="domcontentloaded")
    await pause(1.5, 2.5)

    await remplir_formulaire_complet(page, lot)
    await fermer_modale_authenticite_si_presente(page)

    bouton = page.locator('[data-testid="upload-form-save-draft-button"]')
    await clic_humain(page, bouton)
    await pause(1.5, 2.5)
    await fermer_modale_feedback_si_presente(page)

    _mettre_a_jour_lot(lot["id"], "draft_created")
    print(f"✅ Brouillon sauvegardé pour le lot {lot['id']}.")


async def _attendre_profil_libre(account_key, nom_dressing):
    """Attend qu'aucune automatisation Clemz (republication/baisse de prix/
    partage) ne tienne le verrou de profil du dressing visé. Retourne True si
    libre, False si toujours occupé après VERROU_PROFIL_TIMEOUT_SECONDES."""
    from services.clemz_auto_message import get_profile_lock

    lock = get_profile_lock(account_key)
    elapsed = 0
    while lock.locked():
        if elapsed >= VERROU_PROFIL_TIMEOUT_SECONDES:
            return False
        if elapsed == 0:
            print(f"⏳ Profil {nom_dressing} occupé par une automatisation Clemz en cours -- attente...")
            statut_worker["dernier_message"] = f"Profil {nom_dressing} occupé -- attente..."
        await asyncio.sleep(VERROU_PROFIL_POLL_SECONDES)
        elapsed += VERROU_PROFIL_POLL_SECONDES
    return True


async def _traiter_groupe_dressing(nom_dressing, lots, task_id):
    """Traite TOUS les lots d'un même dressing dans un unique contexte
    navigateur (son profil Chrome dédié), sous le même verrou de profil que
    l'automatisation Clemz pour exclure tout accès concurrent."""
    from services.clemz_auto_message import get_profile_lock

    session = SESSIONS.get(nom_dressing, SESSIONS[DRESSING_PAR_DEFAUT])
    lock = get_profile_lock(session["account_key"])

    if not await _attendre_profil_libre(session["account_key"], nom_dressing):
        msg = f"Profil {nom_dressing} occupé par une automatisation Clemz en cours (timeout {VERROU_PROFIL_TIMEOUT_SECONDES}s)."
        print(f"🚨 {msg}")
        for lot in lots:
            _mettre_a_jour_lot(lot["id"], "draft_error", error_message=msg)
            update_task_result(task_id, lot["id"], status="failed", reason=msg)
        return

    await lock.acquire()
    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                session["path"],
                channel="chrome",
                headless=False,
                no_viewport=True,
                ignore_default_args=[
                    "--disable-extensions",
                    "--disable-component-extensions-with-background-pages",
                ],
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await forcer_fenetre_normale(context, page)

            for i, lot in enumerate(lots, start=1):
                statut_worker["lot_actuel"] += 1
                statut_worker["dernier_message"] = f"[{nom_dressing}] Traitement du lot {i}/{len(lots)}..."
                print(f"\n{'=' * 60}\n[{nom_dressing}] Lot {i}/{len(lots)}\n{'=' * 60}")
                try:
                    await traiter_un_lot(page, lot)
                    update_task_result(task_id, lot["id"], status="success")
                except Exception as e:
                    print(f"🚨 Erreur sur le lot {lot['id']} : {e}")
                    _mettre_a_jour_lot(lot["id"], "draft_error", error_message=str(e))
                    update_task_result(task_id, lot["id"], status="failed", reason=str(e)[:200])

                if i < len(lots):
                    delai = random.uniform(DELAI_ENTRE_LOTS_MIN_SECONDES, DELAI_ENTRE_LOTS_MAX_SECONDES)
                    statut_worker["dernier_message"] = f"Pause de {_formater_duree(delai)} avant le lot {i + 1}..."
                    print(f"⏸️  Pause de {_formater_duree(delai)} avant le prochain lot...")
                    await asyncio.sleep(delai)

            await context.close()
    finally:
        lock.release()


async def _traiter_lots(lots):
    """
    Cœur du traitement, partagé entre le lancement en chaîne (main()) et la
    création à l'unité (traiter_lot_par_id()) -- même statut_worker partagé
    (donc les deux modes se bloquent mutuellement), pour ne jamais avoir deux
    logiques de traitement divergentes à maintenir. Les lots sont regroupés
    par dressing et traités dressing par dressing, jamais simultanément (cf.
    échange du 05/10/2026 -- avant, tout passait toujours par Dressing 1).
    """
    statut_worker["en_cours"] = True
    statut_worker["total_lots"] = len(lots)
    statut_worker["lot_actuel"] = 0

    # Historique (TaskHistory du dashboard) -- absent jusqu'ici pour la création
    # de brouillons, qu'elle soit déclenchée via le bouton ou en lançant ce
    # script directement en terminal (cf. échange du 08/09/2026 : rattrapage
    # des runs passés fait à partir des timestamps déjà en base sur
    # brouillons_pending, ce wiring couvre les runs futurs).
    task_id = start_task_run("creation_brouillon", [
        {"id": lot["id"], "nom": _titre_lot(lot), "dressing": lot.get("dressing") or DRESSING_PAR_DEFAUT, "taux": None}
        for lot in lots
    ])

    groupes = {}
    for lot in lots:
        nom_dressing = lot.get("dressing") or DRESSING_PAR_DEFAUT
        groupes.setdefault(nom_dressing, []).append(lot)

    print(f"🧵 {len(lots)} lot(s) à traiter sur {len(groupes)} dressing(s), pause de "
          f"{DELAI_ENTRE_LOTS_MIN_SECONDES // 60}-{DELAI_ENTRE_LOTS_MAX_SECONDES // 60} min entre chaque lot.")

    try:
        noms_dressings = list(groupes.keys())
        for idx, nom_dressing in enumerate(noms_dressings):
            await _traiter_groupe_dressing(nom_dressing, groupes[nom_dressing], task_id)
            if idx < len(noms_dressings) - 1:
                delai = random.uniform(DELAI_ENTRE_DRESSINGS_MIN_SECONDES, DELAI_ENTRE_DRESSINGS_MAX_SECONDES)
                print(f"⏸️  Pause de {_formater_duree(delai)} avant de passer à {noms_dressings[idx + 1]}...")
                statut_worker["dernier_message"] = f"Pause de {_formater_duree(delai)} avant {noms_dressings[idx + 1]}..."
                await asyncio.sleep(delai)

        print("\n🏁 Traitement terminé.")
        statut_worker["dernier_message"] = "Traitement terminé."
    finally:
        statut_worker["en_cours"] = False
        finish_task_run(task_id)


async def main():
    if statut_worker["en_cours"]:
        print("⏭️  Un traitement est déjà en cours, nouveau lancement ignoré.")
        return

    lots = recuperer_lots_prets()
    if not lots:
        print("Aucun lot 'generation_ok' à traiter.")
        statut_worker["dernier_message"] = "Aucun lot à traiter."
        return

    if len(lots) == MAX_LOTS_PAR_LANCEMENT:
        print(f"ℹ️  Plafond de {MAX_LOTS_PAR_LANCEMENT} brouillon(s) par lancement atteint -- "
              f"relance après si d'autres lots sont prêts.")

    await _traiter_lots(lots)


async def traiter_lot_par_id(lot_id):
    """
    Crée le brouillon Vinted pour UN SEUL lot précis (bouton "Créer ce
    brouillon" sur une carte), plutôt que le traitement en chaîne de tous les
    lots 'generation_ok'. Ignoré si un traitement (groupé ou unitaire) est
    déjà en cours -- même statut_worker que main().
    """
    if statut_worker["en_cours"]:
        print("⏭️  Un traitement est déjà en cours, nouveau lancement ignoré.")
        return

    lot = recuperer_lot_pret(lot_id)
    if not lot:
        print(f"Lot {lot_id} introuvable ou plus en 'generation_ok'.")
        statut_worker["dernier_message"] = "Lot introuvable ou déjà traité."
        return

    await _traiter_lots([lot])


if __name__ == "__main__":
    asyncio.run(main())