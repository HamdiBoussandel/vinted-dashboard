"""
Logique de remplissage du formulaire Vinted "Vends ton article", partagée
entre le script diagnostic (test manuel, un lot à la fois avec confirmation)
et le worker automatique (tous les lots prêts, en chaîne avec pause).
"""
import asyncio
import random

URL_NOUVELLE_ANNONCE = "https://www.vinted.fr/items/new"


# ---------------------------------------------------------------------------
# Humanisation
# ---------------------------------------------------------------------------

async def pause(mini=0.6, maxi=1.6):
    await asyncio.sleep(random.uniform(mini, maxi))


async def clic_humain(page, locator):
    await locator.scroll_into_view_if_needed()
    box = await locator.bounding_box()
    if box:
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await page.mouse.move(x - 40, y - 20, steps=8)
        await pause(0.15, 0.35)
        await page.mouse.move(x, y, steps=6)
        await pause(0.1, 0.25)
    await locator.click()


async def clic_dans_liste(page, locator):
    """Clic pour un élément dans une liste déroulante interne en overlay (couleur, matière)."""
    await locator.click()


async def fermer_dropdown_si_ouvert(page):
    """
    Ferme tout panneau déroulant qui pourrait être resté ouvert -- que la
    sélection qui précède ait réussi ou échoué (ex: résultat de recherche
    introuvable, aucun clic n'a donc eu lieu pour refermer le panneau tout
    seul). Appelée SYSTÉMATIQUEMENT après chaque champ, succès ou échec, pour
    garantir qu'aucun overlay ne reste bloquant pour les champs suivants ni
    pour le bouton final de sauvegarde. Échap ferme n'importe quel panneau
    Vinted de façon fiable, contrairement à un clic sur une zone neutre qui
    peut lui-même être intercepté par un autre overlay resté ouvert.
    """
    await page.keyboard.press("Escape")
    await pause(0.2, 0.4)


async def ouvrir_champ_par_placeholder(page, texte_placeholder, exact=True):
    """
    Ouvre un champ InputDropdown en ciblant l'attribut placeholder de son
    <input readonly> (get_by_text ne "voit" pas un placeholder, ce n'est pas
    du texte de contenu réel dans le DOM).
    """
    champ = page.get_by_placeholder(texte_placeholder, exact=exact)
    await champ.wait_for(timeout=5000)
    await clic_humain(page, champ)


# ---------------------------------------------------------------------------
# Remplissage des champs
# ---------------------------------------------------------------------------

async def uploader_photos(page, chemins_photos):
    print(f"📸 Upload de {len(chemins_photos)} photo(s)...")
    input_photos = page.locator('[data-testid="add-photos-input"]')
    await input_photos.set_input_files(chemins_photos)
    await asyncio.sleep(2 + 0.5 * len(chemins_photos))


async def remplir_titre_description(page, titre, description):
    print("✏️  Titre + description...")
    await clic_humain(page, page.locator("#title"))
    await page.fill("#title", titre)
    await pause()
    await clic_humain(page, page.locator("#description"))
    await page.fill("#description", description)
    await pause()


async def selectionner_categorie(page, categorie_id, nom_feuille):
    if not categorie_id or not nom_feuille:
        print("⏭️  Catégorie non résolue, ignorée.")
        return
    print(f"📁 Catégorie : {nom_feuille} (id={categorie_id})")
    try:
        await clic_humain(page, page.locator("#category"))
        await pause()
        await page.fill("#catalog-search-input", nom_feuille)
        await pause(1.0, 1.8)
        resultat = page.locator(f"#catalog-search-{categorie_id}-result")
        await resultat.wait_for(timeout=5000)
        await clic_humain(page, resultat)
        await pause()
    except Exception:
        print(f"⚠️  Résultat catégorie {categorie_id} introuvable, ignoré.")
    finally:
        await fermer_dropdown_si_ouvert(page)


