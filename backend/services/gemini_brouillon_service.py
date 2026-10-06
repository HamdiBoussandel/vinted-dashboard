"""
Génération du contenu structuré d'un brouillon Vinted (titre, description,
hashtags + champs structurés genre/type/marque/taille/couleur/état/matière/
motif) à partir des photos d'UN SEUL article, puis résolution en IDs Vinted
via referentiel_matching.py.
"""
import json
import mimetypes
import os

import google.generativeai as genai

from database import GEMINI_API_KEYS
from services.gemini_utils import (
    wait_for_gemini_rate_limit,
    set_gemini_keys,
    get_current_gemini_key,
    rotate_gemini_key,
    nb_gemini_keys,
    is_daily_quota_error,
)
from referentiel_matching import (
    charger_referentiels,
    INDEX_MARQUES,
    INDEX_CATEGORIES,
    matcher_par_mots,
    matcher_taille,
    mots_correspondent,
    normaliser_mot,
    enfants_directs,
    trouver_enfant_autres,
)

set_gemini_keys(GEMINI_API_KEYS)

_referentiels = charger_referentiels()

TIMEOUT_GEMINI_SECONDES = 45  # au-delà, on considère l'appel bloqué (souci réseau) plutôt que d'attendre indéfiniment


def _generate_content_avec_rotation(model, content, generation_config):
    """
    Appelle model.generate_content en configurant la clé Gemini courante avant
    chaque tentative. Si l'erreur est un dépassement de quota JOURNALIER
    (is_daily_quota_error), tourne vers la clé suivante et retente -- jusqu'à
    épuiser toutes les clés disponibles. Toute autre erreur remonte
    immédiatement (pas de rotation : ça ne réglerait rien).

    Timeout explicite (TIMEOUT_GEMINI_SECONDES) sur l'appel réseau lui-même --
    sans ça, un souci côté Google (connexion qui traîne sans jamais répondre ni
    échouer) bloquerait indéfiniment, sans que rien ne l'indique dans les logs.
    """
    tentatives_max = max(1, nb_gemini_keys())
    derniere_erreur = None
    for _ in range(tentatives_max):
        genai.configure(api_key=get_current_gemini_key())
        wait_for_gemini_rate_limit()
        try:
            return model.generate_content(
                content,
                generation_config=generation_config,
                request_options={"timeout": TIMEOUT_GEMINI_SECONDES},
            )
        except Exception as e:
            derniere_erreur = e
            if is_daily_quota_error(e) and nb_gemini_keys() > 1:
                print("🔁 Quota journalier atteint sur cette clé Gemini, rotation vers la suivante...")
                rotate_gemini_key()
                continue
            raise
    raise derniere_erreur
