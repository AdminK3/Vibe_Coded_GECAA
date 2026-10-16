# SPDX-License-Identifier: Apache-2.0
"""Turn geodata into evidence for the blueprint's root nodes.

Every result carries a distribution over the node's states plus the numbers
behind it, so the report can show both the probability and where it came
from. Uncertainty in the source data (spread inside the parcel and the known
error of the map) becomes soft evidence instead of a false yes/no.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import yaml
from rasterio.mask import mask as rio_mask
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape

WGS84 = "EPSG:4326"


@dataclass
class Parcel:
    id: str
    name: str
    geometry: dict          # GeoJSON geometry
    crs: str
    properties: dict = field(default_factory=dict)

    def in_crs(self, crs: str) -> dict:
        if str(crs).upper() == str(self.crs).upper():
            return self.geometry
        return transform_geom(self.crs, crs, self.geometry)

    def area_m2(self, metric_crs: str = "EPSG:25832") -> float:
        return float(shape(self.in_crs(metric_crs)).area)


def _crs_of(geojson: dict, default: str) -> str:
    return ((geojson.get("crs") or {}).get("properties") or {}).get("name", default)


def load_parcels(path_or_geojson: Path | str | dict, default_crs: str = WGS84) -> list[Parcel]:
    """Read parcels from a GeoJSON file or object (Feature, FeatureCollection or bare geometry).

    GeoJSON is WGS84 by default (RFC 7946); a legacy "crs" member overrides it.
    """
    gj = path_or_geojson
    if not isinstance(gj, dict):
        gj = json.loads(Path(gj).read_text(encoding="utf-8"))
    crs = _crs_of(gj, default_crs)
    if gj.get("type") == "FeatureCollection":
        feats = gj["features"]
    elif gj.get("type") == "Feature":
        feats = [gj]
    else:
        feats = [{"type": "Feature", "properties": {}, "geometry": gj}]
    out = []
    for i, f in enumerate(feats):
        props = f.get("properties") or {}
        out.append(Parcel(id=str(props.get("id", f"parcel-{i + 1}")), name=str(props.get("name", props.get("id", f"Parcel {i + 1}"))),
                          geometry=f["geometry"], crs=_crs_of(f, crs), properties=props))
    return out


@dataclass
class Dataset:
    root: Path
    config: dict

    @classmethod
    def load(cls, path: Path) -> "Dataset":
        path = Path(path)
        return cls(path.parent, yaml.safe_load(path.read_text(encoding="utf-8")))

    def layer(self, name: str) -> dict:
        info = dict(self.config["layers"][name])
        info["path"] = str(self.root / info["path"])
        return info

    @property
    def synthetic(self) -> bool:
        return bool(self.config.get("synthetic", False))


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _raster_values(path: str, parcel: Parcel) -> tuple[np.ndarray, Any]:
    with rasterio.open(path) as src:
        geom = parcel.in_crs(src.crs.to_string())
        data, _ = rio_mask(src, [geom], crop=True, all_touched=True, filled=False)
        values = data[0].compressed()
        nodata = src.nodata
        if nodata is not None:
            values = values[values != nodata]
        return values.astype("float64"), src.res


def _source(layer: dict) -> dict:
    return {"layer": layer.get("description"), "source": layer.get("source"), "licence": layer.get("licence")}


def kf_evidence(ds: Dataset, parcel: Parcel, p: dict) -> dict:
    layer = ds.layer("kf_log10")
    vals, _ = _raster_values(layer["path"], parcel)
    if vals.size == 0:
        raise ValueError(f"parcel {parcel.id} lies outside the permeability layer")
    mu, spread = float(vals.mean()), float(vals.std())
    sigma = math.sqrt(spread ** 2 + p["kf_log10_sigma"] ** 2)
    lo, hi = math.log10(p["kf_min_m_s"]), math.log10(p["kf_max_m_s"])
    p_low = _phi((lo - mu) / sigma)
    p_high = 1.0 - _phi((hi - mu) / sigma)
    dist = {"too_low": p_low, "suitable": max(0.0, 1.0 - p_low - p_high), "too_high": p_high}
    return {
        "distribution": dist,
        "explanation": (f"Mean permeability in the parcel is kf ≈ {10 ** mu:.1e} m/s. With map uncertainty "
                        f"(±{sigma:.2f} in log10), the chance it lies in the working range is {dist['suitable']:.0%}."),
        "detail": {"kf_mean_m_s": float(f"{10 ** mu:.3g}"), "log10_spread_in_parcel": round(spread, 2),
                   "log10_sigma_used": round(sigma, 2), "pixels": int(vals.size), **_source(layer)},
    }


def groundwater_evidence(ds: Dataset, parcel: Parcel, p: dict) -> dict:
    layer = ds.layer("groundwater_depth")
    vals, _ = _raster_values(layer["path"], parcel)
    if vals.size == 0:
        raise ValueError(f"parcel {parcel.id} lies outside the groundwater layer")
    required = p["facility_depth_m"] + p["min_unsaturated_zone_m"]
    mu, spread = float(vals.mean()), float(vals.std())
    sigma = math.sqrt(spread ** 2 + p["groundwater_sigma_m"] ** 2)
    p_ok = 1.0 - _phi((required - mu) / sigma)
    return {
        "distribution": {"sufficient": p_ok, "insufficient": 1.0 - p_ok},
        "explanation": (f"Groundwater lies about {mu:.1f} m below ground (shallowest {float(vals.min()):.1f} m). "
                        f"The system needs at least {required:.1f} m; chance this is met: {p_ok:.0%}."),
        "detail": {"depth_mean_m": round(mu, 2), "depth_min_m": round(float(vals.min()), 2),
                   "required_depth_m": required, "sigma_m": round(sigma, 2), **_source(layer)},
    }


def slope_evidence(ds: Dataset, parcel: Parcel, p: dict) -> dict:
    layer = ds.layer("dem")
    with rasterio.open(layer["path"]) as src:
        dem = src.read(1, masked=True).astype("float64")
        rx, ry = src.res
        gy, gx = np.gradient(dem.filled(np.nan), ry, rx)
        slope_pct = np.hypot(gx, gy) * 100.0
        profile = src.profile.copy()
        profile.update(dtype="float64", nodata=np.nan)
        geom = parcel.in_crs(src.crs.to_string())
        from rasterio.io import MemoryFile
        with MemoryFile() as mem:
            with mem.open(**profile) as tmp:
                tmp.write(slope_pct, 1)
                data, _ = rio_mask(tmp, [geom], crop=True, all_touched=True, filled=False)
    vals = data[0].compressed()
    vals = vals[~np.isnan(vals)]
    if vals.size == 0:
        raise ValueError(f"parcel {parcel.id} lies outside the terrain layer")
    flat_max, mod_max = p["slope_flat_max_pct"], p["slope_moderate_max_pct"]
    dist = {"flat": float((vals <= flat_max).mean()),
            "moderate": float(((vals > flat_max) & (vals <= mod_max)).mean()),
            "steep": float((vals > mod_max).mean())}
    return {
        "distribution": dist,
        "explanation": (f"Median slope is {float(np.median(vals)):.1f} %; {dist['flat']:.0%} of the parcel is "
                        f"flatter than {flat_max} % and {dist['steep']:.0%} is steeper than {mod_max} %."),
        "detail": {"slope_median_pct": round(float(np.median(vals)), 1),
                   "slope_p90_pct": round(float(np.percentile(vals, 90)), 1), **_source(layer)},
    }


def protection_zone_evidence(ds: Dataset, parcel: Parcel) -> dict:
    layer = ds.layer("water_protection_zones")
    gj = json.loads(Path(layer["path"]).read_text(encoding="utf-8"))
    zone_crs = _crs_of(gj, WGS84)
    site = shape(parcel.in_crs(zone_crs))
    prop = layer.get("zone_property", "zone")
    hits = []
    for f in gj.get("features", []):
        zone = shape(f["geometry"])
        if zone.intersects(site):
            share = zone.intersection(site).area / site.area if site.area else 0.0
            hits.append({"zone": str(f["properties"].get(prop, "")).upper(), "name": f["properties"].get("name"),
                         "share_of_parcel": round(share, 3)})
    zones = {h["zone"] for h in hits}
    if zones & {"I", "II", "IA", "IB", "IIA", "IIB"}:
        state = "zone_i_ii"
    elif zones & {"III", "IIIA", "IIIB"}:
        state = "zone_iii"
    else:
        state = "none"
    names = ", ".join(f"{h['name']} ({h['share_of_parcel']:.0%} of parcel)" for h in hits) or "none"
    return {
        "distribution": {state: 1.0},
        "explanation": f"Overlay with water protection zones: {names}. The strictest zone touching the parcel counts.",
        "detail": {"zones": hits, **_source(layer)},
    }


def contamination_evidence(value: str | None, p: dict) -> dict:
    value = (value or "unknown").lower()
    if value in {"none_known", "no", "none"}:
        return {"distribution": {"none_known": 1.0},
                "explanation": "The municipality reports no known or suspected contamination (Altlastenkataster).",
                "detail": {"reported": value}}
    if value in {"suspected", "yes", "known"}:
        return {"distribution": {"suspected": 1.0},
                "explanation": "The municipality reports a suspected contamination.",
                "detail": {"reported": value}}
    q = p["unknown_contamination_p"]
    return {"distribution": {"none_known": 1.0 - q, "suspected": q},
            "explanation": (f"No information given. A prior of {q:.0%} for a suspected contamination is used; "
                            "check the Altlastenkataster to remove this uncertainty."),
            "detail": {"reported": "unknown", "prior_suspected": q}}


def space_evidence(connected_m2: float | None, available_m2: float | None, p: dict) -> dict:
    ratio = p["area_ratio_min"]
    if not connected_m2 or available_m2 is None:
        return {"distribution": {"sufficient": 0.5, "insufficient": 0.5},
                "explanation": "Connected sealed area or free area not given, so space could not be checked.",
                "detail": {"connected_area_m2": connected_m2, "available_area_m2": available_m2}}
    needed = ratio * connected_m2
    ok = available_m2 >= needed
    return {"distribution": {"sufficient" if ok else "insufficient": 1.0},
            "explanation": (f"About {needed:.0f} m² of swale area is needed for {connected_m2:.0f} m² of connected "
                            f"sealed area; {available_m2:.0f} m² is available."),
            "detail": {"connected_area_m2": connected_m2, "available_area_m2": available_m2, "needed_area_m2": round(needed)}}


def site_evidence(ds: Dataset, parcel: Parcel, parameters: dict, context: dict | None = None) -> dict[str, dict]:
    """Evidence for every root node of the swale-trench blueprint."""
    ctx = {**parcel.properties, **(context or {})}
    return {
        "kf": kf_evidence(ds, parcel, parameters),
        "groundwater": groundwater_evidence(ds, parcel, parameters),
        "slope": slope_evidence(ds, parcel, parameters),
        "protection_zone": protection_zone_evidence(ds, parcel),
        "contamination": contamination_evidence(ctx.get("contamination"), parameters),
        "space": space_evidence(ctx.get("connected_area_m2"), ctx.get("available_area_m2"), parameters),
    }


def parcel_summary(parcel: Parcel, ds: Dataset) -> dict:
    return {"id": parcel.id, "name": parcel.name, "area_m2": round(parcel.area_m2(ds.config.get("crs", "EPSG:25832"))),
            "crs": parcel.crs, "geometry": mapping(shape(parcel.geometry)), "synthetic_data": ds.synthetic}
