# SPDX-License-Identifier: Apache-2.0
"""Offline provider on the synthetic demo dataset (data/samples). For tests and demos without internet."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_bounds
from shapely.geometry import Point, box, shape

from ..k3.model import InputValue, Quality
from ..spatial.layers import geojson_wgs84
from .base import UTM32, WGS84, DataSource, MapLayer, to_utm


class SyntheticProvider:
    id = "synthetic"
    label = "Synthetische Demodaten"

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.sources = [DataSource("synthetic", "Synthetische Demodaten (erfunden)", "GECAA", "CC0",
                                   note="Keine echten Messwerte. Nur für Tests und Vorführungen.")]
        self.map_layers: list[MapLayer] = []
        with rasterio.open(self.folder / "kf_log10.tif") as src:
            self._bounds = transform_bounds(src.crs, WGS84, *src.bounds)
        self._parcels = geojson_wgs84(self.folder / "parcels.geojson")
        self._buildings = geojson_wgs84(self.folder / "buildings.geojson")
        self._zones = geojson_wgs84(self.folder / "water_protection_zones.geojson")

    @property
    def bounds(self):
        return self._bounds

    def covers(self, lon, lat):
        w, s, e, n = self._bounds
        return w <= lon <= e and s <= lat <= n

    def parcel_at(self, lon, lat):
        pt = Point(lon, lat)
        for f in self._parcels["features"]:
            if shape(f["geometry"]).contains(pt):
                p = dict(f["properties"])
                return {"type": "Feature", "geometry": f["geometry"],
                        "properties": {"id": p["id"], "label": p["name"], "source": "synthetisch"}}
        return None

    def buildings(self, bbox):
        area = box(*bbox)
        feats = [f for f in self._buildings["features"] if shape(f["geometry"]).intersects(area)]
        return {"type": "FeatureCollection", "features": feats}

    def _values(self, raster: str, location: dict) -> np.ndarray:
        with rasterio.open(self.folder / raster) as src:
            data, _ = rio_mask(src, [to_utm(location).__geo_interface__], crop=True, all_touched=True, filled=False)
        v = data[0].compressed().astype("float64")
        return v[v != -9999.0]

    def kf(self, location):
        try:
            v = self._values("kf_log10.tif", location)
        except ValueError:
            return None
        if not v.size:
            return None
        mu, sd = float(v.mean()), math.hypot(float(v.std()), 0.5)
        return InputValue(abbr="KF", value=float(f"{10 ** mu:.2g}"), unit="m/s", distribution=("log10normal", mu, sd),
                          source="Synthetische Demodaten: kf-Raster", level="unit", quality=Quality.mittel,
                          note="erfundene Werte")

    def groundwater_depth(self, location):
        try:
            v = self._values("groundwater_depth.tif", location)
        except ValueError:
            return None
        if not v.size:
            return None
        return InputValue(abbr="GWF", value=round(float(v.min()), 2), unit="m",
                          distribution=("normal", float(v.mean()), math.hypot(float(v.std()), 0.5)),
                          source="Synthetische Demodaten: Grundwasser-Raster (kleinster Wert am Standort)",
                          level="unit", quality=Quality.mittel, note="erfundene Werte")

    def protection_zone(self, location):
        loc = shape(location)
        zones = [str(f["properties"].get("zone", "")).upper() for f in self._zones["features"]
                 if shape(f["geometry"]).intersects(loc)]
        order = ["I", "II", "IIIA", "IIIB", "III"]
        hit = next((z for z in order if z in zones), "kein")
        return InputValue(abbr="WSG", value=hit, source="Synthetische Demodaten: Wasserschutzzonen", level="unit",
                          quality=Quality.mittel, note="erfundene Zonen")
