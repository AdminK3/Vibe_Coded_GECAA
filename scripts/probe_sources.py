# SPDX-License-Identifier: Apache-2.0
"""Probe the open data sources GECAA v3 uses and write a short report (run where the internet is reachable).

    python scripts/probe_sources.py > src/gecaa/web/probe.txt
"""

import gzip
import io
import zipfile

import httpx

UA = {"User-Agent": "GECAA/0.3 probe"}
KOSTRA = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/return_periods/precipitation/KOSTRA/KOSTRA_DWD_2020/"
HOT = "https://opendata.dwd.de/climate_environment/CDC/grids_germany/annual/hot_days/"


def get(url, **kw):
    r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True, **kw)
    print(f"# GET {url} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)} bytes")
    return r


def section(t):
    print(f"\n========== {t}")


section("KOSTRA tab D00060")
r = get(KOSTRA + "tab/StatRR_KOSTRA-DWD-2020_D00060.csv.zip")
z = zipfile.ZipFile(io.BytesIO(r.content))
for n in z.namelist():
    txt = z.read(n).decode("latin-1")
    print(n, "lines", txt.count("\n"))
    print("\n".join(txt.splitlines()[:4]))

section("KOSTRA asc D00060")
r = get(KOSTRA + "asc/StatRR_KOSTRA-DWD-2020_D00060_ASC.zip")
z = zipfile.ZipFile(io.BytesIO(r.content))
print(z.namelist()[:30])
for n in z.namelist():
    if n.lower().endswith((".asc", ".prj", ".txt")):
        print("--", n)
        print("\n".join(z.read(n).decode("latin-1").splitlines()[:8])[:700])
        if n.lower().endswith(".asc"):
            break
for n in z.namelist():
    if n.lower().endswith(".prj"):
        print("-- prj", n, z.read(n).decode("latin-1")[:400]); break

section("hot days 2024")
r = get(HOT + "grids_germany_annual_hot_days_2024_17.asc.gz")
txt = gzip.decompress(r.content).decode("latin-1")
print("\n".join(txt.splitlines()[:7])[:600])
r = get(HOT)
print([l for l in r.text.splitlines() if "DESCRIPTION" in l or ".prj" in l][:5])

section("DGM1 WCS")
r = get("https://www.wcs.nrw.de/geobasis/wcs_nw_dgm", params={
    "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage", "COVERAGEID": "nw_dgm", "FORMAT": "image/tiff",
    "SUBSET": ["x(358600,358700)", "y(5679600,5679700)"]})
print(r.content[:8])
try:
    import rasterio
    from rasterio.io import MemoryFile
    with MemoryFile(r.content) as mf, mf.open() as ds:
        a = ds.read(1)
        print("tiff", ds.crs, ds.res, ds.shape, ds.bounds, ds.nodata, float(a.min()), float(a.max()))
except Exception as exc:
    print("not a tiff:", exc, r.text[:300])

section("Starkregen WMS layers")
r = get("https://sgx.geodatenzentrum.de/wms_starkregen", params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"})
import re
print(sorted(set(re.findall(r"<Name>([^<]+)</Name>", r.text)))[:80])

section("Klimaanalyse WMS GetFeatureInfo layer 54, 38, 18 (Mettmann)")
for layer in ("54", "38", "18", "29", "52"):
    r = get("https://www.wms.nrw.de/umwelt/klimaanpassung_klimaanalyse", params={
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
        "STYLES": "", "CRS": "EPSG:25832", "BBOX": "358635,5679665,358655,5679685", "WIDTH": "3", "HEIGHT": "3",
        "I": "1", "J": "1", "INFO_FORMAT": "text/xml"})
    print(layer, r.text[:700])

section("ALKIS Nutzung sample")
r = get("https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht", params={
    "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "ave:Nutzung", "COUNT": "3",
    "BBOX": "358545,5679575,358745,5679775,urn:ogc:def:crs:EPSG::25832"})
print(re.sub(r"<gml:posList[^>]*>[^<]*</gml:posList>", "<posList/>", r.text)[:2500])
