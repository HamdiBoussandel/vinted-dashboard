import os
from PIL import Image

def compress_photo(image_path, max_size_mo=9.0, min_quality=40, quality_step=5):
    """
    Compresse une photo en place tant qu'elle dépasse max_size_mo.
    Stratégie en 2 temps :
      1. Réduction progressive de la qualité JPEG (95 -> min_quality)
      2. Si ça ne suffit pas, réduction des dimensions (redimensionnement) en boucle
    Retourne un dict décrivant ce qui a été fait, pour du logging propre.
    """
    max_bytes = max_size_mo * 1024 * 1024
    current_size = os.path.getsize(image_path)

    if current_size <= max_bytes:
        return {"compressed": False, "reason": "déjà sous la limite", "size_mo": round(current_size / 1024 / 1024, 2)}

    img = Image.open(image_path)
    # Les PNG avec transparence (mode RGBA) plantent en JPEG -> on convertit d'abord
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # --- Phase 1 : on joue sur la qualité de compression JPEG ---
    quality = 95
    while quality >= min_quality:
        img.save(image_path, "JPEG", quality=quality, optimize=True)
        new_size = os.path.getsize(image_path)
        if new_size <= max_bytes:
            return {
                "compressed": True,
                "method": "quality",
                "final_quality": quality,
                "size_mo": round(new_size / 1024 / 1024, 2),
            }
        quality -= quality_step

    # --- Phase 2 : la qualité seule ne suffit pas -> on redimensionne aussi ---
    scale = 0.9
    while scale > 0.3:
        w, h = img.size
        resized = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        resized.save(image_path, "JPEG", quality=85, optimize=True)
        new_size = os.path.getsize(image_path)
        if new_size <= max_bytes:
            return {
                "compressed": True,
                "method": "resize",
                "final_scale": round(scale, 2),
                "size_mo": round(new_size / 1024 / 1024, 2),
            }
        img = resized  # on repart de l'image déjà réduite pour la prochaine itération
        scale -= 0.1

    return {"compressed": False, "reason": "impossible de descendre sous la limite malgré compression + resize"}

def compress_folder(folder_path, max_size_mo=9.0):
    """
    Parcourt folder_path et compresse toutes les images qui dépassent max_size_mo.
    Ne descend pas dans les sous-dossiers (pas besoin ici, Syncthing met tout à plat).
    Retourne un résumé : {"traitees": [...], "deja_ok": [...], "erreurs": [...]}
    """
    extensions_valides = (".jpg", ".jpeg", ".png")
    resume = {"traitees": [], "deja_ok": [], "erreurs": []}

    if not os.path.isdir(folder_path):
        print(f"❌ Dossier introuvable : {folder_path}")
        return resume

    fichiers = [f for f in os.listdir(folder_path) if f.lower().endswith(extensions_valides)]
    print(f"📂 {len(fichiers)} photo(s) trouvée(s) dans {folder_path}")

    for nom_fichier in fichiers:
        chemin_complet = os.path.join(folder_path, nom_fichier)
        try:
            result = compress_photo(chemin_complet, max_size_mo=max_size_mo)
            if result["compressed"]:
                print(f"✅ {nom_fichier} : compressée -> {result['size_mo']} Mo ({result['method']})")
                resume["traitees"].append(nom_fichier)
            else:
                print(f"⏭️  {nom_fichier} : {result['reason']} ({result['size_mo']} Mo)")
                resume["deja_ok"].append(nom_fichier)
        except Exception as e:
            print(f"🚨 {nom_fichier} : erreur -> {e}")
            resume["erreurs"].append({"fichier": nom_fichier, "erreur": str(e)})

    print(f"\n🏁 Terminé : {len(resume['traitees'])} compressée(s), {len(resume['deja_ok'])} déjà OK, {len(resume['erreurs'])} erreur(s)")
    return resume


if __name__ == "__main__":
    # Usage : python photo_compressor.py "C:\chemin\vers\dossier"
    import sys
    if len(sys.argv) < 2:
        print("Usage : python photo_compressor.py chemin/vers/dossier")
        sys.exit(1)

    compress_folder(sys.argv[1])