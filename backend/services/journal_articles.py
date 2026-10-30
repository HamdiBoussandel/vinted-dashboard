"""
Journal par article (28/10/2026, inspiré du "Journal" de Bleam) : table
Supabase article_evenements (script docs/sql/article_evenements.sql), une
ligne par événement de la vie d'un article, avec son origine et sa raison.

Alimentation SANS toucher aux scripts d'action : synchroniser_journal() relit
les sources existantes et n'insère que ce qui manque (clé source_ref unique,
rejouable sans doublon) :
  - price_drops    -> "baisse" (origine retrouvée par la tâche qui l'a faite)
  - task_history   -> "republication", "suppression", "echec", "erreur_clemz",
                      "ecarte" (avec le motif) -- fichier local plafonné à 200
                      tâches : appelée à chaque fin de tâche (finish_task_run)
                      pour ne plus rien perdre
  - ventes         -> "vente"
  - articles       -> "publication" (point de départ du journal)

Les succès de baisse viennent de price_drops (prix exacts) ; pour les tâches
de baisse, seuls les échecs et les articles écartés sont repris du fichier.
"""
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_verrou = threading.Lock()
_table_absente_signalee = False

# sous_type de tâche -> origine affichée
ORIGINES_REPUBLICATION = {
    "validation_manuelle": "validation_manuelle",
    "republication_manuelle": "republication_assistee",
    "auto_completion": "republication_assistee",
    "routine": "routine",
    None: "ancien_script",
}
ORIGINES_BAISSE = {
    "prix_boucher": "prix_boucher",
    "mauvaise_performance": "mauvaise_performance",
    None: "panier_manuel",
}
# Fenêtre de rattachement d'une ligne price_drops à la tâche qui l'a produite
MARGE_TACHE = timedelta(minutes=10)


def _dressing(valeur):
    """"Chrome - Dressing 1" / "Edge - Dressing 2" -> "Dressing 1" / "Dressing 2"."""
    valeur = valeur or ""
    for d in ("Dressing 1", "Dressing 2"):
        if d in valeur:
            return d
    return valeur or None


def _instant(valeur):
    """Date ISO (naïve = heure locale du PC, comme task_history) -> datetime avec fuseau."""
    if not valeur:
        return None
    d = datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()


def _euros(x):
    return f"{float(x):.2f}".replace(".", ",").replace(",00", "") + " €"


def _tout(db, table, colonnes):
    lignes, debut = [], 0
    while True:
        lot = db.table(table).select(colonnes).range(debut, debut + 999).execute().data or []
        lignes += lot
        if len(lot) < 1000:
            return lignes
        debut += 1000


def _evenements_taches(historique, ids_articles):
    evenements = []
    for tache in historique:
        if tache.get("global_status") == "running" or tache.get("type") not in ("republication", "baisse_prix", "suppression"):
            continue
        quand = _instant(tache.get("finished_at") or tache.get("started_at"))
        sous_type = tache.get("sous_type")
        for r in tache.get("results", []):
            article_id = r.get("id")
            if article_id not in ids_articles or r.get("status") == "pending":
                continue
            statut, raison = r.get("status"), r.get("reason")
            if tache["type"] == "baisse_prix":
                if statut == "success":
                    continue  # repris de price_drops, avec les prix exacts
                origine = ORIGINES_BAISSE.get(sous_type, sous_type)
            elif tache["type"] == "republication":
                origine = ORIGINES_REPUBLICATION.get(sous_type, sous_type)
            else:
                origine = sous_type or "suppression"

            if statut == "success":
                type_ = "republication" if tache["type"] == "republication" else "suppression"
            elif statut == "skipped":
                type_ = "ecarte"
            elif r.get("category") == "erreur_clemz" or "erreur clemz" in (raison or "").lower():
                type_ = "erreur_clemz"
            else:
                type_ = "echec"
            evenements.append({
                "article_id": article_id, "nom": r.get("nom") or "?", "dressing": _dressing(r.get("dressing")),
                "type": type_, "origine": origine, "detail": raison,
                "source": "task_history", "source_ref": f"task:{tache['task_id']}:{article_id}",
                "survenu_le": quand.isoformat(),
                # pour retrouver l'origine des baisses (non inséré)
                "_tache": tache, "_debut": _instant(tache.get("started_at")), "_fin": quand,
            })
    return evenements


def _origine_baisse(baisse, historique):
    """Tâche qui a produit cette ligne price_drops : une tâche de baisse
    contenant l'article dans sa fenêtre d'exécution, ou une republication
    (baisse à la republication) qui l'a republié au même moment."""
    instant = _instant(baisse["created_at"])
    for tache in historique:
        debut, fin = _instant(tache.get("started_at")), _instant(tache.get("finished_at"))
        if not debut or not (debut - MARGE_TACHE <= instant <= (fin or debut + timedelta(hours=3)) + MARGE_TACHE):
            continue
        if not any(r.get("id") == baisse["article_id"] for r in tache.get("results", [])):
            continue
        if tache.get("type") == "baisse_prix":
            return ORIGINES_BAISSE.get(tache.get("sous_type"), tache.get("sous_type"))
        if tache.get("type") == "liquidation":
            return "liquidation"
        if tache.get("type") == "republication":
            return "republication"
    return "inconnue"


