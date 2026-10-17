# SPDX-License-Identifier: Apache-2.0
"""v6 API: digital twin scene for a stored heavy-rain run (terrain, LoD2 buildings with results, measures,
project timeline) and open street-level imagery (Panoramax, optional Mapillary)."""

from __future__ import annotations

import base64
import math
import os
import time

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/v6", tags=["v6 (Digitaler Zwilling, Straßenbilder)"])
MAX_TERRAIN = 256


def _run(run_id: str) -> tuple[dict, dict]:
    from .api_v3 import _RESULTS
    from .risk import chain
    res, layers = _RESULTS.get(run_id), chain._RUNS.get(run_id)
    if not res or not layers or "dem" not in layers:
        raise HTTPException(404, "Ergebnis abgelaufen – bitte den Starkregen neu rechnen.")
    return res, layers


def _terrain(dem: dict) -> dict:
    """DEM (EPSG:25832) -> regular WGS84 grid, at most MAX_TERRAIN cells per side."""
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    arr = dem["array"]
    t = dem["transform"]
    tr = t if isinstance(t, Affine) else Affine(*t[:6])
    h, w = arr.shape
    left, top = tr.c, tr.f
    right, bottom = left + w * tr.a, top + h * tr.e
    dst_tr, dw, dh = calculate_default_transform("EPSG:25832", "EPSG:4326", w, h, left, bottom, right, top)
    f = max(dw, dh) / MAX_TERRAIN
    if f > 1:
        dw, dh = max(2, int(dw / f)), max(2, int(dh / f))
        dst_tr, _, _ = calculate_default_transform("EPSG:25832", "EPSG:4326", w, h, left, bottom, right, top,
                                                   dst_width=dw, dst_height=dh)
    out = np.full((dh, dw), np.nan, dtype="float32")
    reproject(arr.astype("float32"), out, src_transform=tr, src_crs="EPSG:25832", dst_transform=dst_tr,
              dst_crs="EPSG:4326", resampling=Resampling.bilinear, dst_nodata=np.nan)
    fill = float(np.nanmin(out)) if np.isfinite(out).any() else 0.0
    out = np.where(np.isfinite(out), out, fill).astype("float32")
    return {"w": dw, "h": dh, "rect": [dst_tr.c, dst_tr.f + dh * dst_tr.e, dst_tr.c + dw * dst_tr.a, dst_tr.f],
            "min": float(out.min()), "max": float(out.max()), "heights": base64.b64encode(out.tobytes()).decode()}


def _dem_at(dem: dict, x: float, y: float) -> float | None:
    t = dem["transform"]
    a, c, e, f = (t.a, t.c, t.e, t.f) if hasattr(t, "a") else (t[0], t[2], t[4], t[5])
    col, row = int((x - c) / a), int((y - f) / e)
    arr = dem["array"]
    if 0 <= row < arr.shape[0] and 0 <= col < arr.shape[1] and np.isfinite(arr[row, col]):
        return float(arr[row, col])
    return None


