# SPDX-License-Identifier: Apache-2.0
"""REST API v2 against the synthetic provider (no network needed)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

from gecaa.api import app
from gecaa.geodata.base import to_wgs

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def wgs_box(x0, y0, x1, y1):
    """Box in UTM 32N metres -> GeoJSON polygon in WGS84."""
    return json.loads(json.dumps(to_wgs(box(x0, y0, x1, y1))))


@pytest.fixture(scope="module")
def free_spot():
    """A 10 x 10 m square away from every demo building."""
    buildings = json.loads((ROOT / "data/samples/buildings.geojson").read_text())
    with (ROOT / "data/samples/parcels.geojson").open() as fh:
        parcels = json.load(fh)
    from shapely.geometry import shape as utm   # demo files are already in EPSG:25832
    blds = [utm(f["geometry"]) for f in buildings["features"]]
    p = utm(parcels["features"][0]["geometry"])
    x0, y0, x1, y1 = p.bounds
    for i in range(int(x0), int(x1) - 10, 5):
        for j in range(int(y0), int(y1) - 10, 5):
            cand = box(i, j, i + 10, j + 10)
            if p.contains(cand) and all(cand.distance(b) > 5 for b in blds):
                return wgs_box(i, j, i + 10, j + 10)
    pytest.skip("no free spot in demo data")


def test_logics_and_providers():
    logics = client.get("/v2/logics").json()
    assert [l["id"] for l in logics] == ["k3_dwa_a_138_1_mre"]
    ids = [p["id"] for p in client.get("/v2/providers").json()]
    assert "synthetic" in ids


def test_buildings_and_size_limit():
    prov = next(p for p in client.get("/v2/providers").json() if p["id"] == "synthetic")
    bbox = ",".join(str(x) for x in prov["bounds"])
    fc = client.get(f"/v2/buildings?provider=synthetic&bbox={bbox}").json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) >= 1
    assert client.get("/v2/buildings?provider=synthetic&bbox=6,50,8,52").status_code == 422


def test_assess_needs_a_unit():
    r = client.post("/v2/assess?provider=synthetic", json={"units": []})
    assert r.status_code == 422


def test_assess_missing_inputs_stay_open(free_spot):
    req = {"units": [{"id": "S1", "name": "Mulde 1", "location": free_spot, "catchment": []}]}
    res = client.post("/v2/assess?provider=synthetic", json=req).json()
    unit = res["units"][0]
    # TA, KT and ALT were not given -> their checks cannot pass, nothing is guessed
    ch03 = next(c for c in unit["checks"] if c["id"] == "CH03")
    assert ch03["result"] in ("OPEN", "BLOCKED")
    assert unit["result"] in ("OPEN", "BLOCKED")
    assert any("Bautiefe" in " ".join(p["missing"]) for p in res["open_points"]) or unit["result"] == "BLOCKED"


def test_report_is_german_html(free_spot):
    req = {"site_name": "Testgrundstück", "site_inputs": {"ALT": "keine", "KT": 0},
           "units": [{"id": "S1", "name": "Mulde 1", "location": free_spot, "inputs": {"TA": 0.5}}]}
    r = client.post("/v2/report?provider=synthetic", json=req)
    assert r.status_code == 200
    html = r.text
    for heading in ("Zusammenfassung", "Technische Machbarkeit", "Rechtliche Rahmenbedingungen", "Nicht beziffert"):
        assert heading in html
    assert "Synthetische" in html or "synthetisch" in html.lower()


def test_start_page_is_v2_and_v1_kept():
    assert "/static/v2.js" in client.get("/").text and "/static/risk.js" in client.get("/").text
    assert "/static/v2.js" in client.get("/v2").text
    assert "/static/app.js" in client.get("/v1").text


# ---- regressions from the independent review

def test_bbox_must_be_ordered_and_finite():
    assert client.get("/v2/buildings?provider=synthetic&bbox=9.4,50.4,5.9,52.5").status_code == 422
    assert client.get("/v2/buildings?provider=synthetic&bbox=nan,50,7,51").status_code == 422


def test_bad_inputs_are_rejected(free_spot):
    base = {"units": [{"id": "S1", "name": "M", "location": free_spot}]}
    for bad in ({"XX": 1}, {"TA": "nan"}, {"TA": -1}, {"TA": "abc"}):
        r = client.post("/v2/assess?provider=synthetic", json={**base, "site_inputs": bad})
        assert r.status_code == 422, bad
    r = client.post("/v2/assess?provider=synthetic", json={**base, "site_inputs": {"ALT": "vielleicht"}})
    assert r.status_code == 422


def test_location_must_be_a_real_area():
    point = {"type": "Point", "coordinates": [6.94, 51.255]}
    r = client.post("/v2/assess?provider=synthetic", json={"units": [{"id": "S1", "name": "M", "location": point}]})
    assert r.status_code == 422
    huge = {"type": "Polygon", "coordinates": [[[6.9, 51.2], [7.0, 51.2], [7.0, 51.3], [6.9, 51.3], [6.9, 51.2]]]}
    r = client.post("/v2/assess?provider=synthetic", json={"units": [{"id": "S1", "name": "M", "location": huge}]})
    assert r.status_code == 422


def test_too_many_units(free_spot):
    units = [{"id": f"S{i}", "name": "M", "location": free_spot} for i in range(21)]
    assert client.post("/v2/assess?provider=synthetic", json={"units": units}).status_code == 422


def test_report_without_units_is_422():
    assert client.post("/v2/report?provider=synthetic", json={"units": []}).status_code == 422


def test_blocked_check_never_shows_high_pass_probability(free_spot):
    req = {"units": [{"id": "S1", "name": "M", "location": free_spot, "inputs": {"TA": 0.5, "GWF": 1.2}}],
           "site_inputs": {"ALT": "keine", "KT": 0}}
    unit = client.post("/v2/assess?provider=synthetic", json=req).json()["units"][0]
    ch03 = next(c for c in unit["checks"] if c["id"] == "CH03")
    assert ch03["result"] == "BLOCKED" and ch03["p_passed"] == 0.0
    assert unit["p_passed"] in (0.0, None)
