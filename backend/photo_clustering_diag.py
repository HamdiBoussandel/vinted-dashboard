"""
Diagnostic : clustering EXIF/nom de fichier des photos par article.
Usage : python photo_clustering_diag.py "C:\chemin\vers\dossier"
"""
import os
import sys
from photo_grouping import EXTENSIONS_VALIDES, grouper_photos_par_article


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python photo_clustering_diag.py \"C:\\chemin\\vers\\dossier\"")
        sys.exit(1)

    dossier = sys.argv[1]
    if not os.path.isdir(dossier):
        print(f"❌ Dossier introuvable : {dossier}")
        sys.exit(1)

    fichiers = [
        os.path.join(dossier, f) for f in os.listdir(dossier)
        if f.lower().endswith(EXTENSIONS_VALIDES)
    ]
    groupes, a_trier = grouper_photos_par_article(fichiers)

    total_fiable = sum(len(g) for g in groupes)
    print(f"\n🔎 {len(groupes)} groupe(s) auto — {total_fiable} photo(s) datées de façon fiable")
    print(f"⚠️  {len(a_trier)} photo(s) à regrouper manuellement (aucune source de temps fiable)\n")

    for i, groupe in enumerate(groupes, start=1):
        debut = groupe[0][1].strftime("%H:%M:%S")
        fin = groupe[-1][1].strftime("%H:%M:%S")
        print(f"--- Groupe {i} ({len(groupe)} photo(s)) | {debut} → {fin} ---")
        for chemin, heure, source in groupe:
            print(f"   {heure.strftime('%H:%M:%S')} [{source}]  {os.path.basename(chemin)}")
        print()

    if a_trier:
        print("--- À regrouper manuellement (dashboard) ---")
        for chemin, heure in a_trier:
            print(f"   {os.path.basename(chemin)}")