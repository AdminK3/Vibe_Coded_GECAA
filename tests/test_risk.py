# SPDX-License-Identifier: Apache-2.0
"""Heavy-rain risk chain: hydrology, KOSTRA, damage, scenario shift, engine agreement, API."""

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from gecaa.geodata.base import to_wgs
from gecaa.geodata.synthetic import SyntheticProvider
from gecaa.risk import damage, kostra, scenario
from gecaa.risk.chain import RainRequest, run
from gecaa.risk.engine_client import _fallback
from gecaa.risk.hydrology import ponding, priority_flood

ROOT = Path(__file__).resolve().parents[1]
CLIMADA_PY = os.environ.get("GECAA_CLIMADA_PYTHON", "/opt/climada-venv/bin/python")


def test_priority_flood_fills_a_pit():
    dem = np.full((7, 7), 10.0)
    dem[3, 3] = 9.0
    filled = priority_flood(dem)
    assert filled[3, 3] >= 10.0 and np.all(filled >= dem)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_mass_balance(seed):
    dem = np.random.default_rng(seed).normal(0, 0.3, (80, 80)).cumsum(0) * 0.05 + np.linspace(0, 2, 80)
    r = ponding(dem, 2.0, 40, 0.6)
    stored = sum(d.stored_m3 for d in r.depressions)
    assert abs(r.runoff_m3 - stored - r.spilled_off_grid_m3) < 1e-6 * r.runoff_m3


def test_bowl_level_pool():
    y, x = np.mgrid[0:41, 0:41]
    dem = 0.05 * np.hypot(x - 20, y - 20)          # cone, closed except at the rim
    dem[0, :] = dem[-1, :] = dem[:, 0] = dem[:, -1] = 5.0
    dem[0, 20] = 0.5                                  # one low outlet on the rim
    r = ponding(dem, 1.0, 10, 1.0)
    stored = sum(d.stored_m3 for d in r.depressions)
    # rain on the 39 x 39 interior collects in the bowl; rain on the rim cells runs off the grid
    assert stored == pytest.approx(39 * 39 * 0.010, rel=0.01)
    assert r.depth[20, 20] > 0


def test_kostra_parser_and_cell():
    text = "INDEX_RC;HN_001A;HN_010A;HN_100A;RN_001A\n133101;20,1;35,6;52,3;55,8\n000000;-99,9;-99,9;-99,9;-99\n"
    t = kostra.parse_table(text)
    assert t["133101"] == {1: 20.1, 10: 35.6, 100: 52.3} and "000000" not in t
    assert kostra.cell_index(6.9745, 51.2509) == "133101"
    assert kostra.cell_index(-30, 10) is None


def test_kostra_lookup_from_zip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("StatRR_KOSTRA-DWD-2020_D00060.csv", "INDEX_RC;HN_010A;HN_030A;HN_100A\n133101;35,6;42,0;52,3\n")
    (tmp_path / "StatRR_KOSTRA-DWD-2020_D00060.csv.zip").write_bytes(buf.getvalue())
    k = kostra.Kostra(cache_dir=tmp_path)
    d = k.depths(6.9745, 51.2509, 60)
    assert d["mm"] == {10: 35.6, 30: 42.0, 100: 52.3} and d["cell"] == "133101"


def test_climaax_shift_matches_hand_values():
    rain = {10: 35.0, 30: 42.0, 100: 51.0}
    s = scenario.return_period_shift(rain, 2.0, 100)
    assert s["factor"] == pytest.approx(1.14, abs=0.01)
    assert 30 < s["rp_future"] < 100                # today's 100-year rain becomes more frequent


def test_damage_curves_and_value():
    curves = damage.load_curves()
    assert float(damage.mdr(curves, "residential", 1.0)) == pytest.approx(0.40)
    assert damage.building_value("residential", 100) == pytest.approx(100 * 720 * 1.218)
    assert damage.classify("Wohnhaus") == "residential" and damage.classify("Lagerhalle") == "industrial"
    assert list(damage.band_frequencies([10, 30, 100])) == pytest.approx([1 / 10 - 1 / 30, 1 / 30 - 1 / 100, 1 / 100])


PAYLOAD = {
    "rps": [10, 30, 100],
    "exposures": [{"id": "a", "lon": 6.97, "lat": 51.25, "value": 500000.0, "cls": "residential"},
                  {"id": "b", "lon": 6.971, "lat": 51.251, "value": 320000.0, "cls": "commercial"}],
    "depth": [[0.1, 0.2, 0.45], [0.0, 0.05, 0.3]], "measure_depth": [[0.0, 0.1, 0.3], [0.0, 0.0, 0.2]],
    "cost_eur": 25000.0, "years": 30, "discount_rate": 0.02}


def _payload():
    curves = damage.load_curves()
    p = dict(PAYLOAD)
    p["impfs"] = {c: {"depth": curves[c][0].tolist(), "mdr": curves[c][1].tolist()} for c in curves}
    blds = [damage.Building(id=e["id"], label="", cls=e["cls"], footprint_m2=1, x=0, y=0, value_eur=e["value"])
            for e in p["exposures"]]
    return p, curves, blds


