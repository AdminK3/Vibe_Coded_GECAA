# SPDX-License-Identifier: Apache-2.0
"""Measure types for the heavy-rain chain: trough, green roof, unsealing, trees.

Each kind changes the runoff coefficient grid (persistent change) and/or adds a storage that is filled by the
runoff of its cells. Values and sources are listed in ASSUMPTIONS; heat notes are qualitative only.
"""

from __future__ import annotations

import numpy as np

KINDS = ("mulde", "gruendach", "entsiegelung", "baeume")
GREEN_ROOF_C = {"extensiv": 0.5, "intensiv": 0.3}   # DIN 1986-100 Tab. 9 (angelehnt)
UNSEALED_C = 0.3                                    # Rasen / Grünfläche
TREE_INTERCEPTION_MM = 1.0                          # Kronenspeicher je m² Kronenfläche

ASSUMPTIONS = {
    "mulde": {"id": "A8", "art": "Annahme", "quelle": "GECAA",
              "text": "Mulde speichert Regen der zugeordneten Dächer und der eigenen Fläche bis zu Fläche × "
                      "Einstautiefe; Bemessung nach DWA-A 138-1 ist damit nicht ersetzt."},
    "gruendach": {"id": "A13", "art": "Normwert (angelehnt)", "quelle": "DIN 1986-100, Tab. 9 (angelehnt)",
                  "text": "Gründach: Abflussbeiwert der zugeordneten Dachflächen 0,5 (extensiv) bzw. 0,3 (intensiv) "
                          "statt 1,0; Speicher der Substratschicht nicht gesondert angesetzt."},
    "entsiegelung": {"id": "A14", "art": "Normwert (angelehnt)", "quelle": "DIN 1986-100, Tab. 9 (angelehnt)",
                     "text": f"Entsiegelung: Abflussbeiwert in der Fläche {UNSEALED_C:g} (Rasen), "
                             "höhere Werte werden ersetzt."},
    "baeume": {"id": "A15", "art": "Annahme, Literaturwert",
               "quelle": "Xiao & McPherson (2002), Urban Ecosystems 6; Kronenspeicher ca. 0,5–2 mm",
               "text": f"Bäume: Interzeptionsspeicher {TREE_INTERCEPTION_MM:g} mm über der Kronenfläche "
                       f"(eingezeichnete Kronenfläche; ohne Fläche keine Interzeption), dazu optional Baumrigolen-Speicher "
                       "je Baum (Eingabe). Wirkt nur auf den Regen über der Fläche."},
}

HEAT_NOTES = {
    "mulde": "Qualitative Einschätzung: Feuchte Mulden mit Bewuchs kühlen lokal durch Verdunstung; die Wirkung ist klein und kleinräumig.",
    "gruendach": "Qualitative Einschätzung: Gründächer senken vor allem die Oberflächentemperatur des Dachs und "
                 "verbessern das Raumklima darunter; auf die gefühlte Temperatur im Straßenraum wirken sie wenig.",
    "entsiegelung": "Qualitative Einschätzung: Entsiegelte, bewachsene Flächen heizen sich weniger auf als Asphalt und kühlen durch "
                    "Verdunstung; die Wirkung ist lokal.",
    "baeume": "Qualitative Einschätzung: Bäume senken durch Schatten und Verdunstung die Oberflächen- und gefühlte "
              "Temperatur in ihrem Kronenbereich; die Wirkung ist hier nicht beziffert.",
}


def apply_static(kind: str, variant: str | None, coeff: np.ndarray, mask: np.ndarray) -> None:
    """Persistent coefficient change of green roofs and unsealing (in place)."""
    if kind == "gruendach":
        coeff[mask] = np.minimum(coeff[mask], GREEN_ROOF_C[variant or "extensiv"])
    elif kind == "entsiegelung":
        coeff[mask] = np.minimum(coeff[mask], UNSEALED_C)


def storage_m3(kind: str, area_m2: float, depth_m: float, trees: int | None, pit_m3: float) -> float:
    if kind == "mulde":
        return area_m2 * depth_m
    if kind == "baeume":
        crown = area_m2                                # only the drawn canopy area; no assumed crown size
        return crown * TREE_INTERCEPTION_MM / 1000.0 + (trees or 0) * pit_m3
    return 0.0