def synchroniser_journal():
    """Insère dans article_evenements ce qui manque. Retourne le nombre de
    lignes envoyées, ou None si la table n'existe pas encore."""
    global _table_absente_signalee
    from database import supabase as db
    from services.automation_scheduler import get_task_history

    with _verrou:
        try:
            articles = _tout(db, "articles", "id, nom, dressing, date_publication, prix_vente")
            ids = {a["id"] for a in articles}
            historique = get_task_history(limit=1000)
            lignes = []

            for a in articles:
                if a.get("date_publication"):
                    lignes.append({
                        "article_id": a["id"], "nom": a["nom"], "dressing": _dressing(a.get("dressing")),
                        "type": "publication", "origine": None, "detail": "Première mise en ligne connue",
                        "source": "articles", "source_ref": f"publication:{a['id']}",
                        "survenu_le": _instant(a["date_publication"] + "T12:00:00").isoformat(),
                    })

            for b in _tout(db, "price_drops", "*"):
                avant, apres, pct = b.get("prix_avant"), b.get("prix_apres_estime"), b.get("pourcentage_applique")
                detail = f"−{pct:g} %" if pct is not None else "Baisse"
                if avant is not None and apres is not None:
                    detail += f" ({_euros(avant)} → {_euros(apres)})"
                lignes.append({
                    "article_id": b["article_id"] if b.get("article_id") in ids else None,
                    "nom": b.get("nom") or "?", "dressing": _dressing(b.get("dressing")),
                    "type": "baisse", "origine": _origine_baisse(b, historique),
                    "prix_avant": avant, "prix_apres": apres, "detail": detail,
                    "source": "price_drops", "source_ref": f"price_drops:{b['id']}",
                    "survenu_le": _instant(b["created_at"]).isoformat(),
                })

            for e in _evenements_taches(historique, ids):
                lignes.append({k: v for k, v in e.items() if not k.startswith("_")})

            for v in _tout(db, "ventes", "id, article_id, nom, dressing, prix_vente, pourcentage_reduction, "
                                        "date_vente, numero_commande"):
                if not v.get("date_vente"):
                    continue
                reduction = v.get("pourcentage_reduction") or 0
                lignes.append({
                    "article_id": v["article_id"] if v.get("article_id") in ids else None,
                    "nom": v.get("nom") or "?", "dressing": _dressing(v.get("dressing")),
                    "type": "vente", "origine": None, "prix_apres": v.get("prix_vente"),
                    "detail": f"Vendu {_euros(v['prix_vente'])}" + (f" (réduction {reduction:g} %)" if reduction else ""),
                    "source": "ventes", "source_ref": f"vente:{v.get('numero_commande') or v['id']}",
                    "survenu_le": _instant(v["date_vente"] + "T12:00:00").isoformat(),
                })

            for i in range(0, len(lignes), 500):
                db.table("article_evenements").upsert(lignes[i:i + 500], on_conflict="source_ref",
                                                      ignore_duplicates=True).execute()
            _table_absente_signalee = False
            logger.info(f"📜 [JOURNAL] Synchronisé : {len(lignes)} événement(s) vérifié(s).")
            return len(lignes)
        except Exception as e:
            if "article_evenements" in str(e) and ("does not exist" in str(e) or "PGRST205" in str(e)):
                if not _table_absente_signalee:
                    logger.warning("📜 [JOURNAL] Table article_evenements absente — exécute "
                                   "docs/sql/article_evenements.sql dans Supabase.")
                    _table_absente_signalee = True
                return None
            logger.error(f"❌ [JOURNAL] Synchronisation impossible : {e}")
            return None


def synchroniser_en_arriere_plan():
    """Appelée à chaque fin de tâche : ne bloque jamais l'appelant."""
    threading.Thread(target=synchroniser_journal, daemon=True, name="journal_articles").start()


def journal_article(article_id):
    """Événements d'un article (du plus récent au plus ancien) + évolution du
    prix, des vues et des favoris d'après les relevés de score."""
    from database import supabase as db
    evenements = db.table("article_evenements").select("*").eq("article_id", article_id) \
        .order("survenu_le", desc=True).execute().data or []
    releves = db.table("score_snapshots").select("captured_at, prix, vues, favoris") \
        .eq("article_id", article_id).order("captured_at").execute().data or []
    return {"evenements": evenements, "releves": releves}


# --- Prix changé hors VintedPro (28/10/2026, point 3 inspiré de Bleam) --------
# Le scraping compare le prix en base au prix lu sur Vinted. Un écart qu'aucune
# de nos baisses récentes n'explique (price_drops, prix d'après à 2 % près --
# règle testée sur l'historique du 13/08 au 13/10 : 68 changements sur 78
# expliqués) est noté "prix_change" : l'article sort alors des baisses
# automatiques jusqu'à ce qu'il soit remis sous stratégie ("reprise_strategie").

FENETRE_BAISSES_JOURS = 14


