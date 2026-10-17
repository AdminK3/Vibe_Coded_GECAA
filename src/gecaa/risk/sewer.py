# SPDX-License-Identifier: Apache-2.0
"""Sewer capacity: design-rain approach (DWA-A 118 / DIN EN 752) and optional PySWMM node flooding."""

from __future__ import annotations

import base64
import os
import tempfile

import numpy as np

DESIGN_RPS = (1, 2, 3, 5, 10)
SEALED_C = 0.6


def effective_coeff(coeff: np.ndarray, event_mm: float, design_mm: float) -> np.ndarray:
    """Sealed cells (coeff >= 0.6) only get max(0, event - design) rain; expressed as a scaled coefficient
    so the uniform-rain ponding model can be used unchanged."""
    if event_mm <= 0:
        return coeff
    f = max(0.0, event_mm - design_mm) / event_mm
    return np.where(coeff >= SEALED_C, coeff * f, coeff)


def decode_inp(b64: str) -> str:
    data = base64.b64decode(b64, validate=True)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("SWMM-Datei größer als 2 MB")
    import re
    text = data.decode("latin-1")
    if re.search(r"^\s*\[FILES\]", text, re.I | re.M) or re.search(r"\bFILE\b", text, re.I):
        raise ValueError("SWMM-Dateien mit Verweisen auf andere Dateien ([FILES], FILE) sind nicht erlaubt")
    fd, path = tempfile.mkstemp(suffix=".inp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def _coords(inp_path: str) -> dict[str, tuple[float, float]]:
    out, sec = {}, None
    with open(inp_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.split(";")[0].strip()
            if not s:
                continue
            if s.startswith("["):
                sec = s.upper()
                continue
            if sec == "[COORDINATES]":
                p = s.split()
                if len(p) >= 3:
                    out[p[0]] = (float(p[1]), float(p[2]))
    return out


def swmm_node_flooding(inp_path: str, rain_series: list[tuple[float, float]] | None = None) -> list[dict]:
    """Run SWMM (pyswmm) and return flooding volume per node: [{id, x, y, flood_m3}].

    rain_series [(t_s, mm/h)], if given, is set on all rain gages each step (overrides the .inp time series).
    Coordinates are taken from [COORDINATES] as written in the file (GECAA assumes EPSG:25832).
    Raises ImportError if pyswmm is missing.
    """
    from pyswmm import Nodes, RainGages, Simulation  # optional extra "sewer"
    coords = _coords(inp_path)
    vol: dict[str, float] = {}
    with Simulation(inp_path) as sim:
        nodes = list(Nodes(sim))
        gages = list(RainGages(sim))
        last = 0.0
        for _ in sim:
            t = (sim.current_time - sim.start_time).total_seconds()
            dt = t - last
            last = t
            if rain_series:
                i = next((v for ts, v in reversed(rain_series) if ts <= t), 0.0)
                for g in gages:
                    g.total_precip = i
            for n in nodes:
                vol[n.nodeid] = vol.get(n.nodeid, 0.0) + max(0.0, n.flooding) * dt
    out = []
    for nid, v in vol.items():
        if nid in coords:
            out.append({"id": nid, "x": coords[nid][0], "y": coords[nid][1], "flood_m3": round(v, 3)})
    return out
