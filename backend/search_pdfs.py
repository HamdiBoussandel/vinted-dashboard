import pdfplumber
from pathlib import Path

# 📂 Dossier contenant tes 100 PDF
DOSSIER_PDFS = Path(r"C:\Users\hamdi\Documents\Facture_Vinted")

# 🔍 Les termes que tu cherches (insensible à la casse)
TERMES_RECHERCHES = ["Nike", "Bonnet", "Tommy", "RL", "Ralph Lauren", "COS", "Champion", "mérinos", "T-Shirt"]

def rechercher_dans_pdfs(dossier, termes):
    resultats = {}

    for pdf_path in dossier.glob("*.pdf"):
        matches_fichier = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    texte = page.extract_text() or ""
                    texte_normalise = texte.casefold()

                    for terme in termes:
                        if terme.casefold() in texte_normalise:
                            matches_fichier.append((terme, i, texte))

        except Exception as e:
            print(f"⚠️ Erreur sur {pdf_path.name} : {e}")
            continue

        if matches_fichier:
            resultats[pdf_path.name] = matches_fichier

    return resultats


if __name__ == "__main__":
    resultats = rechercher_dans_pdfs(DOSSIER_PDFS, TERMES_RECHERCHES)

    if not resultats:
        print("Aucun match trouvé.")
    else:
        for fichier, matches in resultats.items():
            print(f"\n📄 {fichier}")
            for terme, page, texte in matches:
                print(f"\n   ✅ '{terme}' trouvé page {page}")
                print("   " + "-" * 50)
                for ligne in texte.splitlines():
                    print(f"   {ligne}")
                print("   " + "-" * 50)