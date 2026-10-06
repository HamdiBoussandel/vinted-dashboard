"""
Corrige rétroactivement date_publication en base à partir du rapport CSV déjà
généré par check_dates_suspectes.py (colonne date_premiere_publication,
récupérée depuis l'historique clemz.app -- source de vérité).

Ne touche QUE date_publication. N'écrit rien pour les lignes en erreur, sans
date_premiere_publication, ou déjà cohérentes (ecart == 0).

Usage :
    cd backend
    venv\\Scripts\\activate
    python appliquer_correction_dates.py rapport_dates_20260827_153117.csv
"""

import csv
import sys
from datetime import datetime

from database import supabase


def parse_date_fr_to_iso(date_str):
    """'DD/MM/YYYY' -> 'YYYY-MM-DD', ou None si invalide/absent."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage : python appliquer_correction_dates.py <chemin_du_rapport.csv>")
        sys.exit(1)

    chemin_csv = sys.argv[1]

    with open(chemin_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    a_corriger = []
    for row in rows:
        if row.get("erreur"):
            continue
        iso_date = parse_date_fr_to_iso(row.get("date_premiere_publication"))
        if not iso_date:
            continue
        try:
            ecart = int(row.get("ecart") or 0)
        except ValueError:
            continue

        # On ne corrige QUE dans le sens qui répare le bug d'origine (date_publication
        # poussée à tort vers le futur) -- ecart > 0 signifie que Supabase est
        # postérieure à Clemz, donc fausse dans ce sens-là.
        # ecart <= 0 : soit déjà cohérent (0), soit Supabase est DÉJÀ antérieure à
        # Clemz -- dans ce cas Supabase est probablement plus fiable (ex: annonce
        # supprimée puis recréée manuellement dans Clemz, qui a perdu son propre
        # historique d'origine) -- on ne touche jamais à ce cas pour ne pas
        # dégrader une donnée déjà meilleure.
        if ecart <= 0:
            continue

        a_corriger.append({"nom": row["nom"], "date_publication": iso_date})

    if not a_corriger:
        print("Rien à corriger -- aucune ligne éligible dans ce rapport.")
        return

    print(f"🔍 {len(a_corriger)} article(s) à corriger sur {len(rows)} lignes du rapport.\n")
    for item in a_corriger[:10]:
        print(f"  — {item['nom'][:60]} -> date_publication = {item['date_publication']}")
    if len(a_corriger) > 10:
        print(f"  ... et {len(a_corriger) - 10} autre(s).")

    confirmation = input(
        f"\n⚠️  Confirmer l'écriture de {len(a_corriger)} correction(s) dans Supabase "
        f"(colonne date_publication uniquement) ? Tape 'oui' pour continuer : "
    )
    if confirmation.strip().lower() != "oui":
        print("Annulé -- aucune modification effectuée.")
        return

    succes = 0
    echecs = []
    for item in a_corriger:
        try:
            result = supabase.table("articles") \
                .update({"date_publication": item["date_publication"]}) \
                .eq("nom", item["nom"]) \
                .execute()
            if result.data:
                succes += 1
            else:
                echecs.append((item["nom"], "Aucune ligne correspondante trouvée (nom)"))
        except Exception as e:
            echecs.append((item["nom"], str(e)))

    print(f"\n✅ {succes}/{len(a_corriger)} article(s) corrigé(s) avec succès.")
    if echecs:
        print(f"⚠️  {len(echecs)} échec(s) :")
        for nom, raison in echecs:
            print(f"  — {nom[:60]} : {raison}")


if __name__ == "__main__":
    main()
