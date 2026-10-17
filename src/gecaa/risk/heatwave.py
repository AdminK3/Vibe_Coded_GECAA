# SPDX-License-Identifier: Apache-2.0
"""Heatwaves and tropical nights at the nearest DWD climate station (daily data, xclim).

DWD CDC (CC BY 4.0), daily climate data "kl":
  .../observations_germany/climate/daily/kl/historical/KL_Tageswerte_Beschreibung_Stationen.txt
    VERIFIED (WebFetch 2026-10): fixed width, latin-1, header + dashed line,
    "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland Abgabe".
  .../historical/tageswerte_KL_{id:05d}_{von}_{bis}_hist.zip containing produkt_klima_tag_*.txt
    (";"-separated, TXK = Tmax, TNK = Tmin in °C, -999 missing) - file layout from DWD documentation,
    NOT fetched here (check with scripts/probe_v4.py).
Indicators (CLIMAAX heatwave definition): xclim heat_wave_frequency / heat_wave_max_length with
tasmin > 22 °C and tasmax > 28 °C for at least 3 days; tropical nights TN >= 20 °C per year.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/historical/"
STATIONS = BASE + "KL_Tageswerte_Beschreibung_Stationen.txt"
SOURCE = "DWD Climate Data Center, tägliche Stationsmessungen (kl, historisch), CC BY 4.0"
UA = {"User-Agent": "GECAA/0.4"}


def parse_stations(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d{8})\s+(\d{8})\s+(-?\d+)\s+([\d.]+)\s+([\d.]+)\s+(.+?)\s{2,}(\S.*?)\s{2,}", line + "  ")
        if m:
            out.append({"id": int(m[1]), "from": m[2], "to": m[3], "height": int(m[4]), "lat": float(m[5]),
                        "lon": float(m[6]), "name": m[7].strip(), "state": m[8].strip()})
    return out


def _km(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return 6371 * math.acos(min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)))


def nearest_station(stations, lon, lat, min_years: int = 30, active_since: str = "20200101") -> dict | None:
    ok = [s for s in stations if s["to"] >= active_since and int(s["to"][:4]) - int(s["from"][:4]) >= min_years]
    if not ok:
        return None
    s = min(ok, key=lambda s: _km(lat, lon, s["lat"], s["lon"]))
    return {**s, "distance_km": round(_km(lat, lon, s["lat"], s["lon"]), 1)}


def read_product_zip(data: bytes) -> pd.DataFrame:
    """DWD zip -> DataFrame indexed by date with columns tasmax, tasmin (°C, NaN where missing)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.startswith("produkt_klima_tag"))
        df = pd.read_csv(z.open(name), sep=";", skipinitialspace=True, encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]
    df["date"] = pd.to_datetime(df["MESS_DATUM"].astype(str), format="%Y%m%d")
    out = df.set_index("date")[["TXK", "TNK"]].astype(float).replace(-999.0, np.nan)
    return out.rename(columns={"TXK": "tasmax", "TNK": "tasmin"})


def indicators(df: pd.DataFrame) -> list[dict]:
    """Per-year heatwave frequency, max length and tropical nights from a daily tasmax/tasmin frame."""
    import xarray as xr
    from xclim.indicators import atmos
    from xclim.indices import tn_days_above

    full = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="D"))

    def da(col):
        return xr.DataArray(full[col].to_numpy(), coords={"time": full.index}, dims="time",
                            attrs={"units": "degC", "standard_name": "air_temperature",
                                   "cell_methods": "time: " + ("maximum" if col == "tasmax" else "minimum")})

    tn, tx = da("tasmin"), da("tasmax")
    kw = dict(thresh_tasmin="22.0 degC", thresh_tasmax="28.0 degC", window=3, freq="YS")
    freq = atmos.heat_wave_frequency(tasmin=tn, tasmax=tx, **kw)
    mlen = atmos.heat_wave_max_length(tasmin=tn, tasmax=tx, **kw)
    trop = tn_days_above(tn, thresh="20.0 degC", freq="YS", op=">=")
    # mask years with any missing day (tn_days_above has no missing-value check)
    miss = full[["tasmin", "tasmax"]].isna().any(axis=1).groupby(full.index.year).any()

    def val(a, i):
        v = float(a.values[i])
        return None if np.isnan(v) else int(round(v))

    out = []
    for i, t in enumerate(pd.DatetimeIndex(freq.time.values)):
        bad = bool(miss.get(t.year, True))
        out.append({"year": t.year, "heatwaves": None if bad else val(freq, i),
                    "heatwave_max_days": None if bad else val(mlen, i),
                    "tropical_nights": None if bad else val(trop, i)})
    return out


class DWDDaily:
    def __init__(self, cache_dir: Path | None = None, timeout: float = 60.0):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout

    def _fetch(self, url: str, name: str) -> bytes:
        p = self.cache_dir / name if self.cache_dir else None
        if p and p.exists():
            return p.read_bytes()
        r = httpx.get(url, headers=UA, timeout=self.timeout)
        r.raise_for_status()
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
        return r.content

    def stations(self):
        return parse_stations(self._fetch(STATIONS, "kl_stations.txt").decode("latin-1"))

    def station_zip(self, sid: int) -> bytes:
        listing = self._fetch(BASE, "kl_historical_index.html").decode("latin-1")
        m = re.findall(rf"tageswerte_KL_{sid:05d}_\d{{8}}_\d{{8}}_hist\.zip", listing)
        if not m:
            raise LookupError(f"keine Tageswerte-Datei für Station {sid}")
        return self._fetch(BASE + m[-1], m[-1])


def heatwaves(lon: float, lat: float, cache_dir: Path | None = None, client: DWDDaily | None = None) -> dict | None:
    """Station, per-year indicators and means; None when DWD cannot be reached (nothing is estimated)."""
    client = client or DWDDaily(cache_dir)
    try:
        st = nearest_station(client.stations(), lon, lat)
        if st is None:
            return None
        rows = indicators(read_product_zip(client.station_zip(st["id"])))
    except (httpx.HTTPError, OSError, LookupError, zipfile.BadZipFile):
        return None
    def mean(k, last=30):
        v = [r[k] for r in rows[-last:] if r[k] is not None]
        return round(sum(v) / len(v), 2) if v else None
    return {"station": st, "years": rows, "mean_last30": {k: mean(k) for k in ("heatwaves", "heatwave_max_days",
            "tropical_nights")}, "source": SOURCE,
            "method": "xclim heat_wave_frequency/max_length (TN>22 °C, TX>28 °C, >=3 Tage, CLIMAAX); Tropennacht TN>=20 °C"}
