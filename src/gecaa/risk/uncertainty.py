# SPDX-License-Identifier: Apache-2.0
"""Uncertainty of the expected annual damage (aai_agg): numpy Monte Carlo fallback for the CLIMADA worker's
`/uncertainty` (climada.engine.unsequa). Same payload as `/impact`, same factors, 1000 samples.

Factors (uniform): damage-curve scale 0.7-1.3 (MDR x s, clipped to 1), exposure value scale 0.8-1.2 and
hazard intensity scale 0.8-1.2. First-order sensitivity S1 = Var(E[Y|X_i]) / Var(Y), estimated by binning.
"""

from __future__ import annotations

import httpx
import numpy as np

from . import engine_client

FACTORS = {"curve_scale": (0.7, 1.3), "exposure_scale": (0.8, 1.2), "hazard_scale": (0.8, 1.2)}


def _band_freq(rps) -> np.ndarray:
    inv = 1.0 / np.asarray(rps, float)
    return inv - np.append(inv[1:], 0.0)


def monte_carlo(p: dict, n: int = 1000, seed: int = 1) -> dict:
    order = np.argsort(p["rps"])
    rps = np.asarray(p["rps"], float)[order]
    exps = p["exposures"]
    rng = np.random.default_rng(seed)
    x = {k: rng.uniform(lo, hi, n) for k, (lo, hi) in FACTORS.items()}
    y = np.zeros(n)
    if exps:
        depth = np.asarray(p["depth"], float)[:, order]          # buildings x events
        freq = _band_freq(rps)
        values = np.array([e["value"] for e in exps], float)
        cls = [e["cls"] for e in exps]
        for c in set(cls):
            idx = np.array([k == c for k in cls])
            dc = np.asarray(p["impfs"][c]["depth"], float)
            mc = np.asarray(p["impfs"][c]["mdr"], float)
            d = depth[idx][None, :, :] * x["hazard_scale"][:, None, None]          # samples x b x events
            mdr = np.clip(np.interp(d, dc, mc) * x["curve_scale"][:, None, None], 0, 1)
            y += (mdr * values[idx][None, :, None] * freq[None, None, :]).sum(axis=(1, 2))
        y *= x["exposure_scale"]
    var = float(y.var())
    s1 = {}
    for k, xs in x.items():
        bins = np.quantile(xs, np.linspace(0, 1, 21))
        lab = np.clip(np.digitize(xs, bins[1:-1]), 0, 19)
        means = np.array([y[lab == b].mean() for b in range(20)])
        counts = np.array([(lab == b).sum() for b in range(20)])
        s1[k] = float(np.average((means - y.mean()) ** 2, weights=counts) / var) if var > 0 else 0.0
    return {"engine": "GECAA-Fallback (numpy Monte Carlo)", "version": "1", "n_runs": n,
            "factors": {k: list(v) for k, v in FACTORS.items()},
            "aai_agg": {"p5": float(np.percentile(y, 5)), "p50": float(np.percentile(y, 50)),
                        "p95": float(np.percentile(y, 95)), "mean": float(y.mean())},
            "sensitivity_S1": s1}


def assess(p: dict, prefer_worker: bool = True) -> dict:
    """Worker (CLIMADA unsequa) if healthy, else the numpy Monte Carlo."""
    reason = "CLIMADA-Worker nicht gestartet"
    if prefer_worker and engine_client.worker_health():
        try:
            r = httpx.post(f"{engine_client.WORKER_URL}/uncertainty", json=p, timeout=300)
            r.raise_for_status()
            return {**r.json(), "worker": True}
        except (httpx.HTTPError, ValueError) as exc:
            reason = f"CLIMADA-Worker-Fehler: {exc}"[:200]
    return {**monte_carlo(p), "worker": False, "fallback_reason": reason}
