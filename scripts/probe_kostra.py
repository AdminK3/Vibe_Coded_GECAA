# SPDX-License-Identifier: Apache-2.0
"""Find how KOSTRA-DWD-2020 INDEX_RC relates to the ASC grid (run where opendata.dwd.de is reachable)."""

import io
import zipfile

import httpx

B = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/return_periods/precipitation/KOSTRA/KOSTRA_DWD_2020/"
asc = zipfile.ZipFile(io.BytesIO(httpx.get(B + "asc/StatRR_KOSTRA-DWD-2020_D00060_ASC.zip", timeout=60).content))
grid = asc.read("Hn_KOSTRA-DWD-2020_D00060_T100.asc").decode("latin-1").splitlines()
head = {l.split()[0].lower(): float(l.split()[1]) for l in grid[:6]}
rows = [l.split() for l in grid[6:]]
print("header", head, "rows", len(rows), "cols", len(rows[0]))
tab = zipfile.ZipFile(io.BytesIO(httpx.get(B + "tab/StatRR_KOSTRA-DWD-2020_D00060.csv.zip", timeout=60).content))
lines = tab.read(tab.namelist()[0]).decode("latin-1").splitlines()
hdr = lines[0].split(";")
i100 = hdr.index("HN_100A")
valid = [(l.split(";")[0], l.split(";")[i100]) for l in lines[1:] if not l.split(";")[1].startswith("-99")]
print("valid rows", len(valid), "first", valid[:3], "last", valid[-3:])
# ASC value at (row 215, col 39) counted from the top, and where the same value sits in the table
r, c = 215, 39
v = rows[r][c]
print("asc[215][39] =", v, " asc[299-215][39] =", rows[299 - r][c])
cands = [k for k, val in valid if val.replace(",", ".") == v.replace(",", ".")]
print("table indices with that value:", cands[:20])
for name, (rr, cc) in {"rc_top": (r, c), "rc_bottom": (299 - r, c), "cr": (c, r), "cr_bottom": (c, 299 - r)}.items():
    k = f"{rr:03d}{cc:03d}"
    print(name, k, dict(valid).get(k))
# check a whole row pattern: compare ASC top-row-numbering with table for 50 random valid cells
import random
random.seed(1)
hits = {"rc_top": 0, "rc_bottom": 0}
for k, val in random.sample(valid, 200):
    rr, cc = int(k[:3]), int(k[3:])
    a = rows[rr][cc].replace(",", ".")
    b = rows[299 - rr][cc].replace(",", ".")
    hits["rc_top"] += a == val.replace(",", ".")
    hits["rc_bottom"] += b == val.replace(",", ".")
print("agreement of 200 table cells with ASC:", hits)