async def selectionner_marque(page, marque_id, marque_nom):
    if not marque_id or not marque_nom:
        print("⏭️  Marque non résolue, ignorée.")
        return
    print(f"🏷️  Marque : {marque_nom} (id={marque_id})")
    try:
        await ouvrir_champ_par_placeholder(page, "Sélectionne une marque")
        await pause()
        champ_recherche = page.locator("#brand-search-input")
        try:
            await champ_recherche.wait_for(timeout=3000)
            await champ_recherche.fill(marque_nom)
            await pause(1.0, 1.8)
        except Exception:
            pass  # peut-être déjà dans "Marques populaires"
        resultat = page.locator(f"#brand-{marque_id}")
        await resultat.wait_for(timeout=5000)
        await clic_humain(page, resultat)
        await pause()
    except Exception:
        print(f"⚠️  Champ ou résultat Marque introuvable, ignoré.")
    finally:
        await fermer_dropdown_si_ouvert(page)


async def selectionner_taille(page, size_id, taille_id):
    if not size_id or not taille_id:
        print("⏭️  Taille non résolue, ignorée.")
        return
    print(f"📏 Taille (size_id={size_id}, taille_id={taille_id})")
    try:
        await ouvrir_champ_par_placeholder(page, "Sélectionne une taille")
        await pause()
        option = page.locator(f'[data-testid="size-group-{size_id}-grid-option-{taille_id}"]')
        await option.wait_for(timeout=5000)
        await clic_humain(page, option)
        await pause()
    except Exception:
        print("⚠️  Champ ou option Taille introuvable, ignoré.")
    finally:
        await fermer_dropdown_si_ouvert(page)


async def selectionner_etat(page, etat_id):
    if not etat_id:
        print("⏭️  État non résolu, ignoré.")
        return
    print(f"✨ État (id={etat_id})")
    try:
        await ouvrir_champ_par_placeholder(page, "Sélectionne un état")
        await pause()
        option = page.locator(f"#condition-{etat_id}")
        await option.wait_for(timeout=5000)
        await clic_humain(page, option)
        await pause()
    except Exception:
        print("⚠️  Champ ou option État introuvable, ignoré.")
    finally:
        await fermer_dropdown_si_ouvert(page)


async def selectionner_couleur(page, couleur_ids):
    if not couleur_ids:
        print("⏭️  Couleur non résolue, ignorée.")
        return
    print(f"🎨 Couleur(s) (ids={couleur_ids})")
    try:
        await ouvrir_champ_par_placeholder(page, "couleurs maximum", exact=False)
        await pause()
        for couleur_id in couleur_ids:
            # Vinted n'expose plus d'id="color-{id}" -- data-testid="color-{id}"
            # sur l'item de la liste "Toutes les couleurs" (celui de la section
            # "Suggestions" est dupliqué en "suggested-color-{id}", à éviter).
            option = page.locator(f'[data-testid="color-{couleur_id}"]')
            try:
                await option.wait_for(timeout=5000)
                await clic_dans_liste(page, option)
                await pause(0.3, 0.6)
            except Exception:
                print(f"⚠️  Option de couleur {couleur_id} introuvable, ignorée.")
    except Exception:
        print("⚠️  Champ Couleur introuvable, ignoré.")
    finally:
        await fermer_dropdown_si_ouvert(page)


async def selectionner_matiere(page, matiere_ids):
    if not matiere_ids:
        print("⏭️  Matière non résolue, ignorée.")
        return
    print(f"🧵 Matière(s) (ids={matiere_ids})")
    try:
        await ouvrir_champ_par_placeholder(page, "Sélectionne un matériau")
        await pause()
        for matiere_id in matiere_ids:
            option = page.locator(f"#material-{matiere_id}")
            try:
                await option.wait_for(timeout=5000)
                await clic_dans_liste(page, option)
                await pause(0.3, 0.6)
            except Exception:
                print(f"⚠️  Option de matière {matiere_id} introuvable, ignorée.")
    except Exception:
        print("⚠️  Champ Matière introuvable, ignoré.")
    finally:
        await fermer_dropdown_si_ouvert(page)

async def selectionner_colis_petit(page):
    """
    Force systématiquement le format de colis "Petit", peu importe le produit
    -- Vinted présélectionne "Moyen" par défaut (badge "Recommandé"), qu'on
    écrase volontairement à chaque fois.
    """
    print("📦 Format de colis : Petit")
    try:
        option = page.locator("#package-size-1")
        await option.wait_for(timeout=5000)
        await clic_humain(page, option)
        await pause()
    except Exception:
        print("⚠️  Option de format de colis 'Petit' introuvable, ignorée.")

