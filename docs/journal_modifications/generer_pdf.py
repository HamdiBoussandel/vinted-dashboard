"""
Génère le journal des modifications de VintedPro (PDF) à partir de
entrees.json -- une entrée par modification, la plus récente en premier dans
le sommaire. Pour documenter une nouvelle modification : ajouter une entrée
à entrees.json (même structure), puis relancer :

    python docs/journal_modifications/generer_pdf.py

Dépendance : reportlab (pip install reportlab). Polices Segoe UI de Windows.
"""
import json
import os
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

DOSSIER = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(DOSSIER, "VintedPro_journal_des_modifications.pdf")

# --- Polices --------------------------------------------------------------
POLICES = r"C:\Windows\Fonts"
pdfmetrics.registerFont(TTFont("Texte", os.path.join(POLICES, "segoeui.ttf")))
pdfmetrics.registerFont(TTFont("Texte-Gras", os.path.join(POLICES, "segoeuib.ttf")))
pdfmetrics.registerFont(TTFont("Texte-Italique", os.path.join(POLICES, "segoeuii.ttf")))
pdfmetrics.registerFont(TTFont("Texte-Leger", os.path.join(POLICES, "segoeuil.ttf")))
pdfmetrics.registerFontFamily("Texte", normal="Texte", bold="Texte-Gras", italic="Texte-Italique",
                              boldItalic="Texte-Gras")

# --- Couleurs -------------------------------------------------------------
ENCRE = colors.HexColor("#1E293B")
GRIS = colors.HexColor("#64748B")
GRIS_CLAIR = colors.HexColor("#E2E8F0")
FOND = colors.HexColor("#F8FAFC")
ACCENT = colors.HexColor("#0E7490")
ACCENT_CLAIR = colors.HexColor("#ECFEFF")
VERT = colors.HexColor("#047857")
ORANGE = colors.HexColor("#B45309")

MARGE = 18 * mm
LARGEUR_UTILE = A4[0] - 2 * MARGE

# --- Styles ---------------------------------------------------------------
S = {
    "couv_titre": ParagraphStyle("couv_titre", fontName="Texte-Gras", fontSize=30, leading=36, textColor=ENCRE),
    "couv_sous": ParagraphStyle("couv_sous", fontName="Texte-Leger", fontSize=14, leading=20, textColor=GRIS),
    "h_entree": ParagraphStyle("h_entree", fontName="Texte-Gras", fontSize=20, leading=25, textColor=ENCRE),
    "meta": ParagraphStyle("meta", fontName="Texte", fontSize=9, leading=13, textColor=GRIS),
    "h2": ParagraphStyle("h2", fontName="Texte-Gras", fontSize=12, leading=16, textColor=ACCENT,
                         spaceBefore=12, spaceAfter=5),
    "corps": ParagraphStyle("corps", fontName="Texte", fontSize=10, leading=14.5, textColor=ENCRE,
                            alignment=TA_LEFT),
    "puce": ParagraphStyle("puce", fontName="Texte", fontSize=10, leading=14.5, textColor=ENCRE,
                           leftIndent=12, bulletIndent=2, spaceAfter=2.5),
    "cellule": ParagraphStyle("cellule", fontName="Texte", fontSize=8.8, leading=11.5, textColor=ENCRE),
    "cellule_tete": ParagraphStyle("cellule_tete", fontName="Texte-Gras", fontSize=8.8, leading=11.5,
                                   textColor=colors.white),
    "resume": ParagraphStyle("resume", fontName="Texte", fontSize=10.5, leading=15.5, textColor=ENCRE),
    "sommaire": ParagraphStyle("sommaire", fontName="Texte", fontSize=10.5, leading=15, textColor=ENCRE),
}

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def date_longue(iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


EMOJIS = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+ ?")


def esc(texte):
    # Segoe UI n'a pas les emojis (carrés vides) : on les retire du PDF
    texte = EMOJIS.sub("", texte or "")
    texte = texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Espaces insécables de la typographie française : pas de « ou de : seul en bout de ligne
    for avant, apres in (("« ", "« "), (" »", " »"), (" :", " :"), (" ?", " ?"),
                         (" %", " %"), (" €", " €")):
        texte = texte.replace(avant, apres)
    return texte


def puces(elements, couleur=ACCENT):
    return [Paragraph(esc(e), S["puce"], bulletText="•") for e in elements]


def tableau(definition):
    largeurs = [LARGEUR_UTILE * l for l in definition["largeurs"]]
    donnees = [[Paragraph(esc(c), S["cellule_tete"]) for c in definition["colonnes"]]]
    for ligne in definition["lignes"]:
        donnees.append([Paragraph(esc(c), S["cellule"]) for c in ligne])
    t = Table(donnees, colWidths=largeurs, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, GRIS_CLAIR),
        ("BOX", (0, 0), (-1, -1), 0.4, GRIS_CLAIR),
    ]
    for i in range(1, len(donnees)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), FOND))
    t.setStyle(TableStyle(style))
    return t


