from datetime import datetime

from services.risk_guard import report_suspension

# À éditer à chaque signalement : "note" est un texte libre, "detected_at" est
# la date ET L'HEURE RÉELLES de la restriction Vinted (pas l'heure d'exécution
# de ce script) -- c'est cette valeur qui fait démarrer le palier de
# convalescence, désormais très court (2j/5j/10j), donc sensible à la précision.
report_suspension(
    "Dressing 1",
    note="Restriction Vinted constatée le 04/09 vers 14h12, jusqu'au 05/09 annoncé",
    detected_at=datetime(2026, 9, 4, 14, 12),
)
print("Suspension enregistrée pour Dressing 1.")