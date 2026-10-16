# SPDX-License-Identifier: Apache-2.0
"""Impact engine selection: the CLIMADA worker (separate GPL-3.0 service, HTTP) or the built-in fallback.

GECAA never imports CLIMADA. It sends plain JSON to the worker (services/climada_worker) and gets plain JSON
back, so GECAA stays Apache-2.0. If the worker is not running, the numpy fallback computes the same quantities.
"""

from __future__ import annotations

import os
import time

import httpx

from . import damage

WORKER_URL = os.environ.get("GECAA_CLIMADA_URL", "http://127.0.0.1:8001")
_health_cache: dict = {"t": 0.0, "value": None}


def worker_health(force: bool = False) -> dict | None:
    if not force and time.time() - _health_cache["t"] < 30:
        return _health_cache["value"]
    try:
        r = httpx.get(f"{WORKER_URL}/health", timeout=2.0)
        value = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        value = None
    _health_cache.update(t=time.time(), value=value)
    return value


def impact(payload: dict, curves, buildings: list[damage.Building], prefer_worker: bool = True) -> dict:
    """payload: see services/climada_worker/README.md. Returns the worker answer or the fallback answer."""
    if prefer_worker and worker_health():
        try:
            r = httpx.post(f"{WORKER_URL}/impact", json=payload, timeout=120)
            r.raise_for_status()
            out = r.json()
            out["worker"] = True
            return out
        except (httpx.HTTPError, ValueError) as exc:
            fallback_reason = f"CLIMADA-Worker-Fehler: {exc}"[:200]
    else:
        fallback_reason = "CLIMADA-Worker nicht gestartet"
    out = _fallback(payload, curves, buildings)
    out["worker"] = False
    out["fallback_reason"] = fallback_reason
    return out


def _fallback(payload: dict, curves, buildings: list[damage.Building]) -> dict:
    rps = payload["rps"]
    events = [f"RP{r}" for r in rps]

    def run(depth_rows):
        for b, row in zip(buildings, depth_rows):
            b.depth_by_event = dict(zip(events, row))
        return damage.fallback_impact(curves, buildings, events, rps)

    base = run(payload["depth"])
    out = {"engine": base["engine"], "version": base["version"], "aai_agg": base["aai_agg"], "eai": base["eai"],
           "freq_curve": base["freq_curve"]}
    if payload.get("measure_depth") is not None:
        m = run(payload["measure_depth"])
        out["measure"] = {"aai_agg": m["aai_agg"], "freq_curve": m["freq_curve"],
                          "benefit_annual": base["aai_agg"] - m["aai_agg"]}
        cost = payload.get("cost_eur")
        years = payload.get("years", 30)
        rate = payload.get("discount_rate", 0.02)
        annuity = sum(1 / (1 + rate) ** t for t in range(years))   # CLIMADA convention: first year undiscounted
        out["measure"]["benefit_npv"] = out["measure"]["benefit_annual"] * annuity
        out["measure"]["bcr"] = (out["measure"]["benefit_npv"] / cost) if cost else None
    return out
