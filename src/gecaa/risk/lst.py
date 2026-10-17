# SPDX-License-Identifier: Apache-2.0
"""Landsat 8/9 land surface temperature (30 m) around a point, from Microsoft Planetary Computer STAC.

STAC API: https://planetarycomputer.microsoft.com/api/stac/v1, collection `landsat-c2-l2`
(USGS Landsat Collection 2 Level-2, public domain). Assets `lwir11` (= ST_B10) and `qa_pixel`;
URLs are signed anonymously with the `planetary-computer` package. NOT verified here (sandbox fetch
was blocked) - asset keys from the PC collection documentation; check with scripts/probe_v4.py.
LST °C = DN * 0.00341802 + 149.0 - 273.15 (DN 0 = no data). Clouds/shadows masked with QA_PIXEL bits
1 (dilated cloud), 3 (cloud), 4 (cloud shadow). Result is the per-pixel median of the clearest scenes.
CLIMAAX heat classes: bins [20, 25, ..., 60] °C -> classes 1..10 (class 1 < 20 °C, class 10 >= 60 °C).
"""

from __future__ import annotations

import math

import numpy as np

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "landsat-c2-l2"
SCALE, OFFSET = 0.00341802, 149.0
BINS = list(range(20, 61, 5))
QA_MASK = (1 << 1) | (1 << 3) | (1 << 4)
SOURCE = "USGS Landsat 8/9 Collection 2 Level-2 ST_B10 via Microsoft Planetary Computer"

def heat_class(lst_c: np.ndarray) -> np.ndarray:
    cls = (np.digitize(lst_c, BINS) + 1).astype("int16")
    cls[~np.isfinite(lst_c)] = 0
    return cls

def _search(lon, lat, box_m, months, max_cloud, years, limit=200) -> list[dict]:
    """[{'id', 'st', 'qa', 'cloud', 'epsg'}] sorted by cloud cover. Needs the `stac` extra."""
    import planetary_computer
    import pystac_client

    cat = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    d = box_m / 2 / 111000
    search = cat.search(collections=[COLLECTION], bbox=[lon - d, lat - d, lon + d, lat + d],
                        datetime=f"{years[0]}-01-01/{years[1]}-12-31", max_items=limit,
                        query={"eo:cloud_cover": {"lt": max_cloud}, "platform": {"in": ["landsat-8", "landsat-9"]}})
    out = []
    for it in search.items():
        if it.datetime and it.datetime.month in months and "lwir11" in it.assets:
            out.append({"id": it.id, "st": it.assets["lwir11"].href, "qa": it.assets.get("qa_pixel").href
                        if "qa_pixel" in it.assets else None, "cloud": it.properties.get("eo:cloud_cover", 100),
                        "epsg": it.properties.get("proj:epsg")})
    return sorted(out, key=lambda s: s["cloud"])

def _grid(lon, lat, box_m, epsg):
    from pyproj import Transformer
    from rasterio.transform import from_origin

    x, y = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    n = max(1, int(round(box_m / 30)))
    x0, y1 = math.floor((x - box_m / 2) / 30) * 30, math.floor((y + box_m / 2) / 30) * 30
    return from_origin(x0, y1, 30, 30), n

def _read(href, epsg, transform, n):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(href) as src, WarpedVRT(src, crs=f"EPSG:{epsg}", transform=transform, width=n, height=n,
                                               resampling=Resampling.nearest, nodata=0) as vrt:
        return vrt.read(1)

def lst_map(lon: float, lat: float, box_m: float = 2000, months=(6, 7, 8), max_cloud: float = 20,
            years=(2019, 2025), max_scenes: int = 5, scenes: list[dict] | None = None) -> dict | None:
    """Median summer LST (°C) on a 30 m grid of box_m x box_m centred on lon/lat, plus CLIMAAX classes.

    Returns {'lst_c', 'heat_class', 'transform', 'crs', 'scene_ids', 'source'} or None when no scene
    (or no network/STAC packages). `scenes` lets callers/tests pass pre-searched items."""
    if scenes is None:
        try:
            scenes = _search(lon, lat, box_m, set(months), max_cloud, years)
        except Exception:      # offline, missing extra, API error: no invented values
            return None
    scenes = scenes[:max_scenes]
    if not scenes:
        return None
    epsg = scenes[0].get("epsg") or 32632
    transform, n = _grid(lon, lat, box_m, epsg)
    stack, used = [], []
    for s in scenes:
        try:
            dn = _read(s["st"], epsg, transform, n).astype("float64")
            qa = _read(s["qa"], epsg, transform, n) if s.get("qa") else None
        except Exception:
            continue
        lst = dn * SCALE + OFFSET - 273.15
        bad = dn == 0
        if qa is not None:
            bad |= (qa.astype("int64") & QA_MASK) != 0
        lst[bad] = np.nan
        if np.isfinite(lst).any():
            stack.append(lst)
            used.append(s["id"])
    if not stack:
        return None
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(np.stack(stack), axis=0)
    return {"lst_c": np.round(med, 2), "heat_class": heat_class(med), "transform": tuple(transform)[:6],
            "crs": f"EPSG:{epsg}", "scene_ids": used, "source": SOURCE, "bins": BINS}
