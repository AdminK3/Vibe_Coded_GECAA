# SPDX-License-Identifier: Apache-2.0
"""Assess a site: evaluation units (locations + catchments) -> inputs -> K3 logic -> site result."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from shapely.geometry import shape

from ..geodata.base import GeodataProvider, geometry_facts, geometry_inputs, to_utm
from .engine import assess, evaluate_unit
from .model import InputValue, LogicSpec, Quality, SiteResult

SEARCH_RADIUS_M = 60.0
MAX_UNITS = 20
MAX_LOCATION_M2 = 5000.0      # a single trough location; larger areas are split into several units
MAX_VERTICES = 500
# Plausible ranges for user numbers; outside them the value is rejected (422), not guessed.
RANGES = {"TA": (0.0, 10.0), "KT": (0.0, 10.0), "GWF": (0.0, 100.0), "KF": (1e-10, 1.0), "ABST": (0.0, 10000.0)}


class RequestError(ValueError):
    """Invalid request content; the API answers 422."""


class CatchmentSurface(BaseModel):
    id: str
    kind: Literal["roof", "paved", "other"] = "roof"
    area_m2: float | None = None
    geometry: dict | None = None
    label: str | None = None


class UnitRequest(BaseModel):
    id: str
    name: str
    location: dict = Field(..., description="GeoJSON Polygon (WGS84) of the planned system location")
    catchment: list[CatchmentSurface] = Field(default_factory=list, max_length=200)
    inputs: dict[str, Any] = Field(default_factory=dict, description="User values per input abbreviation, e.g. TA, KF (measured)")

    @field_validator("location")
    @classmethod
    def _polygon(cls, geom: dict) -> dict:
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("Der Anlagenstandort muss eine Fläche (Polygon) sein.")
        try:
            g = shape(geom)
        except Exception as exc:
            raise ValueError("Ungültige Geometrie.") from exc
        coords = geom.get("coordinates", [])
        n = sum(len(r) for r in coords) if geom["type"] == "Polygon" else sum(len(r) for p in coords for r in p)
        if n > MAX_VERTICES:
            raise ValueError(f"Zu viele Stützpunkte (höchstens {MAX_VERTICES}).")
        if g.is_empty or not all(math.isfinite(v) for v in g.bounds):
            raise ValueError("Leere oder ungültige Geometrie.")
        if not (-180 <= g.bounds[0] <= 180 and -90 <= g.bounds[1] <= 90 and -180 <= g.bounds[2] <= 180 and -90 <= g.bounds[3] <= 90):
            raise ValueError("Koordinaten müssen WGS84 (Länge, Breite) sein.")
        area = to_utm(geom).area
        if area <= 0.01:
            raise ValueError("Der Anlagenstandort hat keine Fläche.")
        if area > MAX_LOCATION_M2:
            raise ValueError(f"Der Anlagenstandort ist größer als {MAX_LOCATION_M2:g} m²; bitte in mehrere Standorte teilen.")
        return geom


class AssessmentRequest(BaseModel):
    logic_id: str = "k3_dwa_a_138_1_mre"
    site_name: str | None = None
    parcel: dict | None = None
    units: list[UnitRequest] = Field(..., max_length=MAX_UNITS)
    site_inputs: dict[str, Any] = Field(default_factory=dict, description="Inputs answered once, e.g. ALT, KT")


# Where a user value comes from determines its Datengüte (K3 report convention).
USER_QUALITY = {
    "KF": Quality.hoch,      # measured infiltration test
    "GWF": Quality.hoch,     # monitoring well / authority statement
    "TA": Quality.annahme_offen,
    "KT": Quality.annahme_offen,
    "ALT": Quality.annahme_belegt,
    "WSG": Quality.hoch,
}


def _user_value(spec: LogicSpec, abbr: str, raw: Any, level: str) -> InputValue | None:
    if raw is None or raw == "":
        return None
    ispec = next((i for i in spec.inputs if i.abbr == abbr), None)
    if ispec is None:
        raise RequestError(f"Unbekannte Eingabe {abbr!r}.")
    value: Any = raw
    if ispec.kind == "number":
        try:
            value = float(str(raw).replace(",", "."))
        except ValueError:
            raise RequestError(f"{ispec.label}: {raw!r} ist keine Zahl.") from None
        lo, hi = RANGES.get(abbr, (-math.inf, math.inf))
        if not math.isfinite(value) or not lo <= value <= hi:
            raise RequestError(f"{ispec.label}: {raw!r} liegt außerhalb des zulässigen Bereichs ({lo:g} bis {hi:g}).")
    elif ispec.kind == "choice":
        if str(raw) not in ispec.choices:
            raise RequestError(f"{ispec.label}: {raw!r} ist keine zulässige Auswahl.")
        value = str(raw)
    elif ispec.kind == "bool":
        value = raw in (True, "true", "ja", "1", 1)
    return InputValue(abbr=abbr, value=value, unit=ispec.unit, source="Eingabe", level=level or ispec.level,
                      quality=USER_QUALITY.get(abbr, Quality.annahme_offen))


def _area(geom: dict | None) -> float | None:
    return round(to_utm(geom).area, 1) if geom else None


def run_assessment(spec: LogicSpec, provider: GeodataProvider, req: AssessmentRequest) -> SiteResult:
    units = []
    site_inputs: dict[str, InputValue] = {}
    for abbr, raw in req.site_inputs.items():
        iv = _user_value(spec, abbr, raw, None)
        if iv:
            site_inputs[abbr] = iv

    for u in req.units:
        loc = u.location
        c = shape(loc).centroid
        # buildings around the location, in degrees (approx. SEARCH_RADIUS_M)
        dlat = SEARCH_RADIUS_M / 111_000
        dlon = dlat / max(0.2, abs(math.cos(math.radians(c.y))))
        minx, miny, maxx, maxy = shape(loc).bounds
        values: dict[str, InputValue] = {}
        if not provider.covers(c.x, c.y):
            # outside the data area nothing may be read from the map: every map input stays missing -> OPEN
            note = f"Standort außerhalb des Datengebiets ({provider.label})"
            for abbr in ("LUB", "ABST", "KF", "GWF", "WSG"):
                values[abbr] = InputValue(abbr=abbr, value=None, source=provider.label, note=note, level="unit")
            facts = None
        else:
            try:
                bld = provider.buildings((minx - dlon, miny - dlat, maxx + dlon, maxy + dlat))
                facts = geometry_facts(loc, bld)
                values.update(geometry_inputs(facts, f"{provider.label}: Gebäudeumringe", SEARCH_RADIUS_M))
            except Exception as exc:  # building service down: LUB and ABST stay missing
                facts = None
                for abbr in ("LUB", "ABST"):
                    values[abbr] = InputValue(abbr=abbr, value=None, source=f"{provider.label}: Gebäudedienst nicht erreichbar",
                                              note=str(exc)[:200], level="unit")
        for abbr, getter in (("KF", provider.kf), ("GWF", provider.groundwater_depth), ("WSG", provider.protection_zone)):
            if abbr in values:
                continue
            try:
                iv = getter(loc)
            except Exception as exc:  # a failing service must not break the assessment: the input stays missing
                iv = InputValue(abbr=abbr, value=None, source=f"{provider.label}: Fehler beim Abruf",
                                note=str(exc)[:200], level="unit")
            values[abbr] = iv or InputValue(abbr=abbr, value=None, source=provider.label, note="keine Daten", level="unit")
        values.update(site_inputs)
        for abbr, raw in u.inputs.items():           # user values win over map values
            iv = _user_value(spec, abbr, raw, "unit")
            if iv:
                values[abbr] = iv
        for i in spec.inputs:                          # every input appears, missing ones as None
            values.setdefault(i.abbr, InputValue(abbr=i.abbr, value=None, unit=i.unit, level=i.level))
        catchment = sum((s.area_m2 if s.area_m2 is not None else (_area(s.geometry) or 0)) for s in u.catchment) or None
        units.append(evaluate_unit(spec, u.id, u.name, values, geometry=loc,
                                   catchment_m2=catchment, location_m2=round(to_utm(loc).area, 1)))

    sources = [s.__dict__ for s in provider.sources]
    return assess(spec, units, list(site_inputs.values()), sources)
