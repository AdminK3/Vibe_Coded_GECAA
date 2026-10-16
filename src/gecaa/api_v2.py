# SPDX-License-Identifier: Apache-2.0
"""REST API v2: K3 logic, evaluation units and live geodata."""

from __future__ import annotations

import math
import os
import threading
import time
from functools import lru_cache

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from . import data_dir
from .k3.engine import load_logics, validate_logic
from .k3.model import SiteResult
from .k3.service import AssessmentRequest, RequestError, run_assessment
from .report.k3_render import render_k3_report
from .service import default_app

router = APIRouter(prefix="/v2", tags=["v2 (K3 logic)"])


@lru_cache(maxsize=1)
def logics():
    found = load_logics(data_dir() / "blueprints" / "k3")
    provisions = default_app().provisions
    for spec in found.values():
        problems = validate_logic(spec, provisions)
        if problems:
            raise ValueError(f"logic {spec.id} is invalid: {problems}")
    return found


@lru_cache(maxsize=1)
def providers() -> dict:
    from .geodata.synthetic import SyntheticProvider
    out = {"synthetic": SyntheticProvider(data_dir() / "data" / "samples")}
    try:
        from .geodata.nrw import NRWProvider
        out["nrw"] = NRWProvider(cache_dir=data_dir() / "data" / "cache" / "nrw")
    except ImportError:
        pass
    return out


def _provider(pid: str | None):
    pid = pid or os.environ.get("GECAA_PROVIDER", "nrw")
    prov = providers().get(pid) or providers()["synthetic"]
    return prov


def _logic(lid: str):
    spec = logics().get(lid)
    if not spec:
        raise HTTPException(404, f"unknown logic {lid}")
    return spec


@router.get("/logics")
def list_logics():
    return [spec.model_dump() for spec in logics().values()]


@router.get("/providers")
def list_providers():
    out = []
    for pid, p in providers().items():
        out.append({"id": pid, "label": p.label, "bounds": getattr(p, "bounds", None),
                    "sources": [s.__dict__ for s in p.sources], "map_layers": [m.__dict__ for m in p.map_layers]})
    return out


_geo_lock = threading.Lock()
_geo_last = [0.0]


@lru_cache(maxsize=512)
def _nominatim(q: str) -> tuple:
    with _geo_lock:                          # Nominatim usage policy: at most 1 request per second
        wait = 1.0 - (time.monotonic() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        _geo_last[0] = time.monotonic()
        r = httpx.get("https://nominatim.openstreetmap.org/search",
                      params={"q": q, "format": "jsonv2", "countrycodes": "de", "limit": 6,
                              "viewbox": "5.8,52.6,9.5,50.3", "bounded": 1, "accept-language": "de"},
                      headers={"User-Agent": "GECAA/0.2 (open-source climate adaptation tool)"}, timeout=10)
    r.raise_for_status()
    return tuple((h["display_name"], float(h["lat"]), float(h["lon"])) for h in r.json())


@router.get("/geocode")
def geocode(q: str = Query(..., min_length=3, max_length=200)):
    """Address search via OpenStreetMap Nominatim: server-side, throttled to 1 request/s and cached."""
    try:
        hits = _nominatim(" ".join(q.split()).lower())
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Adresssuche nicht erreichbar.") from exc
    return [{"label": label, "lat": lat, "lon": lon} for label, lat, lon in hits]


@router.get("/parcel")
def parcel_at(lon: float, lat: float, provider: str | None = None):
    prov = _provider(provider)
    try:
        feat = prov.parcel_at(lon, lat)
    except Exception as exc:
        raise HTTPException(502, "Flurstücksdienst nicht erreichbar.") from exc
    if not feat:
        raise HTTPException(404, "An dieser Stelle wurde kein Flurstück gefunden.")
    return feat


@router.get("/buildings")
def buildings(bbox: str, provider: str | None = None):
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox = west,south,east,north in WGS84")
    if not all(math.isfinite(v) for v in (w, s, e, n)) or not (w < e and s < n):
        raise HTTPException(422, "bbox = west,south,east,north in WGS84 (west < east, south < north)")
    if (e - w) * (n - s) > 0.0004:     # roughly 1.4 x 2 km at NRW latitudes
        raise HTTPException(422, "Ausschnitt zu groß: bitte weiter hineinzoomen.")
    prov = _provider(provider)
    pw, ps, pe, pn = prov.bounds
    if e < pw or w > pe or n < ps or s > pn:
        return {"type": "FeatureCollection", "features": []}
    try:
        return prov.buildings((w, s, e, n))
    except Exception as exc:
        raise HTTPException(502, "Gebäudedienst nicht erreichbar.") from exc


def _run(req: AssessmentRequest, prov):
    if not req.units:
        raise HTTPException(422, "Mindestens einen Anlagenstandort einzeichnen.")
    spec = _logic(req.logic_id)
    try:
        return spec, run_assessment(spec, prov, req)
    except RequestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/assess", response_model=SiteResult)
def assess_site(req: AssessmentRequest, provider: str | None = None):
    return _run(req, _provider(provider))[1]


@router.post("/report", response_class=HTMLResponse)
def report(req: AssessmentRequest, provider: str | None = None):
    prov = _provider(provider)
    spec, result = _run(req, prov)
    return render_k3_report(result, spec, default_app().provisions, req.site_name, synthetic=prov.id == "synthetic")
