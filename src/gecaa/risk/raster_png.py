# SPDX-License-Identifier: Apache-2.0
"""Rasters -> transparent PNG overlays for the web map (depth, hillshade, flow paths)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

# water depth classes (m) and colours, similar to the BKG/LANUK heavy-rain hazard maps
DEPTH_CLASSES = [(0.05, (198, 230, 247)), (0.10, (140, 200, 238)), (0.30, (66, 146, 214)),
                 (0.50, (26, 94, 176)), (1.00, (14, 52, 128)), (np.inf, (40, 16, 90))]
DEPTH_LEGEND = [("5–10 cm", "#c6e6f7"), ("10–30 cm", "#8cc8ee"), ("30–50 cm", "#4292d6"),
                ("50–100 cm", "#1a5eb0"), ("> 1 m", "#0e3480")]


def _png(rgba: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def depth_png(depth: np.ndarray, min_depth: float = 0.05) -> bytes:
    """Classes: [min_depth, 0.10), [0.10, 0.30), [0.30, 0.50), [0.50, 1.00), [1.00, inf)."""
    rgba = np.zeros(depth.shape + (4,), np.uint8)
    edges = [min_depth, 0.10, 0.30, 0.50, 1.00, np.inf]
    colours = [c[1] for c in DEPTH_CLASSES[:5]]
    for lo, hi, rgb in zip(edges[:-1], edges[1:], colours):
        m = (depth >= lo) & (depth < hi)
        rgba[m, :3] = rgb
        rgba[m, 3] = 215
    return _png(rgba)


def hillshade(dem: np.ndarray, res: float, azimuth: float = 315, altitude: float = 45, z: float = 2.0) -> np.ndarray:
    d = np.where(np.isnan(dem), np.nanmean(dem), dem)
    gy, gx = np.gradient(d * z, res)
    slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(360 - azimuth + 90), np.radians(altitude)
    shaded = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip(shaded, 0, 1)


def mercator_warper(transform, shape):
    """Function that warps an EPSG:25832 grid to EPSG:3857, plus the Leaflet bounds [[S, W], [N, E]]."""
    from pyproj import Transformer
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    h, w = shape
    left, top = transform.c, transform.f
    right, bottom = left + w * transform.a, top + h * transform.e
    dst_tr, dw, dh = calculate_default_transform("EPSG:25832", "EPSG:3857", w, h, left, bottom, right, top)

    def warp(arr: np.ndarray) -> np.ndarray:
        out = np.zeros((dh, dw), dtype="float32")
        reproject(arr.astype("float32"), out, src_transform=transform, src_crs="EPSG:25832", dst_transform=dst_tr,
                  dst_crs="EPSG:3857", resampling=Resampling.nearest, src_nodata=None, dst_nodata=0)
        return out

    to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    w_, s_ = to_ll.transform(dst_tr.c, dst_tr.f + dh * dst_tr.e)
    e_, n_ = to_ll.transform(dst_tr.c + dw * dst_tr.a, dst_tr.f)
    return warp, [[s_, w_], [n_, e_]]


def hillshade_png(dem: np.ndarray, res: float = 1.0, shaded: bool = False) -> bytes:
    """`shaded=True`: `dem` is already a hillshade (0..1), e.g. after warping."""
    hs = dem if shaded else hillshade(dem, res)
    rgba = np.zeros(dem.shape + (4,), np.uint8)
    g = (hs * 255).astype(np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = g
    rgba[..., 3] = (np.abs(hs - 0.7) * 255 * 0.9).clip(0, 160).astype(np.uint8)   # neutral slopes transparent
    return _png(rgba)


def flow_png(acc_m3: np.ndarray, threshold_m3: float = 5.0) -> bytes:
    """Flow paths: cells that carry more than `threshold_m3` runoff, log-scaled."""
    rgba = np.zeros(acc_m3.shape + (4,), np.uint8)
    m = acc_m3 > threshold_m3
    if m.any():
        v = np.log10(acc_m3[m] / threshold_m3)
        v = np.clip(v / max(v.max(), 1e-6), 0, 1)
        rgba[m, 0] = (20 + 30 * v).astype(np.uint8)
        rgba[m, 1] = (120 + 100 * v).astype(np.uint8)
        rgba[m, 2] = 255
        rgba[m, 3] = (110 + 140 * v).astype(np.uint8)
    return _png(rgba)


LST_COLOURS = [(20, "#2c7bb6"), (25, "#abd9e9"), (30, "#ffffbf"), (35, "#fdae61"), (40, "#f46d43"), (45, "#d73027"),
               (50, "#a50026")]


def lst_png(lst_c: np.ndarray) -> bytes:
    """Land surface temperature (°C) -> colour classes (below 20 °C transparent)."""
    rgba = np.zeros(lst_c.shape + (4,), np.uint8)
    edges = [c[0] for c in LST_COLOURS] + [np.inf]
    for (lo, col), hi in zip(LST_COLOURS, edges[1:]):
        m = (lst_c >= lo) & (lst_c < hi)
        rgba[m, :3] = [int(col[i:i + 2], 16) for i in (1, 3, 5)]
        rgba[m, 3] = 170
    return _png(rgba)
