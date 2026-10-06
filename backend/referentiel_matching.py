"""
Matching marque/categorie/taille contre vinted_referentiels.json.
Correspondance stricte normalisee (accents/casse ignores, comparaison par
prefixe commun pour absorber singulier/pluriel francais y compris irregulier),
sans tolerance aux fautes de frappe (pas de fuzzy matching).
"""
import json
import os
import re
import unicodedata

LONGUEUR_MIN_PREFIXE = 4
STOPWORDS = {'et', 'de', 'du', 'la', 'le', 'les', 'des', 'a', 'en', 'un', 'une'}

_CHEMIN_REFERENTIELS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vinted_referentiels.json")
_referentiels_cache = None


def charger_referentiels():
    """Charge et met en cache vinted_referentiels.json (chargé une seule fois)."""
    global _referentiels_cache
    if _referentiels_cache is None:
        with open(_CHEMIN_REFERENTIELS, encoding="utf-8") as f:
            _referentiels_cache = json.load(f)
    return _referentiels_cache


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def normaliser_mot(mot):
    mot = strip_accents(mot.lower())
    return re.sub(r'[^a-z0-9]', '', mot)


def mots_normalises(texte):
    bruts = re.split(r'[^a-zA-ZÀ-ÿ0-9]+', texte)
    resultat = set()
    for m in bruts:
        if not m:
            continue
        nm = normaliser_mot(m)
        if nm and nm not in STOPWORDS:
            resultat.add(nm)
    return resultat


def mots_correspondent(mot_a, mot_b):
    """
    Deux mots correspondent s'ils sont identiques, ou si l'un est prefixe de
    l'autre (absorbe singulier/pluriel francais, y compris irregulier :
    manteau/manteaux, cheveu/cheveux...). Prefixe commun >= LONGUEUR_MIN_PREFIXE
    caracteres pour eviter les faux positifs sur mots courts.
    """
    if mot_a == mot_b:
        return True
    if len(mot_a) >= LONGUEUR_MIN_PREFIXE and len(mot_b) >= LONGUEUR_MIN_PREFIXE:
        return mot_a.startswith(mot_b) or mot_b.startswith(mot_a)
    return False


def tous_les_mots_trouves(mots_entree, mots_texte):
    for mot_e in mots_entree:
        if not any(mots_correspondent(mot_e, mot_t) for mot_t in mots_texte):
            return False
    return True


def construire_index_marques(marques_dict):
    return [{"nom": nom, "id": id_marque, "mots": mots_normalises(nom), "nom_colle": normaliser_mot(nom)}
            for nom, id_marque in marques_dict.items()]


def _chevauchement_chemin_complet(entree, mots_texte):
    """
    Compte combien de mots distincts du CHEMIN COMPLET (tous les niveaux, pas
    juste la feuille) sont réellement présents dans le texte libre. Sert à
    valider qu'une branche plus profonde correspond vraiment à un mot mentionné
    (ex: "Midi" dans "robe midi"), plutôt que d'être profonde par hasard sur un
    critère non mentionné (ex: "sport" dans "Vêtements de sport" pour un simple
    "short chino" qui ne parle pas de sport).
    """
    mots_chemin = mots_normalises(entree.get("chemin", ""))
    return sum(1 for mc in mots_chemin if any(mots_correspondent(mc, mt) for mt in mots_texte))


def matcher_par_mots(texte_libre, index):
    """
    Une entree matche si TOUS ses mots ont une correspondance dans texte_libre.
    Departage en cas d'egalite, dans cet ordre :
      1. Le nombre de mots propres a la feuille (plus specifique = mieux)
      2. Le chevauchement de mots sur le CHEMIN COMPLET avec le texte libre
         (valide qu'une branche plus profonde correspond a un mot reellement
         mentionne, pas juste a une profondeur accidentelle)
      3. Le chemin le plus court (le choix le moins risque par defaut)
    """
    mots_texte = mots_normalises(texte_libre)
    if not mots_texte:
        return None

    candidats = [e for e in index if e["mots"] and tous_les_mots_trouves(e["mots"], mots_texte)]

    # Filet de secours : Gemini colle parfois deux mots d'un nom en un seul
    # (ex: marque "Café Coton" lue "Cafecoton") -- le matching mot-à-mot
    # échoue alors car aucun mot seul de l'entrée ne matche le mot fusionné.
    # On retente en comparant le texte ENTIER normalisé (espaces/accents
    # supprimés) au nom ENTIER normalisé du candidat -- égalité stricte
    # (pas de préfixe), pour ne pas dégrader la précision du matching normal.
    # N'a d'effet que sur les index qui précisent "nom_colle" (les marques) --
    # ignoré silencieusement ailleurs (ex: catégories, qui n'ont pas ce champ).
    if not candidats:
        texte_colle = normaliser_mot(texte_libre)
        candidats = [e for e in index if e.get("nom_colle") == texte_colle]

    if not candidats:
        return None

    return max(
        candidats,
        key=lambda e: (
            len(e["mots"]),
            _chevauchement_chemin_complet(e, mots_texte),
            -e.get("chemin", "").count(">"),
        ),
    )


