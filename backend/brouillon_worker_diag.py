"""
Test manuel du worker de génération de brouillons, sans passer par le watcher.
Usage : python brouillon_worker_diag.py <chemin_photo1> <chemin_photo2> ...
Insère un lot de test dans brouillons_pending, lance le worker dessus, affiche
le résultat.
"""
import sys
import uuid

from database import supabase
from services.brouillon_worker import traiter_lots_pending_generation


def main():
    if len(sys.argv) < 2:
        print("Usage : python brouillon_worker_diag.py <photo1> <photo2> ...")
        sys.exit(1)

    photos = sys.argv[1:]
    lot_id = str(uuid.uuid4())

    supabase.table("brouillons_pending").insert({
        "id": lot_id,
        "photos": photos,
        "status": "pending_generation",
        "dressing": "Dressing 2",
    }).execute()

    print(f"📦 Lot de test {lot_id} inséré, lancement du worker...")
    resume = traiter_lots_pending_generation()
    print(f"Résumé : {resume}")

    row = supabase.table("brouillons_pending").select("*").eq("id", lot_id).execute()
    print("\n--- Résultat en base ---")
    print(row.data[0])


if __name__ == "__main__":
    main()