def _tolerance(prix):
    return max(0.10, 0.02 * prix)


def noter_changement_prix(article_id, nom, dressing, ancien, nouveau):
    """Appelée par le scraping quand le prix lu diffère du prix en base.
    Retourne True si le changement est attribué à une action hors VintedPro."""
    from database import supabase as db
    depuis = (datetime.utcnow() - timedelta(days=FENETRE_BAISSES_JOURS)).isoformat()
    baisses = db.table("price_drops").select("prix_apres_estime").eq("article_id", article_id) \
        .gte("created_at", depuis).execute().data or []
    if any(b.get("prix_apres_estime") is not None and abs(float(b["prix_apres_estime"]) - nouveau) <= _tolerance(nouveau)
           for b in baisses):
        return False  # une de nos baisses : déjà au journal via price_drops

    sens = "hausse" if nouveau > ancien else "baisse"
    maintenant = datetime.now().astimezone()
    db.table("article_evenements").upsert({
        "article_id": article_id, "nom": nom, "dressing": _dressing(dressing),
        "type": "prix_change", "origine": "manuel", "prix_avant": ancien, "prix_apres": nouveau,
        "detail": f"{sens.capitalize()} {_euros(ancien)} → {_euros(nouveau)} faite hors VintedPro : "
                  f"baisses automatiques suspendues pour cet article.",
        "source": "scraping", "source_ref": f"prix:{article_id}:{maintenant.strftime('%Y-%m-%dT%H:%M')}",
        "survenu_le": maintenant.isoformat(),
    }, on_conflict="source_ref", ignore_duplicates=True).execute()
    logger.warning(f"✏️ [JOURNAL] '{nom}' : prix changé hors VintedPro ({ancien} → {nouveau} €) — "
                   f"retiré des baisses automatiques.")
    return True


def articles_prix_manuel():
    """{article_id: événement prix_change} des articles dont le dernier
    événement de stratégie est un prix changé à la main (non remis sous
    stratégie depuis). {} si la table est inaccessible : on ne bloque rien."""
    from database import supabase as db
    try:
        lignes = db.table("article_evenements").select("article_id, type, survenu_le, prix_avant, prix_apres, detail") \
            .in_("type", ["prix_change", "reprise_strategie"]).order("survenu_le").execute().data or []
    except Exception as e:
        logger.warning(f"⚠️ [JOURNAL] Prix manuels illisibles ({e}) — aucun article retiré des baisses.")
        return {}
    dernier = {}
    for ligne in lignes:
        if ligne.get("article_id"):
            dernier[ligne["article_id"]] = ligne
    return {aid: l for aid, l in dernier.items() if l["type"] == "prix_change"}


STRATEGIES = ("standard", "destockage")


def strategies_articles():
    """{article_id: stratégie} des articles placés sur une stratégie autre que
    "standard" (dernier événement "strategie" du journal). {} si illisible."""
    from database import supabase as db
    try:
        lignes = db.table("article_evenements").select("article_id, origine, survenu_le") \
            .eq("type", "strategie").order("survenu_le").execute().data or []
    except Exception as e:
        logger.warning(f"⚠️ [JOURNAL] Stratégies illisibles ({e}) — tous les articles en standard.")
        return {}
    derniere = {l["article_id"]: l["origine"] for l in lignes if l.get("article_id")}
    return {aid: s for aid, s in derniere.items() if s and s != "standard"}


def poser_strategie(article_id, nom, dressing, strategie, ancienne):
    """Choix de stratégie depuis le dashboard (28/10/2026) : "destockage"
    (fin de vie immédiate, rythme 7 j) ou retour en "standard"."""
    from database import supabase as db
    libelles = {"standard": "Standard", "destockage": "Déstockage"}
    maintenant = datetime.now().astimezone()
    db.table("article_evenements").insert({
        "article_id": article_id, "nom": nom, "dressing": _dressing(dressing),
        "type": "strategie", "origine": strategie,
        "detail": f"Stratégie : {libelles.get(ancienne, ancienne)} → {libelles.get(strategie, strategie)}.",
        "source": "dashboard", "source_ref": f"strategie:{article_id}:{maintenant.isoformat()}",
        "survenu_le": maintenant.isoformat(),
    }).execute()
    logger.info(f"🧭 [JOURNAL] '{nom}' : stratégie {ancienne} → {strategie}.")


def reprendre_strategie(article_id, nom, dressing):
    """Bouton "Remettre sous stratégie" : l'article revient dans les baisses automatiques."""
    from database import supabase as db
    maintenant = datetime.now().astimezone()
    db.table("article_evenements").insert({
        "article_id": article_id, "nom": nom, "dressing": _dressing(dressing),
        "type": "reprise_strategie", "origine": "manuel",
        "detail": "Remis sous stratégie : les baisses automatiques reprennent à partir du prix actuel.",
        "source": "dashboard", "source_ref": f"reprise:{article_id}:{maintenant.isoformat()}",
        "survenu_le": maintenant.isoformat(),
    }).execute()
    logger.info(f"↩️ [JOURNAL] '{nom}' remis sous stratégie.")
