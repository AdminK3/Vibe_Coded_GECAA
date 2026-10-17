# SPDX-License-Identifier: Apache-2.0
"""REST API v3: climate risk (heavy rain with CLIMADA, heat), CLIMAAX risk assessment."""

from __future__ import annotations

import threading
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import data_dir
from .api_v2 import _provider
from .risk import chain, cra, engine_client, heat
from .risk.kostra import Kostra

router = APIRouter(prefix="/v3", tags=["v3 (Klimarisiko)"])
MAX_UNC_EXPOSURES = 2000
_RAIN_SLOTS = threading.BoundedSemaphore(2)      # at most two heavy-rain analyses at a time (CPU, memory)


@lru_cache(maxsize=1)
def _kostra() -> Kostra:
    return Kostra(cache_dir=data_dir() / "data" / "cache" / "kostra")


@lru_cache(maxsize=1)
def _hot_days() -> heat.HotDays:
    return heat.HotDays(cache_dir=data_dir() / "data" / "cache" / "dwd_hot_days")


@router.get("/health")
def health():
    w = engine_client.worker_health(force=True)
    return {"status": "ok", "climada_worker": w, "engine": "CLIMADA" if w else "GECAA-Fallback",
            "worker_url": engine_client.WORKER_URL}


@router.post("/risk/rain")
def risk_rain(req: chain.RainRequest, provider: str | None = None):
    prov = _provider(provider)
    if not prov.covers(req.lon, req.lat):
        raise HTTPException(422, "Der Ort liegt außerhalb des Datengebiets.")
    if not _RAIN_SLOTS.acquire(timeout=30):
        raise HTTPException(503, "Gerade laufen andere Analysen – bitte gleich noch einmal versuchen.")
    try:
        return chain.run(prov, req, kostra=None if prov.id == "synthetic" else _kostra())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # network errors of the data services
        raise HTTPException(502, f"Datendienst nicht erreichbar: {type(exc).__name__}") from exc
    finally:
        _RAIN_SLOTS.release()


@router.get("/risk/{run_id}/layer")
def risk_layer(run_id: str, name: str = Query(..., max_length=80)):
    png = chain.layer(run_id, name)
    if png is None:
        raise HTTPException(404, "Ebene nicht gefunden (Ergebnis abgelaufen?)")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/heat")
def risk_heat(lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90),
              provider: str | None = None):
    prov = _provider(provider)
    if not prov.covers(lon, lat):
        raise HTTPException(422, "Der Ort liegt außerhalb des Datengebiets.")
    return heat.assess(prov, lon, lat, None if prov.id == "synthetic" else _hot_days())


class CRABuilding(BaseModel):
    depth: dict[str, float] = Field(default_factory=dict, max_length=10)


class CRARain(BaseModel):
    rps: list[int] = Field(min_length=1, max_length=9)
    threshold_m: float = Field(0.10, ge=0, le=0.5)
    buildings: list[CRABuilding] = Field(default_factory=list, max_length=20000)
    buildings_future: list[CRABuilding] | None = Field(None, max_length=20000)


class CRARequest(BaseModel):
    rain: CRARain | None = None
    heat: dict | None = None
    capacity: int = Field(2, ge=1, le=4)


@router.get("/cra/scales")
def cra_scales():
    return cra.scales()


@router.post("/cra/suggest")
def cra_suggest(req: CRARequest):
    try:
        rows = cra.suggest(req.rain.model_dump() if req.rain else None, req.heat, req.capacity)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise HTTPException(422, f"Ungültige Eingabe: {exc}") from exc
    return {"rows": rows, "source": cra.SOURCE,
            "note": "Vorschlag (GECAA-Heuristik) – CLIMAAX sieht eine qualitative Abwägung im Gespräch vor."}


# --- appended: PDF report, cached rain runs for the 3D view, GeoTIFF export, uncertainty -------------------
from collections import OrderedDict as _OrderedDict  # noqa: E402

from .risk import uncertainty as _uncertainty  # noqa: E402

_RESULTS: "_OrderedDict[str, dict]" = _OrderedDict()     # run_id -> result JSON (only runs via /risk/rain3d)
_MAX_RESULTS = 12


class ReportRequest(BaseModel):
    rain: dict | None = None
    heat: dict | None = None
    cra: list | dict | None = None
    check: dict | None = None
    site: dict = Field(default_factory=dict)


