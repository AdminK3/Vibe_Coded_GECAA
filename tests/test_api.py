# SPDX-License-Identifier: Apache-2.0
import json

import pytest
from fastapi.testclient import TestClient

from gecaa.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def parcel(root):
    gj = json.loads((root / "data" / "samples" / "parcels.geojson").read_text())
    feat = gj["features"][0]
    feat["crs"] = gj["crs"]
    return feat


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_blueprints_listed(client):
    ids = [b["id"] for b in client.get("/v1/blueprints").json()]
    assert "mulden_rigolen_system_nrw" in ids


def test_evaluation(client, parcel):
    r = client.post("/v1/evaluations", json={"parcel": parcel, "context": {"connected_area_m2": 2400,
                                                                           "available_area_m2": 420,
                                                                           "contamination": "none_known"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evaluation"]["verdict"] == "feasible"
    assert body["parcel"]["synthetic_data"] is True
    assert body["evaluation"]["provisions"]["whg_55_2"]["citation"] == "§ 55 Abs. 2 WHG"


def test_report_html(client, parcel):
    r = client.post("/v1/evaluations/report", json={"parcel": parcel})
    assert r.status_code == 200
    assert "Sources" in r.text and "§ 55 Abs. 2 WHG" in r.text and "Synthetic data" in r.text


def test_demo_report(client):
    r = client.get("/v1/demo/demo-c")
    assert r.status_code == 200 and "What would settle it" in r.text


def test_bad_parcel(client):
    r = client.post("/v1/evaluations", json={"parcel": {"type": "Point"}})
    assert r.status_code == 422
