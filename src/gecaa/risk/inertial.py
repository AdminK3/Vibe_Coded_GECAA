"""2D local-inertial shallow-water solver (Bates, Horritt & Fewtrell 2010, J. Hydrol. 387).

This is the explicit scheme behind LISFLOOD-FP "ACC", written from the paper with plain numpy
(no LISFLOOD-FP code). Water depth lives on cells and unit discharge on faces:

    q_new = (q - g h_f dt dEta/dx) / (1 + g dt n^2 |q| / h_f^(7/3))
    dh/dt = -(div q) + rain * coeff

with h_f = max(eta_l, eta_r) - max(z_l, z_r), an adaptive step dt = alpha dx / sqrt(g h_max) and a
per-cell outflow limiter that keeps depths non-negative and mass exact. Edges are open (free
outflow): a ghost cell outside every edge mirrors the outward surface gradient, only outflow is
allowed. Building cells are walls: no flux through their faces and the rain on them is ignored
(roof water is assumed to go to the sewer).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

__version__ = "1.0"
G = np.float32(9.81)
H_MIN = np.float32(1e-3)  # no flow below this face depth (m)


@dataclass
class InertialResult:
    depth_max: np.ndarray
    velocity_max: np.ndarray
    time_of_max: np.ndarray  # s
    depth_final: np.ndarray
    volume: dict = field(default_factory=dict)  # m3: rain_in, outflow, stored, error, rel_error
    steps: int = 0
    runtime_s: float = 0.0


def _rain_rate(rain_mm_h, duration_s):
    """Return (rates in m/s per block, block length s)."""
    r = np.atleast_1d(np.asarray(rain_mm_h, dtype="float64"))
    return r / 3.6e6, duration_s / len(r)


def simulate(dem, res, rain_mm_h, duration_s=3600.0, drain_s=1800.0, coeff=1.0, manning=0.03,
             buildings=None, alpha=0.7, dt_max=10.0, boundary="open", h0=None) -> InertialResult:
    """Run the local-inertial model.

    dem: 2D array (m), res: cell size (m), rain_mm_h: scalar (block rain) or list of block
    intensities spread evenly over duration_s, coeff/manning: scalar or grid, buildings: bool
    mask (walls), drain_s: extra time without rain, boundary: "open" or "closed",
    h0: optional initial depth grid (m); its volume counts as input.
    """
    t0 = time.perf_counter()
    f = np.float32
    z = np.asarray(dem, dtype=f)
    ny, nx = z.shape
    dx = f(res)
    wall = np.zeros((ny, nx), bool) if buildings is None else np.asarray(buildings, bool)
    coeff = np.broadcast_to(np.asarray(coeff, f), (ny, nx)) * (~wall)
    n2 = np.broadcast_to(np.asarray(manning, f) ** 2, (ny, nx)).astype(f)
    n2x = np.empty((ny, nx + 1), f); n2x[:, 1:-1] = 0.5 * (n2[:, 1:] + n2[:, :-1])
    n2x[:, 0], n2x[:, -1] = n2[:, 0], n2[:, -1]
    n2y = np.empty((ny + 1, nx), f); n2y[1:-1] = 0.5 * (n2[1:] + n2[:-1])
    n2y[0], n2y[-1] = n2[0], n2[-1]
    # faces blocked by walls (or by a closed domain edge)
    openx = np.ones((ny, nx + 1), bool); openx[:, 1:-1] = ~(wall[:, 1:] | wall[:, :-1])
    openx[:, 0] &= ~wall[:, 0]; openx[:, -1] &= ~wall[:, -1]
    openy = np.ones((ny + 1, nx), bool); openy[1:-1] = ~(wall[1:] | wall[:-1])
    openy[0] &= ~wall[0]; openy[-1] &= ~wall[-1]
    if boundary == "closed":
        openx[:, [0, -1]] = False; openy[[0, -1]] = False

    rates, block = _rain_rate(rain_mm_h, duration_s)
    t_end = duration_s + drain_s
    h = np.zeros((ny, nx), f) if h0 is None else np.where(wall, 0, np.asarray(h0, f)).astype(f)
    qx = np.zeros((ny, nx + 1), f)
    qy = np.zeros((ny + 1, nx), f)
    hmax = np.zeros_like(h); vmax = np.zeros_like(h); tmax = np.zeros_like(h)
    rain_in = float(h.sum(dtype="float64")) * float(res) ** 2
    outflow = 0.0
    t, steps = 0.0, 0
    area = float(dx) * float(dx)
    eta_x = np.empty((ny, nx + 2), f); z_x = np.empty((ny, nx + 2), f)
    eta_y = np.empty((ny + 2, nx), f); z_y = np.empty((ny + 2, nx), f)

    def faces(eta_g, z_g, q, n2f, op, axis):
        sl = (slice(None), slice(1, None)) if axis == 1 else (slice(1, None), slice(None))
        sr = (slice(None), slice(None, -1)) if axis == 1 else (slice(None, -1), slice(None))
        er, el = eta_g[sl], eta_g[sr]
        hf = np.maximum(er, el) - np.maximum(z_g[sl], z_g[sr])
        wet = (hf > H_MIN) & op
        hf = np.where(wet, hf, f(1.0))
        qn = (q - G * hf * dt32 * (er - el) / dx) / (1 + G * dt32 * n2f * np.abs(q) / hf ** f(7 / 3))
        return np.where(wet, qn, f(0.0))

    while t < t_end - 1e-9:
        hm = float(h.max())
        dt = dt_max if hm < H_MIN else min(dt_max, alpha * float(dx) / np.sqrt(9.81 * hm))
        if t < duration_s:  # do not straddle rain blocks
            dt = min(dt, (np.floor(t / block + 1e-9) + 1) * block - t)
        dt = min(dt, t_end - t)
        dt32 = f(dt)
        eta = z + h
        # ghost cells: mirror the outward surface/bed gradient so water leaves freely
        eta_x[:, 1:-1] = eta; z_x[:, 1:-1] = z
        eta_x[:, 0] = eta[:, 0] - np.maximum(eta[:, 1] - eta[:, 0], 0); z_x[:, 0] = z[:, 0] - np.maximum(z[:, 1] - z[:, 0], 0)
        eta_x[:, -1] = eta[:, -1] - np.maximum(eta[:, -2] - eta[:, -1], 0); z_x[:, -1] = z[:, -1] - np.maximum(z[:, -2] - z[:, -1], 0)
        eta_y[1:-1] = eta; z_y[1:-1] = z
        eta_y[0] = eta[0] - np.maximum(eta[1] - eta[0], 0); z_y[0] = z[0] - np.maximum(z[1] - z[0], 0)
        eta_y[-1] = eta[-1] - np.maximum(eta[-2] - eta[-1], 0); z_y[-1] = z[-1] - np.maximum(z[-2] - z[-1], 0)
        qx = faces(eta_x, z_x, qx, n2x, openx, 1)
        qy = faces(eta_y, z_y, qy, n2y, openy, 0)
        # edges: outflow only
        qx[:, 0] = np.minimum(qx[:, 0], 0); qx[:, -1] = np.maximum(qx[:, -1], 0)
        qy[0] = np.minimum(qy[0], 0); qy[-1] = np.maximum(qy[-1], 0)
        # limiter: a cell cannot export more than it holds
        out = (np.maximum(qx[:, 1:], 0) - np.minimum(qx[:, :-1], 0)
               + np.maximum(qy[1:], 0) - np.minimum(qy[:-1], 0)) * dt32 / dx
        fac = np.where(out > h, h / np.maximum(out, f(1e-12)), f(1.0))
        if (fac < 1).any():
            fx = np.ones((ny, nx + 2), f); fx[:, 1:-1] = fac
            qx = np.where(qx > 0, qx * fx[:, :nx + 1], qx * fx[:, 1:])
            fy = np.ones((ny + 2, nx), f); fy[1:-1] = fac
            qy = np.where(qy > 0, qy * fy[:ny + 1], qy * fy[1:])
        r = rates[min(int(t / block + 1e-9), len(rates) - 1)] if t < duration_s else 0.0
        src = coeff * f(r * dt)
        h = h + (qx[:, :-1] - qx[:, 1:] + qy[:-1] - qy[1:]) * dt32 / dx + src
        np.maximum(h, 0, out=h)
        rain_in += float(src.sum(dtype="float64")) * area
        outflow += (float(qx[:, -1].sum(dtype="float64") - qx[:, 0].sum(dtype="float64")
                          + qy[-1].sum(dtype="float64") - qy[0].sum(dtype="float64")) * dt * float(dx))
        t += dt; steps += 1
        deeper = h > hmax
        hmax = np.where(deeper, h, hmax); tmax = np.where(deeper, f(t), tmax)
        hs = np.maximum(h, H_MIN)
        u = 0.5 * (np.abs(qx[:, 1:]) + np.abs(qx[:, :-1])) / hs
        v = 0.5 * (np.abs(qy[1:]) + np.abs(qy[:-1])) / hs
        vel = np.where(h > 0.01, np.sqrt(u * u + v * v), f(0.0))
        np.maximum(vmax, vel, out=vmax)

    stored = float(h.sum(dtype="float64")) * area
    err = rain_in - outflow - stored
    return InertialResult(hmax, vmax, tmax, h, {
        "rain_in_m3": rain_in, "outflow_m3": float(outflow), "stored_m3": stored, "error_m3": float(err),
        "rel_error": float(err / rain_in) if rain_in else 0.0}, steps, time.perf_counter() - t0)
