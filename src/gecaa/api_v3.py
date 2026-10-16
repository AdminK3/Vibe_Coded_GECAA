# SPDX-License-Identifier: Apache-2.0
"""REST API v3: climate risk (heavy rain with CLIMADA, heat), CLIMAAX risk assessment."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import data_dir
from .api_v2 import _provider
from .risk import chain, cra, engine_client, heat
from .risk.kostra import Kostra

router = APIRouter(prefix="/v3", tags=["v3 (Klimarisiko)"])


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
    try:
        return chain.run(prov, req, kostra=None if prov.id == "synthetic" else _kostra())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # network errors of the data services
        raise HTTPException(502, f"Datendienst nicht erreichbar: {type(exc).__name__}") from exc


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


class CRARequest(BaseModel):
    rain: dict | None = None
    heat: dict | None = None
    capacity: int = Field(2, ge=1, le=4)


@router.get("/cra/scales")
def cra_scales():
    return cra.scales()


@router.post("/cra/suggest")
def cra_suggest(req: CRARequest):
    return {"rows": cra.suggest(req.rain, req.heat, req.capacity), "source": cra.SOURCE,
            "note": "Vorschlag (GECAA-Heuristik) – CLIMAAX sieht eine qualitative Abwägung im Gespräch vor."}