# --- Blocs de règles SEO repris tels quels de inventory.py (generate-description-batch) ---
REGLES_SEO_TITRE_DESCRIPTION_HASHTAGS = """
═══════════════════════════════════════
RÈGLES SEO — TITRE (PRIORITÉ ABSOLUE)
═══════════════════════════════════════
Le titre est le facteur SEO n°1 sur Vinted. Vinted utilise un moteur de recherche sémantique
qui indexe chaque mot du titre. Optimise-le comme une requête de recherche, pas comme un slogan.

- Format STRICT : [Type vêtement] [Marque] [Couleur/Matière] [Détail distinctif] [Taille]
- Maximum 100 caractères, AUCUNE virgule, AUCUN emoji dans le titre
- Marque : première lettre majuscule uniquement (Nike, Massimo Dutti — jamais NIKE)
- Inclure le maximum de mots-clés naturels que les acheteurs tapent réellement
- Exemples de bons mots-clés : coupe, matière, style (slim, oversize, laine, velours, vintage, workwear...)
- Si une info est absente des photos, indique "Non précisé" — N'INVENTE RIEN

═══════════════════════════════════════
RÈGLES SEO — DESCRIPTION
═══════════════════════════════════════
La description est indexée mot par mot par Vinted. Elle doit être riche en synonymes et
en termes de recherche naturels, tout en restant lisible et vendeuse.

- 3 à 5 lignes maximum, ton direct et efficace, sans superlatifs creux ("magnifique", "superbe")
- Inclure des synonymes du type de vêtement (ex: pour un manteau : "veste longue", "pardessus", "overcoat")
- Mentionner la matière, la coupe, et l'occasion de port si déductibles des photos

Après ce paragraphe vendeur, ajoute TOUJOURS la liste à puces suivante (structure identique à
celle utilisée pour toutes les annonces, ne pas la reformuler ni la réordonner). CHAQUE ligne
DOIT se terminer par un retour à la ligne (\\n) avant la suivante -- jamais deux informations
sur la même ligne, jamais tout enchaîné en un seul bloc de texte :

- [Type de vêtement et Marque]
- Coupe : [Déduite visuellement -- slim, oversize, droite, ajustée, etc.]
- Taille : [Déduite de l'étiquette]
- Couleur : [Déduite]
- Matières : [Composition exacte si visible. S'il y a PLUSIEURS matières et que
  l'étiquette indique leurs pourcentages, ils sont OBLIGATOIRES ici (ex: "80% Coton,
  20% Élasthanne") -- ne jamais lister seulement les noms sans pourcentage dans ce cas.
  Une seule matière sans pourcentage sur l'étiquette : le nom seul suffit.]
- Etat : [Déduit visuellement]
- Mesures en photos
- Envoi rapide et soigné sous 24/48h
- Remarques : [UNIQUEMENT si défaut visible ou coupe particulière]

═══════════════════════════════════════
HASHTAGS (OBLIGATOIRE -- NE JAMAIS OMETTRE)
═══════════════════════════════════════
Même si leur poids SEO est limité sur Vinted en 2026, les hashtags DOIVENT toujours
être présents dans ta réponse -- ne les saute jamais, même en cas de contrainte de
longueur. Génère uniquement les langues de tes vrais marchés, proportionnellement
au volume de ventes :

- 5 hashtags FR  (marché principal, 64% des ventes)
- 3 hashtags EN  (Irlande + Pays-Bas cherchent souvent en anglais)
- 3 hashtags NL  (Pays-Bas, 2ème marché réel à 11%)
- 2 hashtags IT  (Italie, 10% des ventes)
- 2 hashtags DE  (Allemagne, panier moyen élevé)

Total : 15 hashtags maximum, 0 doublon entre les langues.
Saute une ligne entre chaque groupe de langue.
NE mets PAS les initiales des langues devant les groupes.

Le champ "description" JSON doit contenir, DANS CET ORDRE : le paragraphe vendeur, une ligne
vide, la liste à puces ci-dessus, une ligne vide, puis les groupes de hashtags -- exactement
comme un acheteur le verrait dans le champ description de Vinted, prêt à être collé tel quel.
"""


def _construire_listes_fermees():
    """Construit les blocs de choix fermés injectés dans le prompt (petites listes)."""
    genres = list(_referentiels["categories"].keys())
    couleurs = list(_referentiels["couleurs"].keys())
    etats = list(_referentiels["etats"].keys())
    matieres = list(_referentiels["matieres"].keys())
    motifs = list(_referentiels["motifs"].keys())
    return genres, couleurs, etats, matieres, motifs


