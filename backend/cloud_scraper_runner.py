"""
Point d'entrée cron pour le scraping cloud. Enchaîne : vérification des DEUX
sessions dédiées (headless) -> scraping des deux dressings -> sync Supabase
-> capture snapshot score. Notifie par email en cas d'échec à n'importe
quelle étape -- pas d'humain, pas d'écran pour s'en rendre compte autrement.

Usage (via crontab) :
    0 14,22 * * * cd ~/vintedpro-scraper && venv/bin/python3 cloud_scraper_runner.py
"""
import asyncio
import sys
import traceback
from datetime import datetime

from services.email_notifier import envoyer_notification_echec


async def main():
    try:
        from session_manager_cloud import ensure_both_sessions_cloud, ACCOUNTS_CLOUD
        from services.vinted_scraper import VintedScraper
        from services.supabase_service import SupabaseService

        ok_d1, ok_d2 = await ensure_both_sessions_cloud()

        if not ok_d1 or not ok_d2:
            comptes_ko = []
            if not ok_d1:
                comptes_ko.append("Dressing 1")
            if not ok_d2:
                comptes_ko.append("Dressing 2")
            # ensure_both_sessions_cloud() a déjà notifié le détail par compte --
            # on arrête ici plutôt que de lancer un scraping partiel/voué à échouer.
            print(f"⏭️  Session(s) invalide(s) ({', '.join(comptes_ko)}), sync annulée.")
            sys.exit(1)

        scraper = VintedScraper()

        articles_d1 = await scraper.scrap_vinted(
            browser_type="chromium",
            session_path=ACCOUNTS_CLOUD["dressing1"]["session"],
            member_id=ACCOUNTS_CLOUD["dressing1"]["member_id"],
            dressing_name="Dressing 1",
        )
        articles_d2 = await scraper.scrap_vinted(
            browser_type="chromium",
            session_path=ACCOUNTS_CLOUD["dressing2"]["session"],
            member_id=ACCOUNTS_CLOUD["dressing2"]["member_id"],
            dressing_name="Dressing 2",
        )

        tous_les_articles = articles_d1 + articles_d2
        if not tous_les_articles:
            message = "Aucun article récupéré sur les deux dressings (scraping cloud)."
            print(f"❌ {message}")
            envoyer_notification_echec("Scraping cloud vide", message)
            sys.exit(1)

        print(f"✅ {len(tous_les_articles)} articles récupérés, synchronisation Supabase...")
        start_time = datetime.now()
        created, updated = await scraper.sync_to_supabase(tous_les_articles)
        print(f"📦 Sync terminée : {created} créés, {updated} mis à jour.")

        svc = SupabaseService()
        nb = svc.capture_score_snapshots()
        print(f"📸 {nb} snapshots de score enregistrés")

        print("✅ Sync cloud terminée avec succès.")

    except Exception as e:
        message = f"Le scraping cloud a échoué :\n\n{traceback.format_exc()}"
        print(f"❌ {message}")
        envoyer_notification_echec("Échec du scraping cloud", message)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())