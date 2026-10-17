# SPDX-License-Identifier: Apache-2.0
"""v6: digital twin scene (terrain, LoD2 faces, timeline) and open street-level imagery."""

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from gecaa.api import app

client = TestClient(app)

# live structure (Mettmann, 2026-10-17), shortened: CRS84h, transform, Solid with semantics, BuildingPart -> parent
CITYJSON = {
    "type": "CityJSON", "version": "1.1",
    "transform": {"scale": [1e-07, 1e-07, 0.001], "translate": [7.0, 51.0, 100.0]},
    "vertices": [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0], [0, 0, 8000], [1000, 0, 8000], [1000, 1000, 8000], [0, 1000, 8000]],
    "CityObjects": {
        "B1": {"type": "Building", "attributes": {"externalReferences": [{"name": "DENW00AL0000abcdXX"}]}, "children": ["P1"]},
        "P1": {"type": "BuildingPart", "parents": ["B1"], "geometry": [{"type": "Solid", "lod": "2", "boundaries": [[
            [[0, 1, 2, 3]], [[4, 5, 6, 7]], [[0, 1, 5, 4]], [[1, 2, 6, 5]]]],
            "semantics": {"surfaces": [{"type": "GroundSurface"}, {"type": "RoofSurface"}, {"type": "WallSurface"}],
                          "values": [[0, 1, 2, 2]]}}]},
    },
}


def test_parse_lod2_surfaces():
    from gecaa.geodata.nrw import NRWProvider
    out = NRWProvider.parse_lod2_surfaces(CITYJSON)
    faces = out["DENW00AL0000abcdXX"]
    assert sorted(f["t"] for f in faces) == ["roof", "wall", "wall"]          # ground dropped
    roof = next(f for f in faces if f["t"] == "roof")
    assert roof["ring"][0] == [7.0, 51.0, 108.0] and roof["ring"][1][0] == pytest.approx(7.0001)


def test_parse_panoramax_facing_and_distance():
    from gecaa.api_v6 import parse_panoramax
    doc = {"features": [
        {"id": "a", "geometry": {"coordinates": [7.0, 50.9996]}, "properties": {"view:azimuth": 0, "datetime": "2025-01-02T00:00:00Z", "license": "CC-BY-SA-4.0"},
         "assets": {"thumb": {"href": "https://x/t.jpg"}, "sd": {"href": "https://x/s.jpg"}}},
        {"id": "b", "geometry": {"coordinates": [7.0, 50.9996]}, "properties": {"view:azimuth": 180}, "assets": {}}]}
    a, b = parse_panoramax(doc, 7.0, 51.0)
    assert a["facing"] is True and b["facing"] is False and 40 <= a["distance_m"] <= 50 and a["date"] == "2025-01-02"


@pytest.fixture(scope="module")
def run():
    prov = next(p for p in client.get("/v2/providers").json() if p["id"] == "synthetic")
    w, s, e, n = prov["bounds"]
    lon, lat = (w + e) / 2, (s + n) / 2
    d = 0.0004
    poly = {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]}
    ms = [{"id": "M1", "name": "Mulde", "kind": "mulde", "geometry": poly}]
    r = client.post("/v3/risk/rain3d?provider=synthetic", json={"lon": lon, "lat": lat, "box_m": 300, "prefer_worker": False, "measures": ms})
    assert r.status_code == 200, r.text
    return r.json(), ms


def test_twin_scene(run, tmp_path, monkeypatch):
    res, ms = run
    sc = client.get(f"/v6/twin/{res['run_id']}/scene")
    assert sc.status_code == 200, sc.text
    j = sc.json()
    t = j["terrain"]
    h = np.frombuffer(base64.b64decode(t["heights"]), dtype="float32")
    assert h.size == t["w"] * t["h"] and t["w"] <= 256 and np.isfinite(h).all()
    assert t["rect"][0] < res["center"][0] < t["rect"][2]
    assert j["buildings"] and all(b.get("surfaces") or b.get("footprint") for b in j["buildings"])
    assert j["has_measure"] is True and j["timeline"] is None
    monkeypatch.setenv("GECAA_PROJECTS_DB", str(tmp_path / "p.sqlite"))
    p = client.post("/v5/projects", json={"name": "Zwilling", "run_id": res["run_id"], "measures": ms}).json()
    j = client.get(f"/v6/twin/{res['run_id']}/scene", params={"project": p["id"]}).json()
    assert j["measures"][0]["kind"] == "mulde" and any(ph["key"] == "umsetzung" for ph in j["timeline"]["phases"])
    assert client.get("/v6/twin/nope/scene").status_code == 404


def test_street_endpoint(monkeypatch):
    import httpx

    from gecaa import api_v6

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"features": [{"id": "x", "geometry": {"coordinates": [7.0001, 51.0]}, "properties": {"view:azimuth": 270},
                                  "assets": {"thumb": {"href": "https://x/t.jpg"}}}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    api_v6._CACHE.clear()
    r = client.get("/v6/street", params={"lon": 7.0, "lat": 51.0, "radius": 50}).json()
    assert r["images"][0]["id"] == "x" and r["images"][0]["facing"] is True and "panoramax" in r["images"][0]["viewer"]
    assert client.get("/v6/street", params={"lon": 0, "lat": 51}).status_code == 422


def test_twin_page_vendored():
    r = client.get("/zwilling")
    assert r.status_code == 200 and "/static/vendor/cesium/Cesium.js" in r.text and "cdn" not in r.text.lower()
    assert client.get("/static/vendor/cesium/Cesium.js").status_code == 200
    assert client.get("/static/twin.js").status_code == 200
