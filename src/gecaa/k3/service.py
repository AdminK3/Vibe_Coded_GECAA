# SPDX-License-Identifier: Apache-2.0
"""Assess a site: evaluation units (locations + catchments) -> inputs -> K3 logic -> site result."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from shapely.geometry import shape

from ..geodata.base import GeodataProvider, geometry_facts, geometry_inputs, to_utm
from .engine import assess, evaluate_unit
from .model import InputValue, LogicSpec, Quality, SiteResult

SEARCH_RADIUS_M = 60.0


class CatchmentSurface(BaseModel):
    id: str
    kind: Literal["roof", "paved", "other"] = "roof"
    area_m2: float | None = None
    geometry: dict | None = None
    label: str | None = None


class UnitRequest(BaseModel):
    id: str
    name: str
    location: dict = Field(..., description="GeoJSON geometry (WGS84) of the planned system location")
    catchment: list[CatchmentSurface] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict, description="User values per input abbreviation, e.g. TA, KF (measured)")


class AssessmentRequest(BaseModel):
    logic_id: str = "k3_dwa_a_138_1_mre"
    site_name: str | None = None
    parcel: dict | None = None
    units: list[UnitRequest]
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
    ispec = spec.input_spec(abbr)
    value: Any = raw
    if ispec.kind == "number":
        try:
            value = float(str(raw).replace(",", "."))
        except ValueError:
            return None
    elif ispec.kind == "bool":
        value = raw in (True, "true", "ja", "1", 1)
    return InputValue(abbr=abbr, value=value, unit=ispec.unit, source="Eingabe", level=level,
                      quality=USER_QUALITY.get(abbr, Quality.annahme_offen))


def _area(geom: dict | None) -> float | None:
    return round(to_utm(geom).area, 1) if geom else None


def run_assessment(spec: LogicSpec, provider: GeodataProvider, req: AssessmentRequest) -> SiteResult:
    units = []
    site_inputs: dict[str, InputValue] = {}
    for abbr, raw in req.site_inputs.items():
        iv = _user_value(spec, abbr, raw, spec.input_spec(abbr).level)
        if iv:
            site_inputs[abbr] = iv

    for u in req.units:
        loc = u.location
        c = shape(loc).centroid
        # buildings around the location, in degrees (approx. SEARCH_RADIUS_M)
        dlat = SEARCH_RADIUS_M / 111_000
        dlon = dlat / max(0.2, abs(__import__("math").cos(__import__("math").radians(c.y))))
        minx, miny, maxx, maxy = shape(loc).bounds
        bld = provider.buildings((minx - dlon, miny - dlat, maxx + dlon, maxy + dlat))
        facts = geometry_facts(loc, bld)
        values: dict[str, InputValue] = {}
        values.update(geometry_inputs(facts, f"{provider.label}: Gebäudeumringe", SEARCH_RADIUS_M))
        for abbr, getter in (("KF", provider.kf), ("GWF", provider.groundwater_depth), ("WSG", provider.protection_zone)):
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
                                   catchment_m2=catchment, location_m2=round(facts.location_m2, 1)))

    sources = [s.__dict__ for s in provider.sources]
    return assess(spec, units, list(site_inputs.values()), sources)
