"""Pick a 2D hydraulics engine: the LISFLOOD-FP worker (GPL, separate service) or built-in `inertial`."""
from __future__ import annotations

import base64
import os

import numpy as np

from . import inertial

DEFAULT_URL = "http://127.0.0.1:8002"


def _url() -> str:
    return os.environ.get("GECAA_LISFLOOD_URL", DEFAULT_URL).rstrip("/")


def worker_health(timeout: float = 1.5) -> dict | None:
    try:
        import httpx
        r = httpx.get(_url() + "/health", timeout=timeout)
        h = r.json() if r.status_code == 200 else None
        return h if h and h.get("ok") else None
    except Exception:  # noqa: BLE001 - any failure means "no worker"
        return None


def _b64(a, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=dtype).tobytes()).decode()


def simulate(dem, res, rain_series, coeff=1.0, manning=0.03, mask=None, engine="auto",
             duration_s=3600.0, drain_s=1800.0) -> dict:
    """Return {depth_max, velocity_max (None for LISFLOOD-FP), engine, version, ...}.

    engine: "auto" (worker if healthy, else inertial), "lisflood" or "inertial".
    rain_series: mm/h, scalar or list of equal blocks over duration_s.
    """
    dem = np.asarray(dem, dtype="float32")
    ny, nx = dem.shape
    rain = list(np.atleast_1d(np.asarray(rain_series, dtype="float64")))
    health = worker_health() if engine in ("auto", "lisflood") else None
    if engine == "lisflood" and not health:
        raise RuntimeError(f"LISFLOOD-FP worker not reachable at {_url()}")
    if health:
        import httpx
        # LISFLOOD-FP has no runoff-coefficient grid: scale rain by the mean coefficient
        c = float(np.mean(np.asarray(coeff, dtype="float64")))
        man = manning if np.ndim(manning) == 0 else _b64(np.broadcast_to(manning, dem.shape), "<f4")
        body = {"dem": _b64(dem, "<f4"), "nx": nx, "ny": ny, "res": float(res),
                "rain_mm_h": [r * c for r in rain], "dt_s": duration_s / len(rain),
                "duration_s": duration_s + drain_s, "manning": float(man) if np.ndim(manning) == 0 else man,
                "mask": None if mask is None else _b64(mask, "u1")}
        r = httpx.post(_url() + "/simulate", json=body, timeout=1800)
        r.raise_for_status()
        j = r.json()
        d = np.frombuffer(base64.b64decode(j["depth_max"]), "<f4").reshape(ny, nx)
        return {"depth_max": d, "velocity_max": None, "engine": "LISFLOOD-FP",
                "version": j.get("version") or health.get("version", ""), "log_tail": j.get("log_tail", "")}
    res_ = inertial.simulate(dem, res, rain, duration_s, drain_s, coeff, manning, mask)
    return {"depth_max": res_.depth_max, "velocity_max": res_.velocity_max, "time_of_max": res_.time_of_max,
            "engine": "gecaa-inertial", "version": inertial.__version__, "volume": res_.volume,
            "runtime_s": res_.runtime_s}
