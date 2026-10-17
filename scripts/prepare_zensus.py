# SPDX-License-Identifier: Apache-2.0
"""Download Zensus 2022 100 m grid (residents, age classes) once, keep NRW, write a compact CSV.

Usage: python scripts/prepare_zensus.py [--out data/cache/zensus] [--keep-zips]
Columns are detected by name (Einwohner / a column containing "65"), since the header is not verified.
Grid coordinates are EPSG:3035 cell centres (x_mp_100m, y_mp_100m); output is EPSG:25832.
NRW is selected by the WGS84 box NRW_BOUNDS (as NRWProvider), so some border cells of neighbouring states remain.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import zipfile
from pathlib import Path

import httpx
from pyproj import Transformer

BASE = "https://www.destatis.de/static/DE/zensus/gitterdaten/"
FILES = {"residents": "Zensus2022_Bevoelkerungszahl.zip", "age": "Alter_in_5_Altersklassen.zip"}
NRW = (5.86, 50.32, 9.47, 52.54)


def _rows(zpath: Path):
    with zipfile.ZipFile(zpath) as z:
        name = max((n for n in z.namelist() if n.lower().endswith(".csv")), key=lambda n: z.getinfo(n).file_size)
        raw = z.read(name)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sep = ";" if text[:2000].count(";") >= text[:2000].count(",") else ","
    yield from csv.DictReader(io.StringIO(text), delimiter=sep)


def _col(fields, *needles, exclude=()):
    for f in fields:
        lf = f.lower()
        if all(n in lf for n in needles) and not any(e in lf for e in exclude):
            return f
    return None


def _val(v):
    v = (v or "").strip().replace(",", ".")
    try:
        return int(float(v))
    except ValueError:
        return None   # secret / dash


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/cache/zensus")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in FILES.values():
        p = out / f
        if not p.exists():
            print("downloading", BASE + f)
            with httpx.stream("GET", BASE + f, timeout=600, follow_redirects=True) as r:
                r.raise_for_status()
                with p.open("wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
    t3035 = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    xs, ys = zip(*(t3035.transform(lo, la) for lo in NRW[::2] for la in NRW[1::2]))
    ex = (min(xs), min(ys), max(xs), max(ys))
    to_wgs = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:3035", "EPSG:25832", always_xy=True)

    cells: dict[str, dict] = {}
    for kind in ("residents", "age"):
        rows = _rows(out / FILES[kind])
        first = next(rows)
        fields = list(first)
        cx, cy = _col(fields, "x_mp"), _col(fields, "y_mp")
        cid = _col(fields, "gitter_id")
        vcol = _col(fields, "einwohner") if kind == "residents" else _col(fields, "65")
        print(kind, "columns:", fields, "->", cx, cy, vcol)
        if not (cx and cy and vcol):
            sys.exit(f"Spalten nicht erkannt in {FILES[kind]}: {fields}")
        for r in [first, *rows]:
            x, y = float(r[cx]), float(r[cy])
            if not (ex[0] <= x <= ex[2] and ex[1] <= y <= ex[3]):
                continue
            lon, lat = to_wgs.transform(x, y)
            if not (NRW[0] <= lon <= NRW[2] and NRW[1] <= lat <= NRW[3]):
                continue
            c = cells.setdefault(r.get(cid) or f"{x}_{y}", {"x3035": x, "y3035": y, "residents": None, "age65": None})
            c["residents" if kind == "residents" else "age65"] = _val(r[vcol])
    target = out / "zensus2022_nrw_100m.csv"
    with target.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["x", "y", "residents", "age65"])
        for c in cells.values():
            ux, uy = to_utm.transform(c["x3035"], c["y3035"])
            w.writerow([round(ux, 1), round(uy, 1), "" if c["residents"] is None else c["residents"],
                        "" if c["age65"] is None else c["age65"]])
    print(f"{len(cells)} cells -> {target}")


if __name__ == "__main__":
    main()