def construire_index_categories(categories_dict, genre=None, chemin=""):
    index = []
    for nom, noeud in categories_dict.items():
        genre_actuel = genre or nom
        nouveau_chemin = f"{chemin} > {nom}" if chemin else nom
        index.append({
            "nom": nom,
            "id": noeud["id"],
            "size_id": noeud.get("size_id"),
            "genre": genre_actuel,
            "mots": mots_normalises(nom),
            "chemin": nouveau_chemin,
            "a_des_enfants": bool(noeud.get("children")),
        })
        if noeud.get("children"):
            index.extend(construire_index_categories(noeud["children"], genre_actuel, nouveau_chemin))
    return index


def matcher_categorie(description_libre, genre, index_categories):
    """
    Priorité stricte aux FEUILLES (catégories sans enfants) : ce sont les
    seules que le champ de recherche du formulaire Vinted propose réellement
    comme résultat sélectionnable. Une catégorie "branche" (avec enfants,
    ex: "Shorts" qui contient "Shorts cargo", "Shorts en jean"...) existe dans
    l'arbre mais n'apparaît jamais comme résultat direct de recherche -- la
    résoudre casserait la sélection automatique dans le formulaire.
    """
    candidats_genre = [e for e in index_categories if e["genre"] == genre]

    feuilles = [e for e in candidats_genre if not e.get("a_des_enfants")]
    resultat = matcher_par_mots(description_libre, feuilles)
    if resultat:
        return resultat

    # Filet de secours si aucune feuille ne matche (rare) : on retente sur tout,
    # au moins pour renseigner la donnée -- le script de remplissage échouera
    # probablement à la sélectionner via la recherche dans ce cas, mais c'est
    # préférable à ne rien avoir du tout côté résolution/affichage.
    return matcher_par_mots(description_libre, candidats_genre)


def enfants_directs(branche, index_categories):
    """
    Retourne la liste des entrées correspondant aux enfants DIRECTS (un seul
    niveau plus profond) d'une branche donnée -- sert à proposer une liste
    fermée de sous-catégories réelles à faire trancher par Gemini.
    """
    profondeur_enfant = branche["chemin"].count(">") + 1
    prefixe = branche["chemin"] + " > "
    return [
        e for e in index_categories
        if e["chemin"].startswith(prefixe) and e["chemin"].count(">") == profondeur_enfant
    ]


def trouver_enfant_autres(branche, index_categories):
    """
    Cherche, parmi les enfants DIRECTS d'une branche, une feuille nommée
    "Autres ..." (catch-all que Vinted prévoit systématiquement pour les cas
    où aucun sous-type précis ne correspond, ex: "Autres T-shirts",
    "Autres shorts"). Retourne cette feuille si trouvée, sinon None.
    """
    for e in enfants_directs(branche, index_categories):
        if not e.get("a_des_enfants") and e["nom"].lower().startswith("autres"):
            return e
    return None


def matcher_taille(taille_libre, tailles_pour_size_id):
    """tailles_pour_size_id : dict {libelle: id} (ex: referentiels['tailles']['4'])."""
    taille_norm = normaliser_mot(taille_libre)
    for libelle, id_taille in tailles_pour_size_id.items():
        parties = [normaliser_mot(p) for p in re.split(r'[/,]', libelle)]
        if taille_norm in parties:
            return libelle, id_taille
    return None


# --- Index construits une seule fois au chargement du module ---
_data = charger_referentiels()
INDEX_MARQUES = construire_index_marques(_data["marques"])
INDEX_CATEGORIES = construire_index_categories(_data["categories"])