"""
Outil ponctuel : pour chaque article non vendu, va chercher sur
clemz.app/dashboard/items sa date_premiere_publication (dernière ligne du
tableau Historique de la fiche article = événement le plus ancien connu,
Clemz ne conservant que 7 derniers mois d'historique), la compare à
date_republication (déjà en base Supabase), et calcule l'écart en jours entre
les deux.

clemz.app réunit les articles des DEUX dressings sur une seule et même page --
un seul navigateur/profil suffit, pas besoin d'itérer par dressing.

Ne modifie AUCUNE donnée en base -- rapport en lecture seule (console).

Usage :
    cd backend
    venv\\Scripts\\activate
    python check_dates_suspectes.py
"""

import asyncio
import csv
import os
from datetime import datetime
from playwright.async_api import async_playwright

from database import supabase
from services.clemz_automation import _close_clemz_promo_tabs

_BASE = os.path.dirname(os.path.realpath(__file__))

# Un seul profil suffit -- clemz.app réunit les articles des deux dressings sur
# une même page, peu importe par quel profil on s'y connecte.
SESSION_PATH = os.path.join(_BASE, "clemz_session_chrome_d1_reel")

CLEMZ_ITEMS_URL = (
    "https://www.clemz.app/dashboard/items"
    "?order_value=last_saved_at&order_direction=desc"
    "&item_status_sold=off&item_status_in_stock=off&item_status_in_stock=on"
)


def normalize(s):
    return (s or "").strip().lower()


def parse_date_fr(date_str):
    """Parse 'DD/MM/YYYY' -> datetime, ou None si invalide/absent."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y")
    except ValueError:
        return None


def parse_date_republication(value):
    """Parse le format de date_republication tel que stocké en Supabase (ISO,
    avec ou sans suffixe Z/offset) -> datetime naïf, ou None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


async def find_date_premiere_publication(page, article_name):
    """
    Utilise la barre de recherche Clemz (#text_search, formulaire GET vers
    /dashboard/items) pour filtrer directement sur le nom de l'article, plutôt
    que de scanner toute la grille à chaque fois. Le formulaire soumet une
    vraie navigation de page (pas d'AJAX) -- on attend donc le rechargement
    avant de chercher la carte résultante.

    Une fois la carte trouvée : ouvre sa modale, clique l'onglet Historique,
    lit la DERNIÈRE ligne du tableau #history -- l'événement le plus ancien
    connu (trié du plus récent au plus ancien, confirmé sur le HTML du
    27/08/2026). Retourne (date_str, raison_erreur_ou_None).
    """
    target = normalize(article_name)

    search_input = await page.query_selector("#text_search")
    if search_input is None:
        return None, "Barre de recherche #text_search introuvable"

    await search_input.fill(article_name)
    await search_input.press("Enter")
    await page.wait_for_load_state("networkidle")

    cards = await page.query_selector_all(".item-card")
    matching_card = None
    for card in cards:
        title_el = await card.query_selector("h6")
        if not title_el:
            continue
        if normalize(await title_el.inner_text()) == target:
            matching_card = card
            break

    if matching_card is None:
        return None, "Aucun résultat exact pour ce nom dans la recherche Clemz"

    link = await matching_card.query_selector('a[data-bs-toggle="modal"]')
    if link is None:
        return None, "Lien d'ouverture de la fiche introuvable sur la carte"

    await link.click()
    try:
        await page.wait_for_selector("#show-item-modal.show", timeout=10000)
    except Exception:
        return None, "La modale ne s'est pas ouverte après le clic"

    # Le contenu de la modale (dont l'onglet Historique) peut être injecté par
    # un appel AJAX séparé APRÈS l'apparition de la modale elle-même -- attendre
    # l'inactivité réseau ici avant de chercher l'onglet, plutôt que de se fier
    # uniquement à wait_for_selector sur le tableau plus bas.
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    # Garde-fou anti-contamination : on vérifie que le titre affiché correspond
    # bien à l'article recherché AVANT de faire confiance à l'historique -- sans
    # ça, une modale pas encore purgée de l'article précédent ferait lire la
    # mauvaise date silencieusement.
    bon_titre = await _attendre_bon_titre(page, target)
    if not bon_titre:
        titre_actuel_el = await page.query_selector("#item-title span")
        titre_actuel = await titre_actuel_el.inner_text() if titre_actuel_el else "?"
        await _fermer_modale(page)
        return None, f"Titre affiché ne correspond pas (attendu {article_name!r}, vu {titre_actuel!r})"

    # Jusqu'à 3 tentatives : on RE-cherche #history-tab à chaque fois plutôt que
    # de réutiliser une référence capturée avant l'injection AJAX -- si le
    # contenu de la modale est remplacé après coup, l'ancienne référence peut
    # devenir obsolète et le clic ne rien faire silencieusement. Comportement
    # intermittent constaté le 27/08/2026 (même article, tantôt trouvé tantôt
    # non selon les runs) -- confirme une course avec le chargement, pas un
    # problème de structure HTML.
    rows = []
    for tentative in range(3):
        history_tab = await page.query_selector("#history-tab")
        if history_tab:
            await history_tab.click()

        try:
            await page.wait_for_selector("#history table tbody tr", timeout=8000)
            rows = await page.query_selector_all("#history table tbody tr")
            if rows:
                break
        except Exception:
            pass

        await page.wait_for_timeout(1000)

    if not rows:
        await _fermer_modale(page)
        return None, "Historique vide ou introuvable (après 3 tentatives)"

    # On ne veut QUE le type "(re)Publication" -- "Demande vues" et "Sauvegarde"
    # peuvent survenir sans republication réelle ce jour-là, donc ne sont pas un
    # signal fiable de date de publication. Le tableau étant trié du plus récent
    # au plus ancien, on parcourt à l'ENVERS et on garde la DERNIÈRE ligne
    # "(re)Publication" rencontrée -- la plus ancienne de ce type précis dans la
    # fenêtre des 7 mois d'historique conservée par Clemz.
    date_str = None
    for row in reversed(rows):
        first_cell = await row.query_selector("td")
        cell_text = await first_cell.inner_text()
        lines = [l.strip() for l in cell_text.split("\n") if l.strip()]
        if not lines:
            continue
        label = lines[0]
        if "(re)Publication" in label:
            date_str = lines[1] if len(lines) > 1 else None
            break

    await _fermer_modale(page)

    if date_str is None:
        return None, "Aucun événement (re)Publication trouvé dans l'historique (7 derniers mois)"

    return date_str, None


