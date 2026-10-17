# SPDX-License-Identifier: Apache-2.0
"""OGC API - Processes (pygeoapi): plugin directly and mounted under /ogc."""

import pytest

pytest.importorskip("pygeoapi")

from fastapi.testclient import TestClient  # noqa: E402

from gecaa.api import app  # noqa: E402

client = TestClient(app)


def _center():
    prov = next(p for p in client.get("/v2/providers").json() if p["id"] == "synthetic")
    w, s, e, n = prov["bounds"]
    return (w + e) / 2, (s + n) / 2


def test_processor_direct():
    from gecaa.ogc.processes import HitzeProcessor, StarkregenProcessor
    lon, lat = _center()
    mt, out = StarkregenProcessor({"name": "gecaa-starkregen"}).execute(
        {"lon": lon, "lat": lat, "box_m": 300, "provider": "synthetic", "prefer_worker": False})
    assert mt == "application/json" and out["value"]["buildings"] and out["value"]["run_id"]
    mt, out = HitzeProcessor({"name": "gecaa-hitze"}).execute({"lon": lon, "lat": lat, "provider": "synthetic"})
    assert out["value"]["lon"] == lon


def test_mounted_ogc_execute():
    r = client.get("/ogc/processes", params={"f": "json"})
    assert r.status_code == 200, r.text
    assert {"gecaa-starkregen", "gecaa-hitze"} <= {p["id"] for p in r.json()["processes"]}
    lon, lat = _center()
    r = client.post("/ogc/processes/gecaa-starkregen/execution",
                    json={"inputs": {"lon": lon, "lat": lat, "box_m": 300, "provider": "synthetic",
                                     "prefer_worker": False}})
    assert r.status_code in (200, 201), r.text[:500]
    body = r.json()
    res = body.get("value", body.get("result", body))
    if isinstance(res, dict) and "value" in res:
        res = res["value"]
    assert "buildings" in res
