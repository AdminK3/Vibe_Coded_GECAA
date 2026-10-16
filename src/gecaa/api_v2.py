# SPDX-License-Identifier: Apache-2.0
"""REST API v2: K3 logic, evaluation units and live geodata."""

from __future__ import annotations

import os
from functools import lru_cache

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from . import data_dir
from .k3.engine import load_logics, validate_logic
from .k3.model import SiteResult
from .k3.service import AssessmentRequest, run_assessment
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


@router.get("/geocode")
def geocode(q: str = Query(..., min_length=3)):
    """Address search via OpenStreetMap Nominatim (server-side, light use within its usage policy)."""
    try:
        r = httpx.get("https://nominatim.openstreetmap.org/search",
                      params={"q": q, "format": "jsonv2", "countrycodes": "de", "limit": 6,
                              "viewbox": "5.8,52.6,9.5,50.3", "bounded": 1, "accept-language": "de"},
                      headers={"User-Agent": "GECAA/0.2 (open-source climate adaptation tool)"}, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Adresssuche nicht erreichbar: {exc}") from exc
    return [{"label": h["display_name"], "lat": float(h["lat"]), "lon": float(h["lon"])} for h in r.json()]


@router.get("/parcel")
def parcel_at(lon: float, lat: float, provider: str | None = None):
    prov = _provider(provider)
    try:
        feat = prov.parcel_at(lon, lat)
    except Exception as exc:
        raise HTTPException(502, f"Flurstücksdienst nicht erreichbar: {exc}") from exc
    if not feat:
        raise HTTPException(404, "An dieser Stelle wurde kein Flurstück gefunden.")
    return feat


@router.get("/buildings")
def buildings(bbox: str, provider: str | None = None):
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox = west,south,east,north in WGS84")
    if (e - w) * (n - s) > 0.0004:     # roughly 1.4 x 2 km at NRW latitudes
        raise HTTPException(422, "Ausschnitt zu groß: bitte weiter hineinzoomen.")
    try:
        return _provider(provider).buildings((w, s, e, n))
    except Exception as exc:
        raise HTTPException(502, f"Gebäudedienst nicht erreichbar: {exc}") from exc


@router.post("/assess", response_model=SiteResult)
def assess_site(req: AssessmentRequest, provider: str | None = None):
    if not req.units:
        raise HTTPException(422, "Mindestens einen Anlagenstandort einzeichnen.")
    return run_assessment(_logic(req.logic_id), _provider(provider), req)


@router.post("/report", response_class=HTMLResponse)
def report(req: AssessmentRequest, provider: str | None = None):
    prov = _provider(provider)
    spec = _logic(req.logic_id)
    result = run_assessment(spec, prov, req)
    return render_k3_report(result, spec, default_app().provisions, req.site_name, synthetic=prov.id == "synthetic")