@router.get("/twin/{run_id}/scene")
def twin_scene(run_id: str, project: str | None = None):
    from pyproj import Transformer
    res, layers = _run(run_id)
    dem = layers["dem"]
    to_ll = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    lod2 = {}
    prov = layers.get("provider")
    if prov is not None and hasattr(prov, "lod2_surfaces"):
        lod2 = {k[:16]: v for k, v in (prov.lod2_surfaces(layers["bbox"]) or {}).items()}
    fps = layers.get("footprints") or {}
    buildings, n_lod2 = [], 0
    for b in res.get("buildings") or []:
        bid = str(b["id"])
        surf = lod2.get(bid) or lod2.get(bid[:16])
        geom = fps.get(bid)
        item = {"id": bid, "label": b.get("label"), "cls": b.get("cls_de"), "eai": b.get("eai_eur"),
                "persons": b.get("persons_est"), "depth": b.get("depth_by_scenario"),
                "depth_m": b.get("depth_measure_by_scenario"), "height": b.get("height_m")}
        if surf:
            x, y = to_utm.transform(*np.mean([p[:2] for s in surf for p in s["ring"]], axis=0))
            ground = _dem_at(dem, x, y)
            zmin = min(p[2] for s in surf for p in s["ring"])
            dz = (ground - zmin) if ground is not None else 0.0
            item["surfaces"] = [{"t": s["t"], "ring": [[p[0], p[1], round(p[2] + dz, 2)] for p in s["ring"]]} for s in surf]
            item["source"] = "LoD2 (Geobasis NRW)"
            n_lod2 += 1
        elif geom is not None:
            g = geom.geoms[0] if hasattr(geom, "geoms") else geom
            c = g.centroid
            item["ground"] = _dem_at(dem, c.x, c.y)
            item["footprint"] = [list(map(lambda v: round(v, 7), to_ll.transform(px, py))) for px, py in g.exterior.coords]
            item["source"] = "ALKIS-Grundriss" + ("" if b.get("height_m") else " (Höhe OFFEN)")
        else:
            continue
        buildings.append(item)
    measures, timeline = [], None
    if project:
        from .projects import store
        p = store.get(project)
        if p:
            measures = [{"id": m["id"], "name": m["name"], "kind": m["kind"], "geometry": m.get("geometry")}
                        for m in p.get("measures") or [] if m.get("geometry")]
            timeline = {"name": p.get("name"), "phases": [{k: ph.get(k) for k in ("key", "label", "start", "end")}
                                                          for ph in p.get("phases") or []]}
    return {"run_id": run_id, "center": res.get("center"), "bounds": res.get("bounds"), "rps": res["rps"],
            "scenarios": res["scenarios"], "threshold_m": res.get("threshold_m"),
            "has_measure": any(k.endswith("|measure") for k in layers if k.startswith("depth|")),
            "measures_result": res.get("measures") or [], "terrain": _terrain(dem), "buildings": buildings,
            "lod2_count": n_lod2, "measures": measures, "timeline": timeline,
            "sources": ["DGM1 / ALKIS / 3D-Gebäudemodell LoD2: Geobasis NRW (dl-de/zero-2-0)",
                        "Hintergrund: basemap.de (BKG, CC BY 4.0)", "Wassertiefen: GECAA-Berechnung (Screening)"]}


# ------------------------------------------------------------------ street-level imagery

PANORAMAX = "https://api.panoramax.xyz/api/search"
_CACHE: dict = {}


def _dist_m(lon1, lat1, lon2, lat2) -> float:
    k = 111320.0
    return math.hypot((lon2 - lon1) * k * math.cos(math.radians(lat1)), (lat2 - lat1) * 110540.0)


