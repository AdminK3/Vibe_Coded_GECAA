# SPDX-License-Identifier: Apache-2.0
"""Heat at a site: DWD hot days (observed, 1 km grid, since 1961) and LANUK climate analysis (PET, night).

DWD files (verified): opendata.dwd.de/climate_environment/CDC/grids_germany/annual/hot_days/
  grids_germany_annual_hot_days_{YYYY}_17.asc.gz  - Esri ASCII grid, 1 km, Gauss-Krüger zone 3 (EPSG:31467),
  NCOLS 654, NROWS 866, XLLCORNER 3280414.71, YLLCORNER 5237500.63, NODATA -999. Hot day: Tmax >= 30 °C.
PET classes (physiologically equivalent temperature, Matzarakis & Mayer 1996; VDI 3787 Blatt 2).
"""

from __future__ import annotations

import gzip
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx
import numpy as np
from pyproj import Transformer
from scipy.stats import theilslopes

BASE = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/annual/hot_days/"
SOURCE_DWD = "DWD, Rasterdaten Deutschland: jährliche Anzahl heißer Tage (Tmax ≥ 30 °C), 1 km (CC BY 4.0)"
SOURCE_LANUK = "LANUK NRW, Klimaanalyse NRW (WMS klimaanpassung_klimaanalyse, dl-de/zero-2-0)"
FIRST_YEAR = 1961
_to_gk3 = Transformer.from_crs("EPSG:4326", "EPSG:31467", always_xy=True)

PET_CLASSES = [(18, "keine Wärmebelastung"), (23, "schwache Wärmebelastung"), (29, "mäßige Wärmebelastung"),
               (35, "starke Wärmebelastung"), (41, "extreme Wärmebelastung")]


def pet_class(pet: float | None) -> str | None:
    if pet is None:
        return None
    label = "keine Wärmebelastung"
    for lo, name in PET_CLASSES:
        if pet >= lo:
            label = name
    return label


def read_asc_value(text: str, x: float, y: float) -> float | None:
    lines = text.splitlines()
    head = {}
    for line in lines[:6]:
        k, v = line.split()[:2]
        head[k.lower()] = float(v)
    ncols, nrows, cell = int(head["ncols"]), int(head["nrows"]), head["cellsize"]
    x0, y0 = head["xllcorner"], head["yllcorner"]
    col = int((x - x0) // cell)
    row = int((y0 + nrows * cell - y) // cell)
    if not (0 <= row < nrows and 0 <= col < ncols):
        return None
    vals = lines[6 + row].split()
    v = float(vals[col])
    return None if v == head.get("nodata_value", -999) else v


class HotDays:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def _text(self, year: int) -> str | None:
        name = f"grids_germany_annual_hot_days_{year}_17.asc.gz"
        path = self.cache_dir / name if self.cache_dir else None
        if path and path.exists():
            raw = path.read_bytes()
        else:
            try:
                r = httpx.get(BASE + name, timeout=30, headers={"User-Agent": "GECAA/0.3"})
            except httpx.HTTPError:
                return None
            if r.status_code != 200:
                return None
            raw = r.content
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        return gzip.decompress(raw).decode("latin-1")

    def series(self, lon: float, lat: float, last_year: int | None = None) -> dict:
        x, y = _to_gk3.transform(lon, lat)
        last = last_year or date.today().year - 1
        years = list(range(FIRST_YEAR, last + 1))

        def one(yr):
            t = self._text(yr)
            return yr, (read_asc_value(t, x, y) if t else None)

        with ThreadPoolExecutor(8) as pool:
            got = dict(pool.map(one, years))
        pts = [(yr, v) for yr, v in sorted(got.items()) if v is not None]
        if len(pts) < 10:
            raise ValueError("Zu wenige DWD-Jahreswerte für diesen Ort")
        yrs = np.array([p[0] for p in pts], float)
        vals = np.array([p[1] for p in pts], float)
        slope, intercept, lo, hi = theilslopes(vals, yrs)
        ref = vals[(yrs >= 1991) & (yrs <= 2020)]
        early = vals[(yrs >= 1961) & (yrs <= 1990)]
        return {"years": yrs.astype(int).tolist(), "values": vals.round(1).tolist(),
                "mean_1961_1990": round(float(early.mean()), 1) if early.size else None,
                "mean_1991_2020": round(float(ref.mean()), 1) if ref.size else None,
                "mean_last10": round(float(vals[-10:].mean()), 1),
                "trend_per_decade": round(float(slope * 10), 2),
                "trend_ci_per_decade": [round(float(lo * 10), 2), round(float(hi * 10), 2)],
                "trend_line": [[int(yrs[0]), round(float(intercept + slope * yrs[0]), 2)],
                               [int(yrs[-1]), round(float(intercept + slope * yrs[-1]), 2)]],
                "source": SOURCE_DWD, "method": "Trend nach Theil-Sen (robust), 95-%-Konfidenzintervall"}


def assess(provider, lon: float, lat: float, hot_days: HotDays | None) -> dict:
    from ..geodata.base import to_utm

    out: dict = {"lon": lon, "lat": lat}
    if hot_days is not None:
        try:
            out["hot_days"] = hot_days.series(lon, lat)
        except Exception as exc:  # network or data gap
            out["hot_days_error"] = str(exc)[:200]
    pt = to_utm({"type": "Point", "coordinates": [lon, lat]})
    try:
        ca = provider.climate_analysis(pt.x, pt.y) if hasattr(provider, "climate_analysis") else {}
    except Exception as exc:
        ca, out["climate_analysis_error"] = {}, str(exc)[:200]
    if ca:
        ca = dict(ca)
        ca["pet_typisch_klasse"] = pet_class(ca.get("pet_typisch"))
        ca["pet_extrem_klasse"] = pet_class(ca.get("pet_extrem"))
        ca["source"] = SOURCE_LANUK
        out["climate_analysis"] = ca
    return out
