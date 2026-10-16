# SPDX-License-Identifier: Apache-2.0
# Scales and steps follow the CLIMAAX Climate Risk Assessment handbook (crabook, Key Risk Assessment protocol),
# https://github.com/CLIMAAX/crabook, licensed Apache-2.0 OR CC-BY-4.0. See NOTICE.
"""CLIMAAX Climate Risk Assessment (CRA): five steps and the Key Risk Assessment (KRA) with 1-4 scales.

CLIMAAX defines the scales but leaves the priority to a qualitative stakeholder discussion. GECAA proposes
values from its analyses ("Vorschlag (GECAA-Heuristik)"); every value stays editable in the interface.
"""

from __future__ import annotations

CRA_STEPS = [
    ("scoping", "Scoping", "Ziele, Betrachtungsraum, Beteiligte und Rahmenbedingungen festlegen."),
    ("exploration", "Risikoerkundung", "Relevante Gefahren und vorhandene Daten sichten (Karten, Messreihen)."),
    ("analysis", "Risikoanalyse", "Gefahr × Exposition × Vulnerabilität quantifizieren (Starkregen, Hitze)."),
    ("key_risks", "Bewertung der Schlüsselrisiken", "Schwere heute/künftig, Dringlichkeit, Resilienz, Priorität."),
    ("monitoring", "Monitoring", "Indikatoren festlegen und Wirkung der Maßnahmen beobachten."),
]

SEVERITY = {1: "begrenzt", 2: "mäßig", 3: "erheblich", 4: "kritisch"}
URGENCY = {1: "kein Handlungsbedarf", 2: "beobachten", 3: "weiteres Handeln nötig", 4: "sofortiges Handeln nötig"}
CAPACITY = {1: "gering (keine Maßnahmen)", 2: "mittel", 3: "beachtlich", 4: "hoch (umfassendes Maßnahmenbündel)"}
PRIORITY = {1: "niedrig", 2: "mittel", 3: "hoch", 4: "sehr hoch"}
SOURCE = "CLIMAAX Handbook, Key Risk Assessment protocol (Skalen 1–4)"


def _clamp(v: float) -> int:
    return int(max(1, min(4, round(v))))


def rain_severity(buildings: list[dict], rp: str, threshold_m: float) -> int:
    over = [b["depth"].get(rp, 0) - threshold_m for b in buildings]
    affected = sum(1 for d in over if d > 0)
    deep = max(over, default=0)
    if deep >= 0.5 or affected >= 10:
        return 4
    if deep >= 0.3 or affected >= 3:
        return 3
    if affected >= 1:
        return 2
    return 1


def priority(sev_now: int, sev_future: int, urgency: int, capacity: int) -> int:
    """GECAA heuristic (CLIMAAX leaves priority to qualitative discussion)."""
    return _clamp(max(sev_now, sev_future) * 0.5 + urgency * 0.5 - (capacity - 1) * 0.5)


def suggest(rain: dict | None, heat: dict | None, capacity: int = 2) -> list[dict]:
    rows = []
    if rain:
        thr = rain.get("threshold_m", 0.10)
        rp_max = str(max(rain["rps"]))
        now = rain_severity(rain["buildings"], rp_max, thr)
        fut_b = rain.get("buildings_future") or rain["buildings"]
        fut = max(now, rain_severity(fut_b, rp_max, thr))
        urg = _clamp(now + (1 if fut > now else 0) - (1 if now <= 1 else 0))
        rows.append({"hazard": "Starkregen", "severity_now": now, "severity_future": fut, "urgency": urg,
                     "capacity": capacity, "priority": priority(now, fut, urg, capacity),
                     "basis": f"Wassertiefen an Gebäuden bei {rp_max}-jährlichem Regen"
                              + (" (heute und +2 K)" if rain.get("buildings_future") else " (nur heute berechnet)")})
    if heat:
        ca = heat.get("climate_analysis") or {}
        pet = ca.get("pet_typisch")
        now = 1 if pet is None else (4 if pet >= 41 else 3 if pet >= 35 else 2 if pet >= 29 else 1)
        hd = heat.get("hot_days") or {}
        trend = hd.get("trend_per_decade") or 0
        fut = _clamp(now + (1 if trend > 1 else 0))
        urg = _clamp(now - 1 + (1 if trend > 1 else 0))
        basis = []
        if pet is not None:
            basis.append(f"PET typischer Sommertag {pet:.0f} °C ({ca.get('pet_typisch_klasse')})")
        if hd:
            basis.append(f"heiße Tage {hd.get('mean_last10')} /Jahr (letzte 10 Jahre), Trend {trend:+.1f} je Jahrzehnt")
        rows.append({"hazard": "Hitze", "severity_now": now, "severity_future": fut, "urgency": urg,
                     "capacity": capacity, "priority": priority(now, fut, urg, capacity),
                     "basis": "; ".join(basis) or "keine Daten"})
    return rows


def scales() -> dict:
    return {"steps": [{"id": i, "title": t, "text": x} for i, t, x in CRA_STEPS], "severity": SEVERITY,
            "urgency": URGENCY, "capacity": CAPACITY, "priority": PRIORITY, "source": SOURCE}
