# SPDX-License-Identifier: Apache-2.0
"""Offline tests for v4 modules: LoD2 heights, development plans, Zensus, heatwaves, Landsat LST."""

import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from gecaa.geodata.nrw import NRWProvider
from gecaa.risk import heatwave, lst, population

# ----------------------------------------------------------------- NRW LoD2 and plans

CITYJSON = {"type": "CityJSON", "version": "1.1", "CityObjects": {
    "B1": {"type": "Building", "name": "DENW49AL00009OGC", "children": ["P1", "P2"], "attributes": {}},
    "P1": {"type": "BuildingPart", "parents": ["B1"], "attributes": {"measuredHeight": 17.081}},
    "P2": {"type": "BuildingPart", "parents": ["B1"], "attributes": {"measuredHeight": 9.5}},
    "B2": {"type": "Building", "attributes": {"measuredHeight": 6.0}}}}


def test_lod2_parse_and_fallback(monkeypatch):
    p = NRWProvider()
    assert p.parse_lod2_cityjson(CITYJSON) == {"DENW49AL00009OGC": 17.08, "B2": 6.0}
    monkeypatch.setattr(p, "_get", lambda url, params: json.dumps(CITYJSON))
    monkeypatch.setattr(p, "buildings_utm", lambda b: [{"id": "DENW49AL00009OGC", "storeys": "4"},
                                                        {"id": "DENWX", "storeys": "3"}, {"id": "DENWY", "storeys": None}])
    h = p.building_heights((404000, 5757000, 404100, 5757100))
    assert h["DENW49AL00009OGC"]["method"].startswith("gemessen")
    assert h["DENWX"] == {"height_m": 9.0, "method": "abgeleitet (ALKIS anzahlgs x 3 m)"}
    assert "DENWY" not in h


def test_building_heights_offline(monkeypatch):
    import httpx
    p = NRWProvider()
    def boom(*a, **k):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(p, "_get", boom)
    assert p.building_heights((0, 0, 1, 1)) == {}
    assert p.development_plans((0, 0, 1, 1)) is None


def test_development_plans(monkeypatch):
    doc = {"features": [{"id": "x", "geometry": None, "properties": {
        "officialTitle": "Philippistraße / Orléans-Ring", "planTypeName.title": "Bebauungsplan",
        "processStepGeneral.title": "rechtskräftig", "validFrom": "1970-10-02", "kommune": "Münster",
        "nr": "_104__", "officialDocument": "https://example.org/104"}}]}
    p = NRWProvider()
    monkeypatch.setattr(p, "_get", lambda url, params: json.dumps(doc))
    r = p.development_plans((404000, 5757000, 404100, 5757100))
    assert r[0]["name"].startswith("Philippi") and r[0]["type"] == "Bebauungsplan" and r[0]["number"] == "104"


# ----------------------------------------------------------------- Zensus

def test_population(tmp_path):
    f = tmp_path / "z.csv"
    f.write_text("x,y,residents,age65\n100.0,200.0,12,3\n150.0,250.0,,\n900.0,900.0,5,1\n")
    r = population.population(None, (0, 0, 500, 500), path=f)
    assert r == [{"x": 100.0, "y": 200.0, "residents": 12, "age65": 3},
                 {"x": 150.0, "y": 250.0, "residents": None, "age65": None}]
    assert population.population(None, (0, 0, 1, 1), path=tmp_path / "missing.csv") is None


# ----------------------------------------------------------------- heatwaves

def _series():
    idx = pd.date_range("2018-01-01", "2019-12-31", freq="D")
    tx = np.full(len(idx), 20.0)
    tn = np.full(len(idx), 10.0)
    hw = (idx >= "2018-07-01") & (idx <= "2018-07-05")     # 5-day heatwave
    tx[hw], tn[hw] = 32.0, 23.0
    tn[(idx >= "2019-08-01") & (idx <= "2019-08-03")] = 20.0  # 3 tropical nights (TN == 20)
    return idx, tx, tn