def _construire_bloc_ajustements(ajustements):
    """
    Construit le bloc "informations confirmées par l'utilisateur" injecté dans le
    prompt -- boutons état/défaut (mutuellement exclusifs) + fabrication (choix
    unique) ajoutés sous la description générée dans Préparer Annonces. Ces
    informations sont CONFIRMÉES et priment sur toute déduction visuelle
    contraire que Gemini pourrait faire à partir des seules photos.
    """
    if not ajustements:
        return ""

    lignes = []
    etat_defaut = ajustements.get("etat_defaut")
    if etat_defaut == "neuf":
        lignes.append(
            "- État confirmé : NEUF / JAMAIS PORTÉ. Utilise l'état Vinted le plus "
            "élevé disponible dans la liste fermée, et mentionne explicitement "
            "'jamais porté' ou 'neuf' dans la description."
        )
    elif etat_defaut == "tache_legere":
        lignes.append(
            "- Défaut confirmé : TACHE LÉGÈRE visible sur le vêtement (même si peu "
            "visible en photo). Mentionne-la explicitement dans les 'Remarques' de "
            "la description (ex: 'légère tache visible, voir photos') et choisis un "
            "'etat' à la baisse en conséquence (jamais 'Neuf' ni le meilleur état)."
        )
    elif etat_defaut == "decoloration":
        lignes.append(
            "- Défaut confirmé : DÉCOLORATION / ALTÉRATION DE COULEUR (tache de "
            "javel ou similaire). Mentionne-la explicitement dans les 'Remarques' "
            "de la description et choisis un 'etat' à la baisse en conséquence."
        )
    elif etat_defaut == "retouche":
        lignes.append(
            "- Modification confirmée : VÊTEMENT RETOUCHÉ (ourlet raccourci, taille "
            "ajustée...). Mentionne-le dans les 'Remarques' de la description "
            "(ex: 'retouché à la taille/à l'ourlet, mesures en photos')."
        )

    fabrication = ajustements.get("fabrication")
    pays_libelle = {
        "italie": "Made in Italy",
        "portugal": "Made in Portugal",
        "france": "Made in France",
    }.get(fabrication)
    if pays_libelle:
        lignes.append(
            f"- Fabrication confirmée : {pays_libelle}. Mentionne-le dans le titre "
            f"si la place le permet, sinon dans la description -- argument de vente "
            f"valorisant pour la qualité de fabrication."
        )

    if not lignes:
        return ""

    lignes_texte = "\n".join(lignes)
    return f"""
═══════════════════════════════════════
INFORMATIONS COMPLÉMENTAIRES CONFIRMÉES PAR L'UTILISATEUR
═══════════════════════════════════════
Ces informations sont CONFIRMÉES et PRIORITAIRES sur toute déduction visuelle
qui les contredirait -- intègre-les dans le titre/description/champs structurés
en conséquence :

{lignes_texte}
"""


