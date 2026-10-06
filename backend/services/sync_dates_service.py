"""
sync_dates_service.py

Service de synchronisation des dates de republication/publication depuis un
export CSV Clemz, exposé via POST /api/maintenance/sync-dates-republication.
Reprend la logique validée de l'ancien script CLI sync_dates_republication.py :
même format CSV attendu (";" / utf-8-sig / colonnes "Titre", "Dernière
republication", "Dernière sauvegarde"), même règle de cohérence date_publication
(clampée sur date_republication si déjà republié, sinon approximée par
"Dernière sauvegarde"). La confirmation avant écriture se fait désormais via
le paramètre apply plutôt qu'un prompt terminal (o/n).
"""
import io
import re
import unicodedata
import difflib
import pandas as pd
from database import supabase


def _normalize_titre(titre: str) -> str:
    """
    Normalise un titre pour comparaison fiable : forme Unicode NFC (les accents
    peuvent arriver en NFC ou NFD selon la source -- DOM Vinted scrapé vs export
    CSV Clemz -- et sont visuellement identiques mais différents en bytes bruts),
    espaces multiples réduits à un seul, espaces de bord retirés.
    """
    titre = unicodedata.normalize("NFC", titre or "")
    titre = re.sub(r"\s+", " ", titre)
    return titre.strip().lower()


def load_csv(file_bytes: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(file_bytes), sep=";", encoding="utf-8-sig")

    required_cols = {"Titre", "Dernière republication", "Dernière sauvegarde"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Colonnes manquantes dans le CSV : {missing}")

    df = df[["Titre", "Dernière republication", "Dernière sauvegarde"]].copy()
    df["date_repub"] = pd.to_datetime(df["Dernière republication"], errors="coerce")
    df["date_save"] = pd.to_datetime(df["Dernière sauvegarde"], errors="coerce")

    df = df.sort_values(["date_repub", "date_save"], ascending=[False, False])
    df = df.drop_duplicates(subset=["Titre"], keep="first")
    df["titre_clean"] = df["Titre"].apply(_normalize_titre)

    return df


def fetch_all_articles():
    all_rows = []
    page_size = 1000
    start = 0
    while True:
        response = (
            supabase.table("articles")
            .select("id, nom, date_publication, date_republication")
            .range(start, start + page_size - 1)
            .execute()
        )
        rows = response.data
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def compute_updates(csv_df: pd.DataFrame, articles: list) -> dict:
    """Calcule les mises à jour à appliquer, sans rien écrire."""
    csv_lookup = {}
    for _, row in csv_df.iterrows():
        csv_lookup[row["titre_clean"]] = {
            "date_repub": row["date_repub"].strftime("%Y-%m-%d") if pd.notna(row["date_repub"]) else None,
            "date_save": row["date_save"].strftime("%Y-%m-%d") if pd.notna(row["date_save"]) else None,
        }

    csv_titles_clean = list(csv_lookup.keys())

    updates = []
    unchanged_count = 0
    not_found = []
    not_found_diag = []  # diagnostic : titre CSV le plus proche pour chaque non-trouvé

    for art in articles:
        nom = art.get("nom", "")
        nom_clean = _normalize_titre(nom)
        match = csv_lookup.get(nom_clean)

        if match is None:
            not_found.append(nom)

            # Diagnostic : cherche le titre CSV le plus proche pour repérer une
            # différence invisible (accent, espace, ponctuation) plutôt qu'une
            # vraie absence du produit dans le CSV.
            closest = difflib.get_close_matches(nom_clean, csv_titles_clean, n=1, cutoff=0.85)
            if closest:
                closest_titre = closest[0]
                diff_positions = [
                    i for i, (a, b) in enumerate(zip(nom_clean, closest_titre)) if a != b
                ]
                not_found_diag.append({
                    "nom_bd": nom,
                    "nom_bd_clean_repr": repr(nom_clean),
                    "csv_proche_repr": repr(closest_titre),
                    "longueur_bd": len(nom_clean),
                    "longueur_csv": len(closest_titre),
                    "premiere_diff_index": diff_positions[0] if diff_positions else None,
                })
            continue

        champs_a_maj = {}
        raisons = []

        date_repub_csv = match["date_repub"]
        date_save_csv = match["date_save"]
        current_repub = art.get("date_republication")
        current_pub = art.get("date_publication")

        if date_repub_csv:
            if current_repub != date_repub_csv:
                champs_a_maj["date_republication"] = date_repub_csv
                raisons.append(f"date_republication: {current_repub} → {date_repub_csv}")

            if current_pub is None or current_pub > date_repub_csv:
                champs_a_maj["date_publication"] = date_repub_csv
                raisons.append(f"date_publication (clampée): {current_pub} → {date_repub_csv}")

        elif date_save_csv:
            if current_pub != date_save_csv:
                champs_a_maj["date_publication"] = date_save_csv
                raisons.append(f"date_publication (≈ dernière sauvegarde): {current_pub} → {date_save_csv}")

        if champs_a_maj:
            updates.append({"id": art["id"], "nom": nom, "champs": champs_a_maj, "raisons": raisons})
        else:
            unchanged_count += 1

    return {
        "updates": updates,
        "unchanged_count": unchanged_count,
        "not_found": not_found,
        "not_found_diag": not_found_diag,
    }


def apply_updates(updates: list) -> dict:
    """Applique réellement les mises à jour en base."""
    success_count = 0
    errors = []
    for u in updates:
        try:
            supabase.table("articles").update(u["champs"]).eq("id", u["id"]).execute()
            success_count += 1
        except Exception as e:
            errors.append({"nom": u["nom"], "error": str(e)[:200]})
    return {"success_count": success_count, "errors": errors}


def run_sync(file_bytes: bytes, apply: bool = False) -> dict:
    """
    Point d'entrée unique appelé par la route. apply=False -> aperçu seul
    (équivalent --dry-run du script CLI). apply=True -> aperçu + écriture réelle.
    """
    csv_df = load_csv(file_bytes)
    articles = fetch_all_articles()
    report = compute_updates(csv_df, articles)

    result = {
        "csv_titles_count": len(csv_df),
        "articles_count": len(articles),
        "to_update_count": len(report["updates"]),
        "unchanged_count": report["unchanged_count"],
        "not_found_count": len(report["not_found"]),
        "not_found": report["not_found"][:20],
        "not_found_diag": report["not_found_diag"][:10],
        "updates_preview": [{"nom": u["nom"], "raisons": u["raisons"]} for u in report["updates"][:50]],
        "applied": False,
    }

    if apply and report["updates"]:
        apply_result = apply_updates(report["updates"])
        result["applied"] = True
        result["success_count"] = apply_result["success_count"]
        result["errors"] = apply_result["errors"]

    return result