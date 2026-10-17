# SPDX-License-Identifier: Apache-2.0
"""PDF report, cached rain runs (3D), GeoTIFF export and uncertainty routes."""

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from gecaa.api import app

ROOT = Path(__file__).resolve().parent.parent
CLIMADA_PY = "/opt/climada-venv/bin/python"
client = TestClient(app)

PAYLOAD = {"rps": [10, 30, 100],
           "exposures": [{"id": "a", "lon": 6.97, "lat": 51.25, "value": 500000, "cls": "res"},
                         {"id": "b", "lon": 6.971, "lat": 51.25, "value": 300000, "cls": "res"}],
           "impfs": {"res": {"depth": [0, 0.5, 1, 2, 6], "mdr": [0, .25, .4, .6, 1]}},
           "depth": [[0.1, 0.2, 0.4], [0.3, 0.5, 0.8]]}


@pytest.fixture(scope="module")
def rain_run():
    prov = next(p for p in client.get("/v2/providers").json() if p["id"] == "synthetic")
    w, s, e, n = prov["bounds"]
    r = client.post("/v3/risk/rain3d?provider=synthetic",
                    json={"lon": (w + e) / 2, "lat": (s + n) / 2, "box_m": 300, "prefer_worker": False})
    assert r.status_code == 200, r.text
    return r.json(), (w + e) / 2, (s + n) / 2


def test_result_cached(rain_run):
    res, _, _ = rain_run
    got = client.get(f"/v3/risk/{res['run_id']}/result")
    assert got.status_code == 200 and got.json()["run_id"] == res["run_id"] and got.json()["bounds"] == res["bounds"]
    assert client.get("/v3/risk/nope/result").status_code == 404


def test_pdf_report(rain_run):
    res, lon, lat = rain_run
    heat = client.get("/v3/heat", params={"lon": lon, "lat": lat, "provider": "synthetic"}).json()
    heat.setdefault("hot_days", {"years": [2020, 2021, 2022], "values": [10.0, 14.0, 20.0], "mean_last10": 14.7})
    cra = client.post("/v3/cra/suggest", json={"rain": {"rps": res["rps"], "buildings": res["buildings"]},
                                              "heat": heat}).json()["rows"]
    check = {"blueprint_title": "Mulden-Rigolen-System", "verdict_label": "eher geeignet", "confidence": 0.7,
             "findings": [{"label": "Boden", "most_likely_label": "durchlässig", "explanation": "Testwert"}]}
    r = client.post("/v3/report/pdf", json={"rain": res, "heat": heat, "cra": cra, "check": check,
                                            "site": {"name": "Demo", "lon": lon, "lat": lat}})
    assert r.status_code == 200, r.text[:500]
    assert r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    from pypdf import PdfReader
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "Starkregen" in text and "Untergrenze/Größenordnung" in text and "kein Gutachten" in text
    # empty report works too
    assert client.post("/v3/report/pdf", json={"site": {}}).content[:4] == b"%PDF"


def test_geotiff(rain_run, monkeypatch):
    from gecaa.risk import chain
    res, _, _ = rain_run
    rid = res["run_id"]
    r = client.get(f"/v3/risk/{rid}/geotiff", params={"name": "depth|heute|100|base"})
    assert r.status_code == 200 and r.content[:2] in (b"II", b"MM")   # chain now keeps raw grids
    grid = {"array": np.full((4, 5), 0.25), "transform": (2.0, 0, 356000.0, 0, -2.0, 5681000.0)}
    monkeypatch.setitem(chain._RUNS[rid], "grid|heute|100|base", grid)
    r = client.get(f"/v3/risk/{rid}/geotiff", params={"name": "depth|heute|100|base"})
    assert r.status_code == 200
    import rasterio
    from rasterio.io import MemoryFile
    with MemoryFile(r.content) as m, m.open() as ds:
        assert ds.crs.to_epsg() == 25832 and ds.read(1).shape == (4, 5)
        assert float(ds.read(1)[0, 0]) == pytest.approx(0.25)
    assert rasterio


def test_uncertainty_fallback_route():
    r = client.post("/v3/risk/uncertainty", json={**PAYLOAD, "prefer_worker": False})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["worker"] is False and a["n_runs"] == 1000
    assert a["aai_agg"]["p5"] < a["aai_agg"]["p50"] < a["aai_agg"]["p95"]
    s1 = a["sensitivity_S1"]
    assert set(s1) == {"curve_scale", "exposure_scale", "hazard_scale"} and 0.5 < sum(s1.values()) < 1.2


@pytest.mark.skipif(not Path(CLIMADA_PY).exists(), reason="CLIMADA worker environment not installed")
def test_uncertainty_worker_and_fallback_agree():
    from gecaa.risk.uncertainty import monte_carlo
    code = "import sys, json, contextlib, io, impact\np = json.load(sys.stdin)\n" \
           "with contextlib.redirect_stdout(io.StringIO()):\n    r = impact.uncertainty(p, n=256)\njson.dump(r, sys.stdout)"
    out = subprocess.run([CLIMADA_PY, "-c", code], input=json.dumps(PAYLOAD), capture_output=True, text=True,
                         cwd=ROOT / "services" / "climada_worker", timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    cl = json.loads(out.stdout)["aai_agg"]["p50"]
    fb = monte_carlo(PAYLOAD)["aai_agg"]["p50"]
    assert fb == pytest.approx(cl, rel=0.10)


def test_3d_page_vendored():
    r = client.get("/3d")
    assert r.status_code == 200 and "maplibre-gl.js" in r.text and "cdn" not in r.text.lower()
    assert client.get("/static/vendor/maplibre/maplibre-gl.js").status_code == 200
    assert client.get("/static/3d.js").status_code == 200
