# SPDX-License-Identifier: GPL-3.0-or-later
"""GECAA LISFLOOD-FP worker (GPL-3.0-or-later). Runs the LISFLOOD-FP binary as a subprocess.

GECAA (Apache-2.0) talks to this service over HTTP only; it never links or imports LISFLOOD-FP.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

BIN = os.environ.get("LISFLOOD_BIN", os.path.expanduser("~/.local/lisflood/bin/lisflood"))
TIMEOUT = float(os.environ.get("LISFLOOD_TIMEOUT_S", "900"))
NODATA = -9999.0
WALL_M = 10.0  # building cells are raised by this much

app = FastAPI(title="GECAA LISFLOOD-FP worker")


class SimRequest(BaseModel):
    dem: str  # base64 float32, row-major (north row first), ny*nx
    nx: int
    ny: int
    res: float
    rain_mm_h: list[float]
    dt_s: float  # length of each rain block
    duration_s: float  # total simulated time (rain + drain)
    manning: float | str = 0.03  # scalar or base64 float32 grid
    mask: str | None = None  # base64 uint8 building mask


def _grid(b64: str, dtype, ny, nx):
    a = np.frombuffer(base64.b64decode(b64), dtype=dtype)
    if a.size != ny * nx:
        raise HTTPException(422, f"grid has {a.size} values, expected {ny * nx}")
    return a.reshape(ny, nx).astype("float64")


def write_ascii(path: Path, a: np.ndarray, res: float):
    ny, nx = a.shape
    hdr = f"ncols {nx}\nnrows {ny}\nxllcorner 0\nyllcorner 0\ncellsize {res}\nNODATA_value {NODATA}\n"
    with open(path, "w") as f:
        f.write(hdr)
        np.savetxt(f, a, fmt="%.4f")


def read_ascii(path: Path) -> np.ndarray:
    a = np.loadtxt(path, skiprows=6)
    a[a == NODATA] = 0
    return a


def build_case(req: SimRequest, d: Path) -> Path:
    dem = _grid(req.dem, "<f4", req.ny, req.nx)
    if req.mask:
        dem = dem + WALL_M * _grid(req.mask, "u1", req.ny, req.nx)
    write_ascii(d / "dem.asc", dem, req.res)
    par = [f"DEMfile dem.asc", "resroot res", "dirroot out", f"sim_time {req.duration_s}",
           "initial_tstep 1.0", f"massint {max(req.dt_s, 60)}", f"saveint {req.duration_s}",
           "acceleration", "rainfall rain.rain", "bcifile bc.bci", "elevoff", "max_Froude 1"]
    if isinstance(req.manning, str):
        write_ascii(d / "n.asc", _grid(req.manning, "<f4", req.ny, req.nx), req.res)
        par.append("manningfile n.asc")
    else:
        par.append(f"fpfric {req.manning}")
    (d / "run.par").write_text("\n".join(par) + "\n")
    # rain: piecewise constant blocks then zero (LISFLOOD-FP .rain: header, count + unit, value time)
    rows = [(r, i * req.dt_s) for i, r in enumerate(req.rain_mm_h)]
    rows.append((0.0, len(req.rain_mm_h) * req.dt_s))
    rows.append((0.0, max(req.duration_s, rows[-1][1]) + 1))
    (d / "rain.rain").write_text("# GECAA rain mm/h\n%d seconds\n" % len(rows)
                                 + "".join(f"{r:.4f} {t:.1f}\n" for r, t in rows))
    (d / "bc.bci").write_text("".join(f"{s} -1e9 1e9 FREE\n" for s in "NSEW"))
    return d / "run.par"


@app.get("/health")
def health():
    ok = shutil.which(BIN) is not None or Path(BIN).is_file()
    ver = ""
    if ok:
        try:
            ver = subprocess.run([BIN, "-version"], capture_output=True, text=True, timeout=10).stdout.strip()[:200]
        except Exception:  # noqa: BLE001
            pass
    return {"ok": ok, "engine": "LISFLOOD-FP", "binary": BIN, "version": ver}


@app.post("/simulate")
def simulate(req: SimRequest):
    if not health()["ok"]:
        raise HTTPException(503, f"LISFLOOD-FP binary not found at {BIN}")
    with tempfile.TemporaryDirectory(prefix="lisflood_") as tmp:
        d = Path(tmp)
        par = build_case(req, d)
        try:
            p = subprocess.run([BIN, "-v", par.name], cwd=d, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise HTTPException(504, f"LISFLOOD-FP timed out after {TIMEOUT} s") from e
        log = (p.stdout + p.stderr)[-4000:]
        mx = d / "out" / "res.max"
        if p.returncode != 0 or not mx.exists():
            raise HTTPException(500, {"error": "LISFLOOD-FP failed", "returncode": p.returncode, "log_tail": log})
        depth = read_ascii(mx).astype("<f4")
    return {"depth_max": base64.b64encode(depth.tobytes()).decode(), "nx": req.nx, "ny": req.ny,
            "engine": "LISFLOOD-FP", "version": health()["version"], "log_tail": log}
