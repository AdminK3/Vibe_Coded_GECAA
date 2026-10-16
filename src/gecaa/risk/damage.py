# SPDX-License-Identifier: Apache-2.0
# Portions adapted from the CLIMAAX FLOODS workflow (Risk_assessment_FLOOD_BUILDING_POPULATION.ipynb),
# https://github.com/CLIMAAX/FLOODS, licensed Apache-2.0 OR CC-BY-4.0. See NOTICE.
"""Building exposure and flood damage (JRC depth-damage curves, CLIMAAX maximum damage values).

Also the fallback impact engine: the same calculation CLIMADA's ImpactCalc does for this setup
(depth -> mean damage ratio -> damage per building; banded event frequencies -> expected annual damage),
so results can be cross-checked against the CLIMADA worker.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import data_dir

# CLIMAAX FLOODS: "Define arrays for damage values based on 2010 estimates" (Huizinga et al. 2017, EU values)
CPI2010, CPI2022 = 100.0, 121.8
CPI_FRAC = CPI2022 / CPI2010
MAX_DAMAGE_2010 = {                     # EUR per m2: [structure, contents]
    "residential": (480.0, 240.0),
    "commercial": (502.0, 502.0),
    "industrial": (328.0, 492.0),
}
MAX_DAMAGE_2010["universal"] = tuple(np.mean([MAX_DAMAGE_2010[k] for k in ("residential", "commercial", "industrial")], axis=0))
CLASS_DE = {"residential": "Wohnen", "commercial": "Gewerbe/Handel", "industrial": "Industrie/Lager", "universal": "sonstige"}
CURVE_COLUMN = {"residential": "residential", "commercial": "commercial", "industrial": "indusrial"}

SOURCE_JRC = ("Huizinga, de Moel & Szewczyk (2017): Global flood depth-damage functions, EUR 28552 EN "
              "(europäische Kurven), übernommen aus dem CLIMAAX-Workflow FLOODS")


def load_curves(path: Path | None = None) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    path = path or data_dir() / "data" / "climaax" / "JRC_damage_curves.csv"
    rows = [r for r in csv.reader(l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("#"))]
    head = [h.strip() for h in rows[0]]
    data = np.array([[float(x) for x in r] for r in rows[1:]])
    depth = data[:, 0]
    curves = {cls: (depth, data[:, head.index(col)]) for cls, col in CURVE_COLUMN.items()}
    d, _ = curves["residential"]
    curves["universal"] = (d, np.mean([curves[c][1] for c in CURVE_COLUMN], axis=0))
    return curves


def classify(function_text: str | None) -> str:
    """ALKIS building function text -> damage class."""
    t = (function_text or "").lower()
    if any(k in t for k in ("wohn", "residential")):
        return "residential"
    if any(k in t for k in ("industrie", "produktion", "lager", "werkstatt", "fabrik", "landwirt", "scheune")):
        return "industrial"
    if any(k in t for k in ("handel", "gewerbe", "büro", "buero", "dienstleist", "geschäft", "gaststätte", "hotel",
                            "verwaltung", "bank", "schule", "kirche", "öffentlich", "kranken", "kinder", "sport")):
        return "commercial"
    return "universal"


@dataclass
class Building:
    id: str
    label: str
    cls: str
    footprint_m2: float
    x: float                      # centroid, EPSG:25832
    y: float
    value_eur: float              # max damage = footprint x (structure + contents) x CPI factor
    geometry: dict | None = None  # WGS84 GeoJSON for the map
    depth_by_event: dict = field(default_factory=dict)
    lonlat: tuple = (0.0, 0.0)


def building_value(cls: str, footprint_m2: float) -> float:
    s, c = MAX_DAMAGE_2010[cls]
    return float(footprint_m2 * (s + c) * CPI_FRAC)


def mdr(curves, cls: str, depth: np.ndarray | float) -> np.ndarray:
    d, f = curves[cls]
    return np.interp(np.clip(depth, 0, d[-1]), d, f)


def band_frequencies(rps: list[float]) -> np.ndarray:
    """CLIMADA convention for return-period events: f_i = 1/RP_i - 1/RP_(i+1), the last one 1/RP_n."""
    rp = np.asarray(sorted(rps), dtype=float)
    inv = 1.0 / rp
    return inv - np.append(inv[1:], 0.0)


def fallback_impact(curves, buildings: list[Building], events: list[str], rps: list[float]) -> dict:
    """Damage per event and building, expected annual damage (EAD) with banded frequencies."""
    order = np.argsort(rps)
    rps_sorted = [rps[i] for i in order]
    ev_sorted = [events[i] for i in order]
    freq = band_frequencies(rps_sorted)
    per_event = {}
    eai = {}
    for b in buildings:
        dmg = np.array([mdr(curves, b.cls, b.depth_by_event.get(e, 0.0)) * b.value_eur for e in ev_sorted])
        eai[b.id] = float((dmg * freq).sum())
        for e, v in zip(ev_sorted, dmg):
            per_event.setdefault(e, 0.0)
            per_event[e] += float(v)
    return {"engine": "GECAA-Fallback (numpy, Methode wie CLIMADA ImpactCalc)", "version": "1",
            "aai_agg": float(sum(eai.values())), "eai": eai,
            "freq_curve": [{"rp": rp, "eur": per_event.get(e, 0.0)} for rp, e in zip(rps_sorted, ev_sorted)]}
