# SPDX-License-Identifier: Apache-2.0
"""Measure types, sewer design mode, basement damage, SWMM hook."""

import base64
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from gecaa.geodata.base import to_utm, to_wgs
from gecaa.geodata.synthetic import SyntheticProvider
from gecaa.risk import measures as mlib, sewer
from gecaa.risk.chain import RainRequest, run

PROV = SyntheticProvider(Path(__file__).resolve().parents[1] / "data" / "samples")


def _center():
    w, s, e, n = PROV.bounds
    return (w + e) / 2, (s + n) / 2


def _req(**kw):
    lon, lat = _center()
    return RainRequest(lon=lon, lat=lat, box_m=200, rps=[10, 100], scenarios=["heute"], prefer_worker=False, **kw)


def _poly(half=60.0):
    lon, lat = _center()
    x, y = to_utm({"type": "Point", "coordinates": [lon, lat]}).coords[0]
    return to_wgs(box(x - half, y - half, x + half, y + half))


def _runoff(res, variant="base"):
    return next(e for e in res["events"] if e["rp"] == 100)[variant]["runoff_m3"]


@pytest.fixture(scope="module")
def base():
    return run(PROV, _req())


@pytest.mark.parametrize("kind", ["mulde", "gruendach", "entsiegelung", "baeume"])
def test_each_kind_reduces_runoff(base, kind):
    roof_ids = [b["id"] for b in base["buildings"][:5]] if kind in ("mulde", "gruendach") else []
    m = {"id": "m1", "kind": kind, "geometry": _poly(), "roof_ids": roof_ids}
    if kind == "baeume":
        m.update(trees=20, pit_m3_per_tree=2.0)
    res = run(PROV, _req(measures=[m]))
    assert _runoff(res, "measure") < _runoff(res, "base")
    out = res["measures"][0]
    assert out["kind"] == kind and out["retained_m3"] > 0 and out["heat_note"]
    ids = {a["id"] for a in res["assumptions"]}
    assert mlib.ASSUMPTIONS[kind]["id"] in ids
    others = {mlib.ASSUMPTIONS[k]["id"] for k in mlib.KINDS if k != kind}
    assert not (ids & others)


def test_green_roof_coefficient():
    c = np.ones((3, 3))
    mask = np.zeros((3, 3), bool)
    mask[1, 1] = True
    mlib.apply_static("gruendach", "extensiv", c, mask)
    assert c[1, 1] == 0.5 and c[0, 0] == 1.0
    mlib.apply_static("gruendach", "intensiv", c, mask)
    assert c[1, 1] == 0.3
    assert "Normwert (angelehnt)" in mlib.ASSUMPTIONS["gruendach"]["art"]


def test_sewer_design_reduces_effective_rain(base):
    c = np.array([1.0, 0.9, 0.3])
    eff = sewer.effective_coeff(c, 50.0, 30.0)
    assert np.allclose(eff, [0.4, 0.36, 0.3])
    res = run(PROV, _req(sewer_mode="bemessung", sewer_design_rp=3))
    ev = next(e for e in res["events"] if e["rp"] == 100)
    assert ev["effective_mm"] < ev["rain_mm"]
    assert _runoff(res) < _runoff(base)
    assert res["sewer"]["mode"] == "bemessung"
    a3 = next(a for a in res["assumptions"] if a["id"] == "A3")
    assert "3-jährlichen" in a3["text"]


def test_basement_increases_damage(base):
    res = run(PROV, _req(basement_share=0.8, threshold_m=0.0))
    assert res["impact"]["heute"]["aai_agg"] > base["impact"]["heute"]["aai_agg"]
    assert any(a["id"] == "A16" for a in res["assumptions"])


def test_invalid_kind_gives_422():
    from gecaa.api import app
    lon, lat = _center()
    r = TestClient(app).post("/v3/risk/rain?provider=synthetic", json={
        "lon": lon, "lat": lat, "box_m": 200, "prefer_worker": False,
        "measures": [{"id": "x", "kind": "pool", "geometry": _poly(10)}]})
    assert r.status_code == 422


TINY_INP = """[OPTIONS]
FLOW_UNITS CMS
START_DATE 01/01/2020
START_TIME 00:00:00
END_DATE 01/01/2020
END_TIME 02:00:00
REPORT_STEP 00:05:00
WET_STEP 00:01:00
DRY_STEP 00:05:00
ROUTING_STEP 0:00:10
[RAINGAGES]
RG1 INTENSITY 0:05 1.0 TIMESERIES TS1
[SUBCATCHMENTS]
S1 RG1 J1 5 100 500 0.5 0
[SUBAREAS]
S1 0.01 0.1 0.05 0.05 0 OUTLET
[INFILTRATION]
S1 3.0 0.5 4 7 0
[JUNCTIONS]
J1 100 0.5 0 0 0
[OUTFALLS]
O1 99 FREE NO
[CONDUITS]
C1 J1 O1 50 0.013 0 0 0 0
[XSECTIONS]
C1 CIRCULAR 0.1 0 0 0 1
[TIMESERIES]
TS1 0:00 100
TS1 1:00 100
TS1 1:05 0
[COORDINATES]
J1 400000 5700000
O1 400050 5700000
"""


@pytest.mark.skipif(importlib.util.find_spec("pyswmm") is None, reason="pyswmm nicht installiert")
def test_swmm_node_flooding(tmp_path):
    p = tmp_path / "tiny.inp"
    p.write_text(TINY_INP)
    nodes = sewer.swmm_node_flooding(str(p))
    j1 = next(n for n in nodes if n["id"] == "J1")
    assert j1["x"] == 400000 and j1["flood_m3"] >= 0
    assert sewer.decode_inp(base64.b64encode(TINY_INP.encode()).decode()).endswith(".inp")
