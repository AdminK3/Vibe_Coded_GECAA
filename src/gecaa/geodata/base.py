# SPDX-License-Identifier: Apache-2.0
"""Geodata providers: where the site inputs come from.

A provider answers a few questions about a site in WGS84 GeoJSON:
the parcel at a point, the buildings around it, and for a planned system
location the soil permeability, the groundwater depth and the water
protection zone. Every answer is an InputValue with source and Datengüte,
or None when the provider has no data, which the logic then turns into OPEN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform
from pyproj import Transformer

from ..k3.model import InputValue, Quality

WGS84 = "EPSG:4326"
UTM32 = "EPSG:25832"

_to_utm = Transformer.from_crs(WGS84, UTM32, always_xy=True).transform
_to_wgs = Transformer.from_crs(UTM32, WGS84, always_xy=True).transform


def to_utm(geom: dict):
    return shp_transform(_to_utm, shape(geom))


def to_wgs(geom) -> dict:
    return mapping(shp_transform(_to_wgs, geom))


@dataclass
class MapLayer:
    """A layer the browser loads directly (WMS) - display only, not used in checks."""
    id: str
    label: str
    url: str
    layers: str
    attribution: str
    opacity: float = 0.6
    min_zoom: int = 12
    legend_url: str | None = None


@dataclass
class DataSource:
    id: str
    label: str
    provider: str
    licence: str
    url: str | None = None
    note: str | None = None


class GeodataProvider(Protocol):
    id: str
    label: str
    sources: list[DataSource]
    map_layers: list[MapLayer]

    def covers(self, lon: float, lat: float) -> bool: ...
    def parcel_at(self, lon: float, lat: float) -> dict | None: ...
    def buildings(self, bbox: tuple[float, float, float, float]) -> dict: ...
    def kf(self, location: dict) -> InputValue | None: ...
    def groundwater_depth(self, location: dict) -> InputValue | None: ...
    def protection_zone(self, location: dict) -> InputValue | None: ...


@dataclass
class UnitGeometry:
    """Location of a seepage system and the facts GECAA derives from geometry alone."""
    on_unbuilt_land: bool
    distance_to_building_m: float | None
    location_m2: float
    overlapping_buildings: list[str] = field(default_factory=list)


def geometry_facts(location: dict, buildings: dict) -> UnitGeometry:
    loc = to_utm(location)
    overlap, dist = [], None
    for f in buildings.get("features", []):
        b = to_utm(f["geometry"])
        if b.intersects(loc) and b.intersection(loc).area > 0.01:
            overlap.append(str(f.get("properties", {}).get("id", "")))
        d = b.distance(loc)
        dist = d if dist is None else min(dist, d)
    return UnitGeometry(on_unbuilt_land=not overlap, distance_to_building_m=dist, location_m2=loc.area,
                        overlapping_buildings=overlap)


def geometry_inputs(facts: UnitGeometry, source: str, search_radius_m: float = 60.0) -> dict[str, InputValue]:
    return {
        "LUB": InputValue(abbr="LUB", value=facts.on_unbuilt_land, source=source, level="unit", quality=Quality.hoch,
                          note=("überschneidet Gebäude " + ", ".join(facts.overlapping_buildings)) if facts.overlapping_buildings else None),
        "ABST": InputValue(abbr="ABST", unit="m", source=source, level="unit", quality=Quality.hoch,
                           value=round(facts.distance_to_building_m, 1) if facts.distance_to_building_m is not None else search_radius_m,
                           note=None if facts.distance_to_building_m is not None else
                           f"kein Gebäude im Umkreis von {search_radius_m:g} m – Abstand mindestens {search_radius_m:g} m"),
    }