def encadre(paragraphes, fond=ACCENT_CLAIR, bord=ACCENT):
    t = Table([[paragraphes]], colWidths=[LARGEUR_UTILE])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fond),
        ("LINEBEFORE", (0, 0), (0, -1), 3, bord),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def section(titre, contenu):
    """Titre gardé avec le début de son contenu (pas de titre orphelin en bas de page)."""
    contenu = contenu if isinstance(contenu, list) else [contenu]
    return [KeepTogether([Paragraph(esc(titre), S["h2"]), contenu[0]])] + contenu[1:]


def entree(e):
    histoire = [
        Paragraph(f"MODIFICATION N° {e['id']}  ·  {esc(e['categorie']).upper()}", ParagraphStyle(
            "kicker", parent=S["meta"], fontName="Texte-Gras", textColor=ACCENT, fontSize=8.5)),
        Spacer(1, 3),
        Paragraph(esc(e["titre"]), S["h_entree"]),
        Spacer(1, 4),
        Paragraph(f"{date_longue(e['date'])}  ·  Statut : <font color='{VERT.hexval()}'>{esc(e['statut'])}</font>"
                  + (f"<br/>Inspiration : {esc(e['inspiration'])}" if e.get("inspiration") else ""), S["meta"]),
        Spacer(1, 10),
        encadre([Paragraph(esc(e["resume"]), S["resume"])]),
    ]
    histoire += section("Pourquoi", puces(e["pourquoi"]))
    histoire += section("Ce qui change dans le dashboard", puces(e["visible"]))
    for t in e.get("tableaux", []):
        histoire += section(t["titre"], [tableau(t)])
    histoire += section("Fichiers modifiés", [tableau({
        "colonnes": ["Fichier", "Rôle"], "largeurs": [0.38, 0.62], "lignes": e["fichiers"]})])
    if e.get("champ"):
        histoire += section(e["champ"]["titre"], [tableau(e["champ"])])
    histoire += section("Vérifié", puces(e["verifie"]))
    if e.get("a_verifier"):
        histoire += section("Reste à vérifier", [encadre(puces(e["a_verifier"]), fond=colors.HexColor("#FFFBEB"),
                                                        bord=ORANGE)])
    if e.get("limites"):
        histoire += section("Limites connues", puces(e["limites"]))
    return histoire


def pied_de_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Texte", 8)
    canvas.setFillColor(GRIS)
    canvas.drawString(MARGE, 10 * mm, "VintedPro — Journal des modifications")
    canvas.drawRightString(A4[0] - MARGE, 10 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(GRIS_CLAIR)
    canvas.line(MARGE, 13 * mm, A4[0] - MARGE, 13 * mm)
    canvas.restoreState()


def couverture(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(ACCENT)
    canvas.rect(0, A4[1] - 12 * mm, A4[0], 12 * mm, stroke=0, fill=1)
    canvas.restoreState()


def main():
    with open(os.path.join(DOSSIER, "entrees.json"), encoding="utf-8") as f:
        entrees = sorted(json.load(f), key=lambda e: (e["date"], e["id"]))

    doc = SimpleDocTemplate(SORTIE, pagesize=A4, leftMargin=MARGE, rightMargin=MARGE,
                            topMargin=20 * mm, bottomMargin=20 * mm,
                            title="VintedPro — Journal des modifications", author="VintedPro")

    histoire = [
        Spacer(1, 40 * mm),
        Paragraph("VintedPro", ParagraphStyle("marque", parent=S["meta"], fontName="Texte-Gras", fontSize=11,
                                               textColor=ACCENT)),
        Spacer(1, 6),
        Paragraph("Journal des modifications", S["couv_titre"]),
        Spacer(1, 8),
        Paragraph("Ce qui a changé dans le dashboard, le backend et les automatisations : "
                  "pourquoi, comment, et ce qui reste à vérifier.", S["couv_sous"]),
        Spacer(1, 14 * mm),
        Paragraph(f"Édition du {date_longue(datetime.now().strftime('%Y-%m-%d'))}  ·  "
                  f"{len(entrees)} modification{'s' if len(entrees) > 1 else ''} documentée"
                  f"{'s' if len(entrees) > 1 else ''}", S["meta"]),
        Spacer(1, 10 * mm),
        Paragraph("Sommaire", S["h2"]),
    ]
    lignes = [[Paragraph(f"<b>N° {e['id']}</b>", S["sommaire"]),
               Paragraph(esc(e["titre"]), S["sommaire"]),
               Paragraph(esc(e["categorie"]), ParagraphStyle("c", parent=S["sommaire"], textColor=GRIS)),
               Paragraph(datetime.strptime(e["date"], "%Y-%m-%d").strftime("%d/%m/%Y"), S["sommaire"])]
              for e in reversed(entrees)]
    sommaire = Table(lignes, colWidths=[LARGEUR_UTILE * l for l in (0.1, 0.5, 0.24, 0.16)])
    sommaire.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRIS_CLAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    histoire.append(sommaire)

    for e in entrees:
        histoire.append(PageBreak())
        histoire += entree(e)

    doc.build(histoire, onFirstPage=couverture, onLaterPages=pied_de_page)
    print(f"PDF généré : {SORTIE} ({len(entrees)} entrée(s))")


if __name__ == "__main__":
    main()