def _construire_prompt(ajustements=None):
    genres, couleurs, etats, matieres, motifs = _construire_listes_fermees()
    bloc_ajustements = _construire_bloc_ajustements(ajustements)

    return f"""
Tu es un expert en rachat-revente sur Vinted. Tu vas recevoir toutes les photos d'UN SEUL article
(vêtement + éventuellement ses mesures). Génère le contenu de son annonce.

{REGLES_SEO_TITRE_DESCRIPTION_HASHTAGS}
{bloc_ajustements}

═══════════════════════════════════════
CHAMPS STRUCTURÉS (pour remplir automatiquement le formulaire Vinted)
═══════════════════════════════════════
En plus du titre/description/hashtags, déduis des photos :

- "genre" : choisis EXACTEMENT un de ces libellés (respecte la casse) : {", ".join(genres)}
- "type_vetement" : description libre et courte en français du type de vêtement précis
  (ex: "robe midi", "jean droit taille haute", "pull col rond"). Pas besoin de coller à un
  vocabulaire Vinted précis, du langage naturel suffit.
- "marque_brute" : le nom de la marque tel que lu sur l'étiquette/logo visible en photo.
  Si aucune marque n'est lisible, indique "Non précisé" — N'INVENTE RIEN.
- "taille_brute" : la taille telle que lue sur l'étiquette (ex: "M", "38", "42"). Si non
  lisible ou absente, indique "Non précisé".
- "couleurs" : liste de 1 à 2 libellés MAXIMUM parmi (respecte la casse) : {", ".join(couleurs)}
  Ne retiens QUE les couleurs dominantes (zones de surface significatives du
  vêtement), jamais un détail mineur isolé (boutons, surpiqûres, petit logo,
  liseré fin). Exemple : un tee-shirt jaune avec juste des boutons rouges ->
  ["Jaune"] uniquement, PAS ["Jaune", "Rouge"]. En revanche, si deux couleurs
  couvrent chacune une vraie portion du vêtement (ex: un sac moitié beige
  moitié marron), les deux sont dominantes -> ["Beige", "Marron"]. Si une
  seule couleur domine largement, ne mets qu'elle seule dans la liste.
- "etat" : choisis EXACTEMENT un de ces libellés (respecte la casse) : {", ".join(etats)}
- "matieres" : liste des matières identifiées en LISANT L'ÉTIQUETTE DE COMPOSITION
  si elle est visible et lisible sur une des photos (ex: étiquette "80% Coton,
  20% Élasthanne" -> ["Coton", "Élasthanne"]), choisies EXACTEMENT parmi ces
  libellés (respecte la casse) : {", ".join(matieres)}
  Ce champ ne garde QUE les noms (pas de pourcentage, la liste ci-dessus n'en
  contient pas) -- mais si plusieurs matières sont identifiées avec leurs
  pourcentages sur l'étiquette, reporte ces pourcentages dans la ligne "Matières"
  de la description (voir règles SEO description ci-dessus), jamais seulement ici.
  Si aucune étiquette de composition n'est visible/lisible, tu peux proposer UNE
  matière si elle est évidente visuellement (ex: jean -> "Denim"), sinon liste
  vide [] — N'INVENTE RIEN.
- "motif" : choisis EXACTEMENT un de ces libellés (respecte la casse) : {", ".join(motifs)}
  Si le vêtement est uni ou le motif indéterminé, indique "Non précisé".
- "points_vigilance" : UNIQUEMENT si nécessaire (taille trompeuse, anomalie d'authenticité) —
  sinon chaîne vide "".

═══════════════════════════════════════
RÈGLE CRITIQUE — NE JAMAIS ÉCRIRE "Non précisé" DANS LE TITRE OU LA DESCRIPTION
═══════════════════════════════════════
"Non précisé" n'est autorisé QUE dans les champs structurés individuels ci-dessus
(marque_brute, taille_brute...), jamais dans le texte visible par un acheteur.
Si une info manque pour le titre ou la description, OMETS-LA simplement — ne mentionne
jamais son absence. Un acheteur ne doit jamais voir un placeholder ou une mention de donnée
manquante dans l'annonce elle-même.

═══════════════════════════════════════
FORMAT DE RÉPONSE — CRITIQUE
═══════════════════════════════════════
Rappel avant de répondre : le champ "description" doit impérativement contenir les
15 hashtags à la toute fin, en plus du paragraphe vendeur et de la liste à puces --
ne les omets sous aucun prétexte, même si tu dois raccourcir le paragraphe vendeur
pour faire de la place.

Réponds UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ou après, sans balises
markdown (pas de ```json), avec EXACTEMENT ces clés :

{{
  "titre": "...",
  "description": "... (description + liste à puces + hashtags, prête à coller dans le champ description Vinted)",
  "genre": "...",
  "type_vetement": "...",
  "marque_brute": "...",
  "taille_brute": "...",
  "couleurs": ["..."],
  "etat": "...",
  "matieres": ["..."],
  "motif": "...",
  "points_vigilance": "..."
}}
"""


def _deviner_mime_type(chemin_fichier):
    mime, _ = mimetypes.guess_type(chemin_fichier)
    return mime or "image/jpeg"