def _bearing(lon1, lat1, lon2, lat2) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(math.radians(lat1)) * math.cos(
        math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def parse_panoramax(doc: dict, lon: float, lat: float) -> list[dict]:
    out = []
    for f in doc.get("features", []) or []:
        g = (f.get("geometry") or {}).get("coordinates") or [None, None]
        if g[0] is None:
            continue
        pr, a = f.get("properties") or {}, f.get("assets") or {}
        az = pr.get("view:azimuth")
        towards = _bearing(g[0], g[1], lon, lat)
        facing = None if az is None else abs((az - towards + 180) % 360 - 180) <= 50
        out.append({"source": "Panoramax", "id": f.get("id"), "lon": g[0], "lat": g[1], "azimuth": az,
                    "date": (pr.get("datetime") or "")[:10], "thumb": (a.get("thumb") or {}).get("href"),
                    "image": (a.get("sd") or {}).get("href"), "hd": (a.get("hd") or {}).get("href"),
                    "license": pr.get("license"), "author": pr.get("geovisio:producer"),
                    "distance_m": round(_dist_m(lon, lat, g[0], g[1])), "facing": facing,
                    "viewer": f"https://api.panoramax.xyz/#focus=pic&pic={f.get('id')}&map=19/{g[1]}/{g[0]}"})
    return out


def _mapillary(lon: float, lat: float, r: float) -> list[dict]:
    tok = os.environ.get("GECAA_MAPILLARY_TOKEN")
    if not tok:
        return []
    d = r / 111320.0
    q = {"access_token": tok, "fields": "id,thumb_256_url,thumb_1024_url,computed_geometry,captured_at,compass_angle",
         "bbox": f"{lon - d * 1.6},{lat - d},{lon + d * 1.6},{lat + d}", "limit": "20"}
    try:
        data = httpx.get("https://graph.mapillary.com/images", params=q, timeout=15).json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for m in data:
        c = (m.get("computed_geometry") or {}).get("coordinates")
        if not c:
            continue
        az = m.get("compass_angle")
        towards = _bearing(c[0], c[1], lon, lat)
        out.append({"source": "Mapillary", "id": m["id"], "lon": c[0], "lat": c[1], "azimuth": az,
                    "date": time.strftime("%Y-%m-%d", time.gmtime((m.get("captured_at") or 0) / 1000)),
                    "thumb": m.get("thumb_256_url"), "image": m.get("thumb_1024_url"), "hd": m.get("thumb_1024_url"),
                    "license": "CC-BY-SA-4.0", "author": "Mapillary-Mitwirkende", "distance_m": round(_dist_m(lon, lat, *c)),
                    "facing": None if az is None else abs((az - towards + 180) % 360 - 180) <= 50,
                    "viewer": f"https://www.mapillary.com/app/?pKey={m['id']}"})
    return out


@router.get("/street")
def street_images(lon: float = Query(ge=5.0, le=16.0), lat: float = Query(ge=47.0, le=56.0),
                  radius: float = Query(60, ge=10, le=300)):
    """Open street-level photos around a point, best first (facing the point, then nearest)."""
    key = (round(lon, 5), round(lat, 5), radius)
    if key in _CACHE and time.time() - _CACHE[key][0] < 3600:
        return _CACHE[key][1]
    d = radius / 111320.0
    imgs, err = [], None
    try:
        r = httpx.get(PANORAMAX, params={"bbox": f"{lon - d * 1.6},{lat - d},{lon + d * 1.6},{lat + d}", "limit": "40"},
                      timeout=15, headers={"User-Agent": "GECAA/0.6"})
        r.raise_for_status()
        imgs = parse_panoramax(r.json(), lon, lat)
    except (httpx.HTTPError, ValueError) as exc:
        err = f"Panoramax nicht erreichbar: {type(exc).__name__}"
    imgs += _mapillary(lon, lat, radius)
    imgs = [i for i in imgs if i["distance_m"] <= radius]
    imgs.sort(key=lambda i: (i["facing"] is not True, i["distance_m"]))
    out = {"lon": lon, "lat": lat, "radius_m": radius, "images": imgs[:12], "error": err,
           "sources": ["Panoramax (OpenStreetMap France u. a.), Lizenz je Bild"]
           + (["Mapillary (CC-BY-SA)"] if os.environ.get("GECAA_MAPILLARY_TOKEN") else [])}
    if not err:
        _CACHE[key] = (time.time(), out)
        if len(_CACHE) > 500:
            _CACHE.pop(next(iter(_CACHE)))
    return out


# ------------------------------------------------------------------ Google Street View (optional, needs a key)

def _google_key() -> str | None:
    return os.environ.get("GECAA_GOOGLE_MAPS_KEY") or None


@router.get("/streetview")
def google_streetview(lon: float = Query(ge=5.0, le=16.0), lat: float = Query(ge=47.0, le=56.0),
                      radius: int = Query(80, ge=10, le=300)):
    """Embed URL for Google Street View looking at a point (Maps Embed API, street view mode).

    Only when GECAA_GOOGLE_MAPS_KEY is set. The free Street View metadata request finds the nearest panorama so the
    view can be turned towards the building. Images are only displayed in Google's own frame; nothing is stored or
    analysed (Google Maps Platform terms)."""
    key = _google_key()
    if not key:
        return {"enabled": False}
    pano = None
    try:
        m = httpx.get("https://maps.googleapis.com/maps/api/streetview/metadata",
                      params={"location": f"{lat},{lon}", "radius": radius, "source": "outdoor", "key": key}, timeout=10).json()
        if m.get("status") == "OK":
            pano = {"id": m.get("pano_id"), "lat": m["location"]["lat"], "lon": m["location"]["lng"], "date": m.get("date")}
        status = m.get("status")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        status = f"Fehler: {type(exc).__name__}"
    if not pano:
        return {"enabled": True, "available": False, "status": status}
    heading = round(_bearing(pano["lon"], pano["lat"], lon, lat))
    q = httpx.QueryParams({"key": key, "pano": pano["id"], "heading": heading, "pitch": 5, "fov": 80})
    return {"enabled": True, "available": True, "status": status, "pano_date": pano["date"],
            "distance_m": round(_dist_m(lon, lat, pano["lon"], pano["lat"])), "heading": heading,
            "embed_url": f"https://www.google.com/maps/embed/v1/streetview?{q}",
            "maps_url": f"https://www.google.com/maps/@?api=1&map_action=pano&pano={pano['id']}&heading={heading}",
            "note": "Bilder © Google. Nur Anzeige im Google-Fenster; keine Speicherung oder Auswertung."}
