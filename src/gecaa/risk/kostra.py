# SPDX-License-Identifier: Apache-2.0
"""KOSTRA-DWD-2020 design rainfall for a point (Deutscher Wetterdienst, open data, CC BY 4.0).

Files (verified): .../KOSTRA_DWD_2020/tab/StatRR_KOSTRA-DWD-2020_D{minutes:05d}.csv.zip
  INDEX_RC;HN_001A;...;HN_100A;RN_...;UC_...   (';' separated, decimal comma, -99,9 = no data)
Grid (from the ASC version of the same data): 300 x 300 cells of 5 km, lower left corner (3600000, 2300000)
in ETRS89 / LCC Europe (EPSG:3034). INDEX_RC = row (from the top) * 1000 + column, written with 6 digits.
"""

from __future__ import annotations

import io
import zipfile
from functools import lru_cache
from pathlib import Path

import httpx
from pyproj import Transformer

BASE = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/return_periods/precipitation/KOSTRA/KOSTRA_DWD_2020/"
X0, Y0, CELL, NROWS, NCOLS = 3_600_000.0, 2_300_000.0, 5_000.0, 300, 300
RPS = (1, 2, 3, 5, 10, 20, 30, 50, 100)
DURATIONS = (5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 540, 720, 1080, 1440, 2880, 4320)
SOURCE = "KOSTRA-DWD-2020, Deutscher Wetterdienst (CC BY 4.0)"
_to_lcc = Transformer.from_crs("EPSG:4326", "EPSG:3034", always_xy=True)


def cell_index(lon: float, lat: float) -> str | None:
    x, y = _to_lcc.transform(lon, lat)
    col = int((x - X0) // CELL)
    row = int((Y0 + NROWS * CELL - y) // CELL)
    if not (0 <= row < NROWS and 0 <= col < NCOLS):
        return None
    return f"{row:03d}{col:03d}"


def parse_table(text: str) -> dict[str, dict[int, float]]:
    lines = [l for l in text.splitlines() if l.strip()]
    head = [h.strip().upper() for h in lines[0].split(";")]
    cols = {int(h[3:6]): i for i, h in enumerate(head) if h.startswith("HN_") and h.endswith("A")}
    out = {}
    for line in lines[1:]:
        parts = line.split(";")
        vals = {}
        for rp, i in cols.items():
            v = float(parts[i].replace(",", "."))
            if v > -99:
                vals[rp] = v
        if vals:
            out[parts[0].strip()] = vals
    return out


class Kostra:
    def __init__(self, cache_dir: Path | None = None, timeout: float = 60.0):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout

    def _zip(self, duration: int) -> bytes:
        name = f"StatRR_KOSTRA-DWD-2020_D{duration:05d}.csv.zip"
        path = self.cache_dir / name if self.cache_dir else None
        if path and path.exists():
            return path.read_bytes()
        r = httpx.get(BASE + "tab/" + name, timeout=self.timeout, headers={"User-Agent": "GECAA/0.3"})
        r.raise_for_status()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        return r.content

    @lru_cache(maxsize=4)
    def table(self, duration: int) -> dict[str, dict[int, float]]:
        z = zipfile.ZipFile(io.BytesIO(self._zip(duration)))
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return parse_table(z.read(name).decode("latin-1"))

    def depths(self, lon: float, lat: float, duration: int = 60) -> dict:
        if duration not in DURATIONS:
            raise ValueError(f"Dauerstufe {duration} min gibt es in KOSTRA nicht")
        idx = cell_index(lon, lat)
        if idx is None:
            raise ValueError("Ort liegt außerhalb des KOSTRA-Rasters")
        vals = self.table(duration).get(idx)
        if not vals:
            raise ValueError(f"KOSTRA hat für Rasterfeld {idx} keine Werte")
        return {"duration_min": duration, "cell": idx, "mm": vals, "source": SOURCE}