def _demander_sous_categorie_gemini(chemins_photos, nom_branche, noms_enfants):
    """
    Second appel Gemini, ciblé : demande de choisir EXACTEMENT une sous-catégorie
    parmi une liste fermée et courte (les enfants directs d'une branche Vinted,
    ex: pour "T-shirts" -> T-shirts unis/imprimés/à rayures/Polos/...), en
    ré-analysant les mêmes photos. Ne se déclenche QUE quand la catégorie large
    résolue a effectivement des sous-catégories parmi lesquelles choisir --
    gratuit (aucun appel supplémentaire) pour les catégories directement
    sélectionnables comme "Blouse" qui n'ont pas de sous-type.
    """
    prompt = f"""
Tu regardes les photos d'un vêtement déjà identifié comme "{nom_branche}".
Voici les sous-catégories Vinted RÉELLEMENT disponibles pour ce type de vêtement :
{", ".join(noms_enfants)}

Choisis EXACTEMENT UNE de ces valeurs (recopie le libellé mot pour mot, aucune
reformulation), celle qui correspond le mieux à ce que montrent les photos.
Si aucune ne correspond clairement et qu'une option "Autres ..." existe dans
la liste, choisis-la. Réponds UNIQUEMENT avec le libellé choisi, rien d'autre
(pas de guillemets, pas de ponctuation superflue, pas d'explication).
"""
    content = [prompt]
    for chemin in chemins_photos:
        try:
            with open(chemin, "rb") as f:
                content.append({"mime_type": _deviner_mime_type(chemin), "data": f.read()})
        except Exception:
            continue

    try:
        model = genai.GenerativeModel("gemini-3.1-flash-lite")
        response = _generate_content_avec_rotation(model, content, None)
        return response.text.strip().strip('"').strip("'")
    except Exception as e:
        print(f"⚠️  Erreur affinage sous-catégorie : {e}")
        return ""


def _resoudre_categorie(genre, type_vetement, chemins_photos):
    """
    Résout la catégorie en donnant la priorité à un match direct sur une
    FEUILLE (catégorie sans sous-type, ex: "Blouse" -> sélectionnable
    directement, aucun appel Gemini supplémentaire). Si le texte ne correspond
    qu'à une BRANCHE ayant des sous-catégories (ex: "T-shirts"), on redemande
    à Gemini de trancher parmi la vraie liste de sous-catégories Vinted,
    plutôt que de deviner ou de retomber par défaut sur "Autres".
    """
    candidats_genre = [e for e in INDEX_CATEGORIES if e["genre"] == genre]
    feuilles = [e for e in candidats_genre if not e.get("a_des_enfants")]

    noeud = matcher_par_mots(type_vetement, feuilles)
    if noeud:
        return noeud

    branches = [e for e in candidats_genre if e.get("a_des_enfants")]
    noeud = matcher_par_mots(type_vetement, branches)
    if not noeud:
        return None

    profondeur_max = 4
    for _ in range(profondeur_max):
        enfants = enfants_directs(noeud, INDEX_CATEGORIES)
        if not enfants:
            break
        noms_enfants = [e["nom"] for e in enfants]
        choix = _demander_sous_categorie_gemini(chemins_photos, noeud["nom"], noms_enfants)
        enfant_choisi = next(
            (e for e in enfants if mots_correspondent(normaliser_mot(e["nom"]), normaliser_mot(choix))),
            None,
        )
        if not enfant_choisi:
            enfant_choisi = trouver_enfant_autres(noeud, INDEX_CATEGORIES) or enfants[0]
        noeud = enfant_choisi
        if not noeud.get("a_des_enfants"):
            break

    return noeud