@pytest.mark.skipif(not Path(CLIMADA_PY).exists(), reason="CLIMADA worker environment not installed")
def test_climada_and_fallback_agree():
    p, curves, blds = _payload()
    out = subprocess.run([CLIMADA_PY, "impact.py"], input=json.dumps(p), capture_output=True, text=True,
                         cwd=ROOT / "services" / "climada_worker", timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    cl = json.loads(out.stdout)
    fb = _fallback(p, curves, blds)
    assert cl["aai_agg"] == pytest.approx(fb["aai_agg"], rel=1e-6)
    for a, b in zip(cl["freq_curve"], fb["freq_curve"]):
        assert a["eur"] == pytest.approx(b["eur"], rel=1e-6)
    assert cl["measure"]["benefit_npv"] == pytest.approx(fb["measure"]["benefit_npv"], rel=1e-6)
    assert cl["measure"]["bcr"] == pytest.approx(fb["measure"]["bcr"], rel=1e-6)


def test_chain_on_demo_data_with_measure():
    p = SyntheticProvider(ROOT / "data" / "samples")
    w, s, e, n = p.bounds
    trough = to_wgs(box(356240, 5680290, 356262, 5680300))
    r = run(p, RainRequest(lon=(w + e) / 2, lat=(s + n) / 2, box_m=300, prefer_worker=False,
                           measures=[{"id": "M1", "geometry": trough, "roof_ids": ["B-B1"]}]))
    assert r["rain"]["source"].startswith("Demowerte")
    assert {b["id"] for b in r["buildings"]} >= {"B-A1", "B-B1"}
    ev = {(e["scenario"], e["rp"]): e for e in r["events"]}
    assert ev[("+2 K", 100)]["rain_mm"] > ev[("heute", 100)]["rain_mm"]
    assert ev[("heute", 100)]["base"]["area_over_10cm_m2"] >= ev[("heute", 10)]["base"]["area_over_10cm_m2"]
    assert ev[("heute", 100)]["measure"]["runoff_m3"] < ev[("heute", 100)]["base"]["runoff_m3"]
    assert {a["id"] for a in r["assumptions"]} >= {"A1", "A2", "A5", "A7", "A10"}
    assert "hillshade" in r["layers"] and any(l.startswith("depth|heute|100") for l in r["layers"])


def test_api_v3_rain_and_layers():
    from gecaa.api import app
    client = TestClient(app)
    prov = next(p for p in client.get("/v2/providers").json() if p["id"] == "synthetic")
    w, s, e, n = prov["bounds"]
    r = client.post("/v3/risk/rain?provider=synthetic", json={"lon": (w + e) / 2, "lat": (s + n) / 2, "box_m": 300,
                                                                "prefer_worker": False})
    assert r.status_code == 200, r.text
    res = r.json()
    png = client.get(f"/v3/risk/{res['run_id']}/layer", params={"name": "depth|heute|100|base"})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(f"/v3/risk/{res['run_id']}/layer", params={"name": "nope"}).status_code == 404
    bad = client.post("/v3/risk/rain?provider=synthetic", json={"lon": 6.94, "lat": 51.25, "rps": [7]})
    assert bad.status_code == 422


def test_heat_asc_reader_and_classes():
    from gecaa.risk import heat
    asc = "NCOLS 3\nNROWS 2\nXLLCORNER 1000\nYLLCORNER 2000\nCELLSIZE 1000\nNODATA_VALUE -999\n1 2 3\n4 -999 6\n"
    assert heat.read_asc_value(asc, 1500, 3500) == 1.0          # top row
    assert heat.read_asc_value(asc, 2500, 2500) is None         # nodata
    assert heat.read_asc_value(asc, 3500, 2500) == 6.0
    assert heat.pet_class(41.3) == "extreme Wärmebelastung" and heat.pet_class(30) == "mäßige Wärmebelastung"


def test_cra_suggestions():
    from gecaa.risk import cra
    rain = {"rps": [10, 100], "threshold_m": 0.1,
            "buildings": [{"depth": {"100": 0.45}}, {"depth": {"100": 0.05}}],
            "buildings_future": [{"depth": {"100": 0.65}}]}
    heat = {"climate_analysis": {"pet_typisch": 41.3, "pet_typisch_klasse": "extreme Wärmebelastung"},
            "hot_days": {"trend_per_decade": 2.1, "mean_last10": 14.2}}
    rows = {r["hazard"]: r for r in cra.suggest(rain, heat)}
    assert rows["Starkregen"]["severity_now"] == 3 and rows["Starkregen"]["severity_future"] == 4
    assert rows["Hitze"]["severity_now"] == 4
    assert all(1 <= r[k] <= 4 for r in rows.values() for k in ("urgency", "priority"))
