"""
Vérification de similarité visuelle (CLIP local, CPU) en second passage sur les
groupes déjà formés par grouper_photos_par_article (clustering temporel). Ne
remplace pas le seuil temporel -- le complète, pour rattraper les deux erreurs
qu'aucun seuil de temps seul ne peut résoudre :
  - sur-regroupement : deux articles différents pris à quelques secondes d'écart
  - sous-regroupement : un seul article coupé en deux par une pause plus longue
    que le seuil, à un autre moment de la séance

Aucun appel API -- modèle local, pas de conflit avec le quota Gemini.
"""
import numpy as np
import torch
import open_clip
from PIL import Image

# Seuil de similarité cosinus au-dessus duquel deux photos sont considérées
# comme montrant le même article. À calibrer sur de vrais exemples, comme pour
# SEUIL_ECART_GROUPE_SECONDES -- valeur de départ raisonnable pour CLIP ViT-B-32.
SEUIL_SIMILARITE_MEME_ARTICLE = 0.80

_model = None
_preprocess = None


def _charger_modele():
    """Chargement paresseux -- seulement au premier appel, pas au démarrage du backend."""
    global _model, _preprocess
    if _model is None:
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        _model.eval()
    return _model, _preprocess


def calculer_embedding(chemin_photo):
    """Vecteur visuel normalisé (norme 1) pour UNE photo."""
    model, preprocess = _charger_modele()
    image = preprocess(Image.open(chemin_photo).convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        embedding = model.encode_image(image)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)
    return embedding.squeeze(0).numpy()


def _centroide(embeddings):
    """Moyenne normalisée d'un ensemble d'embeddings -- représente 'l'apparence
    moyenne' d'un groupe de photos du même article."""
    moyenne = np.mean(embeddings, axis=0)
    return moyenne / np.linalg.norm(moyenne)


def affiner_groupes_par_similarite(groupes_temporels, seuil=SEUIL_SIMILARITE_MEME_ARTICLE):
    """
    groupes_temporels : liste de groupes, chaque groupe = liste de (chemin, heure, source)
    (format identique à la sortie de grouper_photos_par_article).

    Retourne la liste affinée après scission des groupes hétérogènes et fusion
    des groupes/singletons visuellement très proches.
    """
    # 1. Calcul des embeddings pour toutes les photos, une seule fois
    tous_chemins = [chemin for groupe in groupes_temporels for chemin, _, _ in groupe]
    embeddings = {chemin: calculer_embedding(chemin) for chemin in tous_chemins}

    # 2. SCISSION : à l'intérieur de chaque groupe temporel, si une photo est trop
    # dissemblable du reste du groupe, on l'isole dans un sous-groupe séparé.
    groupes_scindes = []
    for groupe in groupes_temporels:
        if len(groupe) <= 1:
            groupes_scindes.append(groupe)
            continue

        sous_groupes = []
        for chemin, heure, source in groupe:
            embedding = embeddings[chemin]
            meilleur_sous_groupe = None
            meilleure_similarite = -1
            for sg in sous_groupes:
                centroid = _centroide([embeddings[c] for c, _, _ in sg])
                sim = float(np.dot(embedding, centroid))
                if sim > seuil and sim > meilleure_similarite:
                    meilleur_sous_groupe = sg
                    meilleure_similarite = sim
            if meilleur_sous_groupe is not None:
                meilleur_sous_groupe.append((chemin, heure, source))
            else:
                sous_groupes.append([(chemin, heure, source)])

        groupes_scindes.extend(sous_groupes)

    # 3. FUSION : deux groupes scindés (même issus de groupes temporels différents)
    # dont les centroïdes sont très proches sont probablement le même article,
    # malgré un écart de temps qui les avait séparés au passage 1.
    groupes_fusionnes = []
    for groupe in groupes_scindes:
        centroid_groupe = _centroide([embeddings[c] for c, _, _ in groupe])
        meilleur_cible = None
        meilleure_similarite = -1
        for cible in groupes_fusionnes:
            centroid_cible = _centroide([embeddings[c] for c, _, _ in cible])
            sim = float(np.dot(centroid_groupe, centroid_cible))
            if sim > seuil and sim > meilleure_similarite:
                meilleur_cible = cible
                meilleure_similarite = sim
        if meilleur_cible is not None:
            meilleur_cible.extend(groupe)
        else:
            groupes_fusionnes.append(list(groupe))

    return groupes_fusionnes