def _resoudre_champs(gemini_json, chemins_photos):
    """
    Résout genre/type_vetement/marque_brute/taille_brute/couleur/etat/matiere/motif
    en IDs Vinted via referentiel_matching. Tout champ non résolu avec confiance
    reste à None (laissé vide pour complétion manuelle). chemins_photos est
    nécessaire pour le second appel Gemini d'affinage catégorie, quand une
    branche à sous-catégories est détectée.
    """
    resolution = {
        "categorie_id": None, "categorie_chemin": None, "size_id": None,
        "marque_id": None, "marque_nom": None,
        "taille_id": None, "taille_libelle": None,
        "couleur_ids": [], "etat_id": None, "matiere_ids": [], "motif_id": None,
    }

    genre = gemini_json.get("genre", "")
    type_vetement = gemini_json.get("type_vetement", "")
    if genre and type_vetement:
        cat = _resoudre_categorie(genre, type_vetement, chemins_photos)
        if cat:
            resolution["categorie_id"] = cat["id"]
            resolution["categorie_chemin"] = cat["chemin"]
            resolution["size_id"] = cat["size_id"]

    marque_brute = gemini_json.get("marque_brute", "")
    if marque_brute and marque_brute != "Non précisé":
        marque = matcher_par_mots(marque_brute, INDEX_MARQUES)
        if marque:
            resolution["marque_id"] = marque["id"]
            resolution["marque_nom"] = marque["nom"]

    taille_brute = gemini_json.get("taille_brute", "")
    if taille_brute and taille_brute != "Non précisé" and resolution["size_id"] is not None:
        tailles_dispo = _referentiels["tailles"].get(str(resolution["size_id"]))
        if tailles_dispo:
            res_taille = matcher_taille(taille_brute, tailles_dispo)
            if res_taille:
                resolution["taille_libelle"], resolution["taille_id"] = res_taille

    # Vinted limite à 2 couleurs -- on tronque par sécurité même si le prompt
    # demande déjà 1-2 max, au cas où Gemini en renvoie plus.
    for couleur in gemini_json.get("couleurs", [])[:2]:
        if couleur in _referentiels["couleurs"]:
            resolution["couleur_ids"].append(_referentiels["couleurs"][couleur]["id"])

    etat = gemini_json.get("etat", "")
    if etat in _referentiels["etats"]:
        resolution["etat_id"] = _referentiels["etats"][etat]
    else:
        # Filet de sécurité : l'état est un champ obligatoire côté Vinted (pas
        # d'option "à compléter manuellement" comme marque/catégorie). Si le
        # texte de Gemini ne matche aucun libellé exact, on retombe sur "Très
        # bon état" plutôt que de laisser le brouillon sans état du tout.
        resolution["etat_id"] = _referentiels["etats"].get("Très bon état")

    for matiere in gemini_json.get("matieres", []):
        if matiere in _referentiels["matieres"]:
            resolution["matiere_ids"].append(_referentiels["matieres"][matiere])

    motif = gemini_json.get("motif", "")
    if motif in _referentiels["motifs"]:
        resolution["motif_id"] = _referentiels["motifs"][motif]

    return resolution


def generer_brouillon_ia(chemins_photos, ajustements=None):
    """
    Génère le contenu d'un brouillon Vinted à partir des photos d'UN SEUL article.

    ajustements (optionnel) : dict {"etat_defaut": "neuf"|"tache_legere"|
    "decoloration"|"retouche"|None, "fabrication": "italie"|"portugal"|"france"|None}
    -- informations confirmées manuellement par l'utilisateur (boutons sous la
    description dans Préparer Annonces), injectées en priorité dans le prompt.

    Retourne :
      {"status": "success", "gemini_raw": {...}, "resolution": {...}}
      ou {"status": "error", "message": "..."}
    """
    if not chemins_photos:
        return {"status": "error", "message": "Aucune photo fournie."}

    content = [_construire_prompt(ajustements)]
    for chemin in chemins_photos:
        try:
            with open(chemin, "rb") as f:
                content.append({"mime_type": _deviner_mime_type(chemin), "data": f.read()})
        except Exception as e:
            return {"status": "error", "message": f"Erreur lecture photo {chemin} : {e}"}

    try:
        model = genai.GenerativeModel("gemini-3.1-flash-lite")

        response = _generate_content_avec_rotation(model, content, None)

        texte_brut = response.text.strip()
        # Sécurité : au cas où Gemini ajoute quand même des balises markdown
        if texte_brut.startswith("```"):
            texte_brut = texte_brut.strip("`")
            if texte_brut.lower().startswith("json"):
                texte_brut = texte_brut[4:].strip()

        gemini_json = json.loads(texte_brut)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"Réponse Gemini non-JSON : {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Erreur appel Gemini : {e}"}

    resolution = _resoudre_champs(gemini_json, chemins_photos)

    # Estimation de prix retirée du prompt le 04/10/2026 (sur demande explicite) --
    # "prix_estimation" reste à None plutôt que supprimé du retour, pour que
    # brouillon_worker.py (qui ne l'écrit que si not None) et le frontend
    # (qui n'affiche le bloc que si prix_estimation est renseigné) continuent
    # de fonctionner sans modification côté appelant.
    return {"status": "success", "gemini_raw": gemini_json, "resolution": resolution, "prix_estimation": None}