async def _attendre_bon_titre(page, target_normalized, tentatives=8, delai_ms=400):
    """
    Vérifie que #item-title span (onglet Détails, toujours présent dans le DOM
    même si Historique est l'onglet actif) affiche bien le nom de l'article
    recherché -- garde-fou contre la contamination croisée entre deux articles
    (modale précédente pas encore purgée au moment où on relit l'historique,
    constaté le 27/08/2026 : date d'un article A lue pour l'article B).
    """
    for _ in range(tentatives):
        title_el = await page.query_selector("#item-title span")
        if title_el:
            titre_affiche = normalize(await title_el.inner_text())
            if titre_affiche == target_normalized:
                return True
        await page.wait_for_timeout(delai_ms)
    return False


async def _fermer_modale(page):
    try:
        await page.click('.btn-close[data-bs-dismiss="modal"]', timeout=3000)
        # Attendre que Bootstrap ait bien retiré la classe "show" (fin réelle de
        # la transition de fermeture) -- une simple pause fixe ne garantissait
        # pas que le contenu précédent soit purgé avant la recherche suivante.
        await page.wait_for_selector("#show-item-modal.show", state="detached", timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(300)


async def run():
    response = supabase.table("articles") \
        .select("nom, dressing, date_publication, date_republication") \
        .eq("est_vendu", False) \
        .execute()
    articles = response.data or []

    if not articles:
        print("Aucun article à vérifier.")
        return []

    print(f"🔍 {len(articles)} article(s) à vérifier.\n")

    resultats = []

    async with async_playwright() as p:
        print("🚀 Ouverture du navigateur (un seul, pour les deux dressings)...")
        context = await p.chromium.launch_persistent_context(
            SESSION_PATH,
            channel="chrome",
            headless=False,
            ignore_default_args=[
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ],
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await _close_clemz_promo_tabs(context, page)

        await page.goto(CLEMZ_ITEMS_URL, wait_until="networkidle")

        input(
            "\n⏸️  Si besoin, connecte-toi à clemz.app dans la fenêtre qui vient de "
            "s'ouvrir, puis reviens ici et appuie sur Entrée pour lancer la vérification..."
        )

        for art in articles:
            date_premiere_publication, erreur = await find_date_premiere_publication(page, art["nom"])

            # Comparaison contre date_publication (PAS date_republication) --
            # c'est ce champ qui sert à calculer jours_en_vente affiché sur le
            # dashboard (⏳). Un grand écart ici est anormal : contrairement à
            # date_republication (qui avance logiquement à chaque republication),
            # date_publication ne devrait JAMAIS bouger après la toute première
            # mise en ligne -- un écart important signale que ce champ a
            # probablement été réinitialisé (annonce recréée en brouillon plutôt
            # que republiée en place).
            date_publication_obj = parse_date_republication(art.get("date_publication"))
            date_premiere_publication_obj = parse_date_fr(date_premiere_publication)

            ecart = None
            if date_premiere_publication_obj and date_publication_obj:
                ecart = (date_publication_obj - date_premiere_publication_obj).days

            resultats.append({
                "nom": art["nom"],
                "dressing": art["dressing"],
                "date_premiere_publication": date_premiere_publication,
                "date_publication": art.get("date_publication"),
                "date_republication": art.get("date_republication"),
                "ecart": ecart,
                "erreur": erreur,
            })

            print(f"— {art['nom'][:70]} ({art['dressing']})")
            if erreur:
                print(f"    ⚠️  {erreur}")
            else:
                print(f"    date_premiere_publication (Clemz) : {date_premiere_publication}")
                print(f"    date_publication (Supabase)       : {art.get('date_publication')}")
                print(f"    écart (jours)                     : {ecart}")
            print()

        await context.close()

    chemin_rapport = os.path.join(_BASE, f"rapport_dates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    with open(chemin_rapport, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["nom", "dressing", "date_premiere_publication", "date_publication", "date_republication", "ecart", "erreur"])
        writer.writeheader()
        writer.writerows(resultats)

    print(f"✅ Terminé -- aucune donnée modifiée en base. Rapport écrit dans : {chemin_rapport}")
    return resultats


if __name__ == "__main__":
    asyncio.run(run())