@router.post("/report/pdf", response_class=Response)
def report_pdf(req: ReportRequest):
    from .report.typst_report import build_pdf
    try:
        rain = req.rain
        if rain and rain.get("run_id") in _RESULTS:      # use the server's own result, not client-sent numbers
            rain = _RESULTS[rain["run_id"]]
        elif rain:
            rain = {**rain, "client_supplied": True}
        pdf = build_pdf(rain, req.heat, req.cra, req.check, req.site)
    except Exception as exc:  # typst compile errors on odd inputs
        raise HTTPException(422, f"Bericht konnte nicht erstellt werden: {type(exc).__name__}: {exc}"[:400]) from exc
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="klimarisiko-bericht.pdf"'})


@router.post("/risk/rain3d")
def risk_rain3d(req: chain.RainRequest, provider: str | None = None):
    """Like /risk/rain, but keeps the result so /risk/{run_id}/result (3D view, QGIS) can read it."""
    res = risk_rain(req, provider)
    _RESULTS[res["run_id"]] = res
    while len(_RESULTS) > _MAX_RESULTS:
        _RESULTS.popitem(last=False)
    return res


@router.get("/risk/{run_id}/result")
def risk_result(run_id: str):
    res = _RESULTS.get(run_id)
    if res is None:
        raise HTTPException(404, "Ergebnis nicht gespeichert (abgelaufen oder nicht über /v3/risk/rain3d berechnet).")
    return res


@router.get("/risk/{run_id}/geotiff", response_class=Response)
def risk_geotiff(run_id: str, name: str = Query(..., max_length=80)):
    """Depth grid as GeoTIFF (EPSG:25832). Needs chain to keep float grids in _RUNS[run_id] under
    'grid|<scenario>|<rp>|<variant>' as {"array": 2-D float array, "transform": affine 6-tuple (UTM 32)}."""
    runs = getattr(chain, "_RUNS", {})
    if run_id not in runs:
        raise HTTPException(404, "Lauf nicht gefunden (Ergebnis abgelaufen?)")
    key = "grid|" + (name[len("depth|"):] if name.startswith("depth|") else name)
    g = runs[run_id].get(key)
    if not isinstance(g, dict) or "array" not in g or "transform" not in g:
        raise HTTPException(404, "Für diesen Lauf liegen keine Rohraster vor: chain speichert nur PNG-Ebenen. "
                                 f"Benötigt wird _RUNS[run_id]['{key}'] = {{'array', 'transform'}}.")
    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import Affine

    arr = np.asarray(g["array"], dtype="float32")
    t = g["transform"]
    tr = t if isinstance(t, Affine) else Affine(*list(t)[:6])
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype="float32",
                      crs=rasterio.crs.CRS.from_epsg(25832), transform=tr, nodata=-9999.0, compress="deflate") as ds:
            ds.write(np.nan_to_num(arr, nan=-9999.0), 1)
            ds.update_tags(units="m", name=name)
        data = mem.read()
    return Response(data, media_type="image/tiff",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}_depth.tif"'})


class UncertaintyRequest(BaseModel):
    rps: list[float] = Field(min_length=1, max_length=10)
    exposures: list[dict] = Field(max_length=MAX_UNC_EXPOSURES)
    impfs: dict[str, dict]
    depth: list[list[float]] = Field(max_length=MAX_UNC_EXPOSURES)
    measure_depth: list[list[float]] | None = None
    cost_eur: float | None = None
    years: int = Field(30, ge=1, le=100)
    discount_rate: float = Field(0.02, ge=0, le=0.2)
    prefer_worker: bool = True


@router.post("/risk/uncertainty")
def risk_uncertainty(req: UncertaintyRequest):
    if len(req.depth) != len(req.exposures):
        raise HTTPException(422, "depth braucht eine Zeile je Gebäude")
    p = req.model_dump(exclude={"prefer_worker"})
    try:
        return _uncertainty.assess(p, prefer_worker=req.prefer_worker)
    except (KeyError, ValueError, IndexError) as exc:
        raise HTTPException(422, f"Ungültige Eingabe: {exc}") from exc


# ---------------------------------------------------------------- v4: heatwaves, surface temperature, plans

@router.get("/heat/waves")
def heat_waves(lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90)):
    """Heatwaves (xclim, CLIMAAX thresholds) and tropical nights at the nearest DWD station."""
    from .risk import heatwave
    try:
        out = heatwave.heatwaves(lon, lat, cache_dir=data_dir() / "data" / "cache" / "dwd_daily")
    except ImportError as exc:
        raise HTTPException(501, f"xclim nicht installiert: {exc}") from exc
    if out is None:
        raise HTTPException(502, "DWD-Stationsdaten nicht erreichbar")
    return out


