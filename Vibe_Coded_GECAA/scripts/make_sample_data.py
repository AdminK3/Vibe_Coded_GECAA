# SPDX-License-Identifier: Apache-2.0
"""Generate the SYNTHETIC demo dataset in data/samples.

The rasters and zones are invented for testing. They are placed in UTM 32N
(EPSG:25832, the NRW standard CRS) near Mettmann only so coordinates look
realistic. They say nothing about the real ground there.

Run: python scripts/make_sample_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

OUT = Path(__file__).resolve().parent.parent / "data" / "samples"
CRS = "EPSG:25832"
X0, Y0 = 356000.0, 5680400.0   # top-left corner
RES = 2.0                      # metres per pixel
N = 200                        # 400 m x 400 m


def grid():
    cols, rows = np.meshgrid(np.arange(N), np.arange(N))
    x = cols * RES          # metres east of the west edge
    y = rows * RES          # metres south of the north edge
    return x, y


def write(name: str, arr: np.ndarray, description: str):
    profile = dict(driver="GTiff", height=N, width=N, count=1, dtype="float32", crs=CRS,
                   transform=from_origin(X0, Y0, RES, RES), nodata=-9999.0, compress="deflate")
    with rasterio.open(OUT / name, "w", **profile) as dst:
        dst.write(arr.astype("float32"), 1)
        dst.update_tags(DESCRIPTION=description, SYNTHETIC="true")


def poly(coords):
    ring = [[X0 + cx, Y0 - cy] for cx, cy in coords]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    x, y = grid()
    rng = np.random.default_rng(42)

    # Terrain: gentle 2 % slope towards the south, steepening to ~13 % in the east.
    east = np.clip((x - 280) / 120, 0, 1)
    dem = 60.0 - 0.02 * y - 0.13 * east * (x - 280).clip(0) + rng.normal(0, 0.02, x.shape)
    write("dem.tif", dem, "Synthetic terrain, m above sea level")

    # Permeability as log10(kf in m/s): sandy west (-4.5), clayey east (-6.8).
    t = np.clip((x - 180) / 120, 0, 1)
    log_kf = -4.5 * (1 - t) + -6.8 * t + rng.normal(0, 0.15, x.shape)
    write("kf_log10.tif", log_kf, "Synthetic soil permeability, log10(kf [m/s])")

    # Depth to mean highest groundwater: 3.5 m, rising to 1.2 m near a stream in the south.
    stream = np.clip((y - 300) / 100, 0, 1)
    gw = 3.5 * (1 - stream) + 1.2 * stream + rng.normal(0, 0.1, x.shape)
    write("groundwater_depth.tif", gw, "Synthetic depth to mean highest groundwater level, m")

    zones = {
        "type": "FeatureCollection",
        "name": "synthetic_water_protection_zones",
        "crs": {"type": "name", "properties": {"name": CRS}},
        "features": [
            {"type": "Feature", "properties": {"zone": "III", "name": "Demo-Wasserwerk, Zone III"},
             "geometry": poly([(240, 0), (400, 0), (400, 200), (240, 200)])},
            {"type": "Feature", "properties": {"zone": "II", "name": "Demo-Wasserwerk, Zone II"},
             "geometry": poly([(340, 20), (400, 20), (400, 90), (340, 90)])},
        ],
    }
    (OUT / "water_protection_zones.geojson").write_text(json.dumps(zones, indent=1), encoding="utf-8")

    parcels = {
        "type": "FeatureCollection",
        "name": "synthetic_parcels",
        "crs": {"type": "name", "properties": {"name": CRS}},
        "features": [
            {"type": "Feature",
             "properties": {"id": "demo-a", "name": "Schulhof West (demo)", "connected_area_m2": 2400,
                            "available_area_m2": 420, "contamination": "none_known"},
             "geometry": poly([(40, 60), (110, 60), (110, 130), (40, 130)])},
            {"type": "Feature",
             "properties": {"id": "demo-b", "name": "Gewerbefläche Ost (demo)", "connected_area_m2": 5200,
                            "available_area_m2": 610, "contamination": "unknown"},
             "geometry": poly([(260, 110), (330, 110), (330, 180), (260, 180)])},
            {"type": "Feature",
             "properties": {"id": "demo-c", "name": "Wohnquartier am Bach (demo)", "connected_area_m2": 3100,
                            "available_area_m2": 350, "contamination": "none_known"},
             "geometry": poly([(60, 320), (150, 320), (150, 390), (60, 390)])},
        ],
    }
    (OUT / "parcels.geojson").write_text(json.dumps(parcels, indent=1), encoding="utf-8")
    print(f"wrote synthetic sample data to {OUT}")


if __name__ == "__main__":
    main()
