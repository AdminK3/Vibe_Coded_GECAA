# SPDX-License-Identifier: Apache-2.0
"""Map layers for the web app: data layers as coloured overlays and GeoJSON in WGS84.

Raster layers are classified with the same thresholds the blueprint uses, so
what the map shows is exactly what the evaluation sees.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.warp import transform_bounds, transform_geom

from .analysis import WGS84, Dataset, _crs_of


def geojson_wgs84(path: Path) -> dict:
    gj = json.loads(Path(path).read_text(encoding="utf-8"))
    src_crs = _crs_of(gj, WGS84)
    feats = []
    for f in gj.get("features", []):
        geom = f["geometry"] if src_crs.upper() == WGS84 else transform_geom(src_crs, WGS84, f["geometry"])
        feats.append({"type": "Feature", "properties": f.get("properties", {}), "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def _rgba(classes: np.ndarray, palette: list[tuple[int, int, int, int]], valid: np.ndarray) -> np.ndarray:
    out = np.zeros((4, *classes.shape), dtype="uint8")
    for i, colour in enumerate(palette):
        sel = (classes == i) & valid
        for band in range(4):
            out[band][sel] = colour[band]
    return out


def _png(rgba: np.ndarray) -> bytes:
    profile = dict(driver="PNG", height=rgba.shape[1], width=rgba.shape[2], count=4, dtype="uint8")
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(rgba)
        return mem.read()


def _read(path: str):
    with rasterio.open(path) as src:
        data = src.read(1, masked=True).astype("float64")
        bounds = transform_bounds(src.crs, WGS84, *src.bounds)
        return data, src.res, bounds


# Colours: good / caution / problem, readable on a light basemap.
GOOD, CAUTION, PROBLEM = (46, 139, 87, 150), (230, 160, 30, 150), (200, 60, 50, 160)


def layer_catalogue(ds: Dataset, params: dict) -> list[dict]:
    """Describe the layers the app can show, with legends derived from the blueprint parameters."""
    req = params["facility_depth_m"] + params["min_unsaturated_zone_m"]
    return [
        {"id": "kf", "label": "Soil permeability (kf)", "type": "raster",
         "legend": [["Too slow (< 1e-6 m/s)", PROBLEM], ["Suitable (1e-6 to 1e-3 m/s)", GOOD], ["Too fast (> 1e-3 m/s)", CAUTION]]},
        {"id": "groundwater", "label": "Depth to groundwater", "type": "raster",
         "legend": [[f"Too shallow (< {req:.1f} m)", PROBLEM], [f"Borderline ({req:.1f} to {req + 0.5:.1f} m)", CAUTION],
                    [f"Sufficient (≥ {req + 0.5:.1f} m)", GOOD]]},
        {"id": "slope", "label": "Terrain slope", "type": "raster",
         "legend": [[f"Flat (≤ {params['slope_flat_max_pct']} %)", GOOD],
                    [f"Moderate (≤ {params['slope_moderate_max_pct']} %)", CAUTION],
                    [f"Steep (> {params['slope_moderate_max_pct']} %)", PROBLEM]]},
        {"id": "zones", "label": "Water protection zones", "type": "vector",
         "legend": [["Zone I / II", PROBLEM], ["Zone III", CAUTION]]},
        {"id": "parcels", "label": "Sample parcels", "type": "vector", "legend": []},
    ]


def raster_overlay(ds: Dataset, layer_id: str, params: dict) -> tuple[bytes, tuple[float, float, float, float]]:
    """Return a PNG and its WGS84 bounds (west, south, east, north)."""
    if layer_id == "kf":
        data, _, bounds = _read(ds.layer("kf_log10")["path"])
        lo, hi = np.log10(params["kf_min_m_s"]), np.log10(params["kf_max_m_s"])
        v = data.filled(np.nan)
        classes = np.where(v < lo, 0, np.where(v > hi, 2, 1))
        palette = [PROBLEM, GOOD, CAUTION]
    elif layer_id == "groundwater":
        data, _, bounds = _read(ds.layer("groundwater_depth")["path"])
        req = params["facility_depth_m"] + params["min_unsaturated_zone_m"]
        v = data.filled(np.nan)
        classes = np.where(v < req, 0, np.where(v < req + 0.5, 1, 2))
        palette = [PROBLEM, CAUTION, GOOD]
    elif layer_id == "slope":
        data, (rx, ry), bounds = _read(ds.layer("dem")["path"])
        gy, gx = np.gradient(data.filled(np.nan), ry, rx)
        v = np.hypot(gx, gy) * 100.0
        classes = np.where(v <= params["slope_flat_max_pct"], 0, np.where(v <= params["slope_moderate_max_pct"], 1, 2))
        palette = [GOOD, CAUTION, PROBLEM]
    else:
        raise KeyError(layer_id)
    valid = ~np.isnan(v)
    return _png(_rgba(classes, palette, valid)), bounds
