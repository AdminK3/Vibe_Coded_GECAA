# SPDX-License-Identifier: Apache-2.0
"""Residents per 100 m cell (Zensus 2022, Destatis, dl-de/by-2-0) and residents aged 65+.

Destatis publishes the grid only as ZIP/CSV downloads (no WMS/WFS/OGC API found; the Zensus-Atlas is a
web map without a documented service endpoint). Download URLs VERIFIED on the Destatis Zensus 2022 page
(2026-10), file contents/column names NOT verified (sandbox has no access):
  https://www.destatis.de/static/DE/zensus/gitterdaten/Zensus2022_Bevoelkerungszahl.zip
  https://www.destatis.de/static/DE/zensus/gitterdaten/Alter_in_5_Altersklassen.zip
Run `python scripts/prepare_zensus.py` once; it writes data/cache/zensus/zensus2022_nrw_100m.csv with
columns x,y (cell centre, EPSG:25832), residents, age65. Cells kept secret by Destatis stay empty (None).
"""

from __future__ import annotations

import csv
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[3] / "data" / "cache" / "zensus" / "zensus2022_nrw_100m.csv"
SOURCE = "Statistisches Bundesamt (Destatis), Zensus 2022, 100-m-Gitter (dl-de/by-2-0)"


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def population(provider_or_none, bbox_utm, path: Path | None = None) -> list[dict] | None:
    """Cells whose centre lies in bbox_utm: [{x, y, residents, age65}]. None if the prepared file is missing."""
    path = Path(path) if path else DEFAULT_FILE
    if provider_or_none is not None and getattr(provider_or_none, "cache_dir", None) and not path.exists():
        alt = Path(provider_or_none.cache_dir).parent / "zensus" / DEFAULT_FILE.name
        path = alt if alt.exists() else path
    if not path.exists():
        return None
    x0, y0, x1, y1 = bbox_utm
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            x, y = float(r["x"]), float(r["y"])
            if x0 <= x <= x1 and y0 <= y <= y1:
                out.append({"x": x, "y": y, "residents": _int(r.get("residents")), "age65": _int(r.get("age65"))})
    return out