async def fermer_modale_feedback_si_presente(page, timeout_ms=4000):
    """
    Après la sauvegarde d'un brouillon, Vinted affiche parfois une modale de
    satisfaction ("Évalue notre support"). On se contente de la fermer via sa
    croix, sans jamais répondre au sondage. Attente courte et non bloquante :
    si la modale n'apparaît pas dans le délai, on continue simplement --
    ce n'est pas systématique.
    """
    try:
        bouton_fermer = page.locator('[data-testid="feedback-resolved-close"]')
        await bouton_fermer.wait_for(timeout=timeout_ms)
        print("💬 Modale de feedback détectée, fermeture...")
        await clic_humain(page, bouton_fermer)
        await pause(0.3, 0.6)
    except Exception:
        pass  # Modale absente -- comportement normal, rien à faire


async def fermer_modale_authenticite_si_presente(page, timeout_ms=4000):
    """
    Pour certaines marques (mode/luxe à risque de contrefaçon), Vinted affiche
    une modale "Preuves d'authenticité" juste après la sélection de la marque,
    invitant à ajouter des photos supplémentaires. On la ferme systématiquement
    sans jamais cliquer sur "Ajoute des photos" -- le contenu du lot est déjà
    figé par la génération IA. Attente courte et non bloquante : si la modale
    n'apparaît pas dans le délai, on continue simplement.
    """
    try:
        bouton_fermer = page.locator('[data-testid="authenticity-modal--close-button"]')
        await bouton_fermer.wait_for(timeout=timeout_ms)
        print("🔍 Modale 'Preuves d'authenticité' détectée, fermeture...")
        await clic_humain(page, bouton_fermer)
        await pause(0.3, 0.6)
    except Exception:
        pass  # Modale absente -- comportement normal, rien à faire
# ---------------------------------------------------------------------------
# Orchestration complète d'un lot
# ---------------------------------------------------------------------------

async def remplir_formulaire_complet(page, lot):
    """
    Remplit l'intégralité du formulaire pour un lot (photos, titre, description,
    catégorie, marque, taille, état, couleur(s), matière(s)). Ne clique PAS sur
    "Sauvegarder le brouillon" -- à l'appelant de le faire ensuite.
    """
    photos = lot["photos"]
    raw = lot["gemini_output"]["raw"]
    resolution = lot["gemini_output"]["resolution"]

    nom_feuille_categorie = None
    if resolution.get("categorie_chemin"):
        nom_feuille_categorie = resolution["categorie_chemin"].split(" > ")[-1]

    await uploader_photos(page, photos)
    await remplir_titre_description(page, raw["titre"], raw["description"])
    await selectionner_categorie(page, resolution.get("categorie_id"), nom_feuille_categorie)
    await selectionner_marque(page, resolution.get("marque_id"), resolution.get("marque_nom"))
    await fermer_modale_authenticite_si_presente(page)
    await selectionner_taille(page, resolution.get("size_id"), resolution.get("taille_id"))
    await selectionner_etat(page, resolution.get("etat_id"))
    await selectionner_couleur(page, resolution.get("couleur_ids"))
    await selectionner_matiere(page, resolution.get("matiere_ids"))
    await selectionner_colis_petit(page)


async def forcer_fenetre_normale(context, page, largeur=1920, hauteur=1080):
    """Force la fenêtre en état normal (pas minimisé) + taille/position exactes via CDP."""
    cdp = await context.new_cdp_session(page)
    info_fenetre = await cdp.send("Browser.getWindowForTarget")
    await cdp.send("Browser.setWindowBounds", {
        "windowId": info_fenetre["windowId"],
        "bounds": {"windowState": "normal"},
    })
    await cdp.send("Browser.setWindowBounds", {
        "windowId": info_fenetre["windowId"],
        "bounds": {"left": 0, "top": 0, "width": largeur, "height": hauteur, "windowState": "normal"},
    })