def test_indicators_synthetic():
    pytest.importorskip("xclim")
    idx, tx, tn = _series()
    rows = heatwave.indicators(pd.DataFrame({"tasmax": tx, "tasmin": tn}, index=idx))
    assert rows[0] == {"year": 2018, "heatwaves": 1, "heatwave_max_days": 5, "tropical_nights": 5}
    assert rows[1] == {"year": 2019, "heatwaves": 0, "heatwave_max_days": 0, "tropical_nights": 3}


STATIONS = """Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland Abgabe
----------- --------- --------- ------------- --------- --------- ----------------------------------------- ---------- ------
00003 18910101 20110331            202     50.7827    6.0941 Aachen                                   Nordrhein-Westfalen                      Frei
01078 19520101 20261015             37     51.2960    6.7686 Düsseldorf                               Nordrhein-Westfalen                      Frei
"""


def _dwd_zip():
    idx, tx, tn = _series()
    tx[10] = -999
    lines = ["STATIONS_ID;MESS_DATUM;QN_3;  FX;  FM;QN_4; RSK;RSKF; SDK;SHK_TAG;  NM; VPM;  PM; TMK; UPM; TXK; TNK; TGK;eor"]
    for d, a, b in zip(idx, tx, tn):
        lines.append(f"       1078;{d:%Y%m%d};   10;-999;-999;    3;   0.0;   0;-999;   0;-999;-999;-999;-999;-999;"
                     f"{a:6.1f};{b:6.1f};-999;eor")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("produkt_klima_tag_19520101_20261015_01078.txt", "\n".join(lines).encode("latin-1"))
        z.writestr("Metadaten_Geographie_01078.txt", "x")
    return buf.getvalue()


def test_station_and_zip():
    pytest.importorskip("xclim")
    st = heatwave.parse_stations(STATIONS)
    assert [s["id"] for s in st] == [3, 1078] and st[1]["name"] == "Düsseldorf"
    near = heatwave.nearest_station(st, 6.77, 51.23)
    assert near["id"] == 1078 and near["distance_km"] < 10

    class Fake(heatwave.DWDDaily):
        def stations(self):
            return st
        def station_zip(self, sid):
            return _dwd_zip()

    r = heatwave.heatwaves(6.77, 51.23, client=Fake())
    assert r["station"]["id"] == 1078
    assert r["years"][0]["heatwaves"] is None            # 2018 has a missing day (-999)
    assert r["years"][1]["tropical_nights"] == 3


# ----------------------------------------------------------------- Landsat LST

def _tif(path, arr, transform, epsg=32632):
    import rasterio
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype=arr.dtype, crs=f"EPSG:{epsg}", transform=transform, nodata=0) as d:
        d.write(arr, 1)


def test_lst_map_mocked(tmp_path):
    from rasterio.transform import from_origin
    lon, lat = 6.77, 51.23
    t, n = lst._grid(lon, lat, 600, 32632)
    big = from_origin(t.c - 300, t.f + 300, 30, 30)
    dn = lambda c: np.uint16(round((c + 273.15 - 149.0) / 0.00341802))
    scenes = []
    for i, temp in enumerate([30.0, 34.0, 50.0]):
        st = np.full((60, 60), dn(temp), dtype="uint16")
        qa = np.zeros((60, 60), dtype="uint16")
        if i == 2:
            qa[:] = 1 << 3                                   # fully cloudy -> masked
        _tif(tmp_path / f"st{i}.tif", st, big)
        _tif(tmp_path / f"qa{i}.tif", qa + 0, big)
        scenes.append({"id": f"S{i}", "st": str(tmp_path / f"st{i}.tif"), "qa": str(tmp_path / f"qa{i}.tif"),
                       "cloud": i, "epsg": 32632})
    r = lst.lst_map(lon, lat, box_m=600, scenes=scenes)
    assert r["lst_c"].shape == (n, n) and r["scene_ids"] == ["S0", "S1"]
    assert np.allclose(r["lst_c"], 32.0, atol=0.01)
    assert (r["heat_class"] == 4).all()                     # 30..35 °C -> class 4


def test_heat_class_bins():
    assert lst.heat_class(np.array([10.0, 20.0, 24.9, 59.9, 60.0, np.nan])).tolist() == [1, 2, 2, 9, 10, 0]
    assert lst.lst_map(0, 0, scenes=[]) is None