_LST_CACHE: dict = {}


@router.get("/heat/lst")
def heat_lst(lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90),
             box_m: float = Query(2000, ge=500, le=5000)):
    """Landsat summer land surface temperature (median) with CLIMAAX heat classes, as map overlay."""
    import uuid

    import numpy as np
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    from .risk import lst, raster_png
    try:
        r = lst.lst_map(lon, lat, box_m=box_m)
    except ImportError as exc:
        raise HTTPException(501, f"STAC-Pakete nicht installiert: {exc}") from exc
    if r is None:
        raise HTTPException(502, "Keine wolkenfreien Landsat-Szenen oder STAC nicht erreichbar")
    a = np.asarray(r["lst_c"], dtype="float32")
    tr = Affine(*r["transform"])
    h, w = a.shape
    dst_tr, dw, dh = calculate_default_transform(r["crs"], "EPSG:3857", w, h, tr.c, tr.f + h * tr.e, tr.c + w * tr.a, tr.f)
    out = np.full((dh, dw), np.nan, dtype="float32")
    reproject(a, out, src_transform=tr, src_crs=r["crs"], dst_transform=dst_tr, dst_crs="EPSG:3857",
              resampling=Resampling.nearest, src_nodata=np.nan, dst_nodata=np.nan)
    rid = uuid.uuid4().hex[:12]
    _LST_CACHE[rid] = raster_png.lst_png(out)
    while len(_LST_CACHE) > 8:
        _LST_CACHE.pop(next(iter(_LST_CACHE)))
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    w_, s_ = t.transform(dst_tr.c, dst_tr.f + dh * dst_tr.e)
    e_, n_ = t.transform(dst_tr.c + dw * dst_tr.a, dst_tr.f)
    valid = a[np.isfinite(a)]
    hc = np.asarray(r["heat_class"])
    return {"id": rid, "bounds": [[s_, w_], [n_, e_]], "scene_ids": r["scene_ids"], "source": r["source"],
            "stats": {"min": float(valid.min()), "median": float(np.median(valid)), "max": float(valid.max()),
                      "p90": float(np.percentile(valid, 90))} if valid.size else None,
            "classes": {int(k): int((hc == k).sum()) for k in range(1, 11)}}


@router.get("/heat/lst/{rid}.png")
def heat_lst_png(rid: str):
    png = _LST_CACHE.get(rid)
    if png is None:
        raise HTTPException(404, "Ebene abgelaufen")
    return Response(png, media_type="image/png")


@router.get("/plans")
def plans(lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90),
          box_m: float = Query(400, ge=50, le=2000), provider: str | None = None):
    """Development plans (Bebauungspläne, INSPIRE PLU NRW) around a point."""
    from .geodata.base import to_utm
    prov = _provider(provider)
    if not hasattr(prov, "development_plans"):
        return {"plans": [], "note": "Für diese Datenquelle gibt es keine Bebauungspläne."}
    p = to_utm({"type": "Point", "coordinates": [lon, lat]})
    out = prov.development_plans((p.x - box_m / 2, p.y - box_m / 2, p.x + box_m / 2, p.y + box_m / 2))
    if out is None:
        raise HTTPException(502, "Bebauungsplan-Dienst nicht erreichbar")
    return {"plans": out, "source": "Geobasis NRW / Kommunen, INSPIRE Planned Land Use (dl-de/by-2-0)"}


@router.get("/risk/{run_id}/uncertainty")
def risk_run_uncertainty(run_id: str):
    """Uncertainty (CLIMADA unsequa or numpy Monte Carlo) for a stored heavy-rain run, today's scenario."""
    p = chain._RUNS.get(run_id, {}).get("payload")
    if p is None:
        raise HTTPException(404, "Ergebnis abgelaufen – bitte neu rechnen")
    if len(p["exposures"]) > MAX_UNC_EXPOSURES:
        raise HTTPException(422, f"Zu viele Gebäude für die Unsicherheitsanalyse (höchstens {MAX_UNC_EXPOSURES})")
    if not p["exposures"]:
        return {"note": "keine Gebäude im Gebiet"}
    return _uncertainty.assess(dict(p), prefer_worker=True)
