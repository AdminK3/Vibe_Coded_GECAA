# SPDX-License-Identifier: Apache-2.0
import pytest
from fastapi.testclient import TestClient

from gecaa.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_web_app_served_without_cdn(client):
    html = client.get("/v1").text
    assert "GECAA" in html and "/static/app.js" in html
    v2 = client.get("/").text
    assert "/static/v2.js" in v2 and 'src="http' not in v2
    assert 'src="http' not in html and 'href="http' not in html   # no third-party scripts or styles
    for path in ["/static/app.js", "/static/style.css", "/static/vendor/leaflet/leaflet.js",
                 "/static/vendor/geoman/leaflet-geoman.min.js", "/static/vendor/cytoscape/cytoscape.min.js"]:
        assert client.get(path).status_code == 200, path


def test_dataset_and_layers(client):
    ds = client.get("/v1/dataset").json()
    w, s, e, n = ds["bounds"]
    assert 6.9 < w < e < 7.0 and 51.2 < s < n < 51.3 and ds["synthetic"] is True
    layers = {l["id"]: l for l in client.get("/v1/layers").json()}
    assert set(layers) == {"kf", "groundwater", "slope", "zones", "parcels"}
    for lid in ("kf", "groundwater", "slope"):
        r = client.get(layers[lid]["url"])
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    zones = client.get("/v1/layers/zones.geojson").json()
    lon, lat = zones["features"][0]["geometry"]["coordinates"][0][0]
    assert 6.9 < lon < 7.0 and 51.2 < lat < 51.3   # reprojected to WGS84


def test_drawn_parcel_outside_data_gives_clear_error(client):
    far = {"type": "Polygon", "coordinates": [[[13.40, 52.52], [13.41, 52.52], [13.41, 52.53], [13.40, 52.52]]]}
    r = client.post("/v1/evaluations", json={"parcel": far})
    assert r.status_code == 422 and "outside" in r.json()["detail"]


def test_knowledge_refresh_reports_per_provision(client, monkeypatch):
    from gecaa.knowledge import neuris

    def fail(self, prov, store, term):
        raise LookupError("NeuRIS not reachable in tests")
    monkeypatch.setattr(neuris.NeurisClient, "refresh", fail)
    results = client.post("/v1/knowledge/refresh").json()
    by_id = {r["id"]: r for r in results}
    assert by_id["whg_55_2"]["status"] == "failed"
    assert by_id["lwg_nrw_44"]["status"] == "skipped"
