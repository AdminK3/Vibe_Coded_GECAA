# SPDX-License-Identifier: GPL-3.0-or-later
import impact


def test_hand_calculation():
    p = {"rps": [100, 10, 30],
         "exposures": [{"id": "a", "lon": 6.97, "lat": 51.25, "value": 500000, "cls": "res"}],
         "impfs": {"res": {"depth": [0, 0.5, 1, 2, 6], "mdr": [0, .25, .4, .6, 1]}},
         "depth": [[0.4, 0.1, 0.2]]}
    r = impact.compute(p)
    # RP10: 0.1 m -> 5 % ; RP30: 0.2 m -> 10 % ; RP100: 0.4 m -> 20 % of 500 000
    freq = [1 / 10 - 1 / 30, 1 / 30 - 1 / 100, 1 / 100]
    expected = 500000 * (0.05 * freq[0] + 0.10 * freq[1] + 0.20 * freq[2])
    assert abs(r["aai_agg"] - expected) < 1e-6


def test_zero_benefit_is_json_safe():
    import json
    p = {"rps": [10], "exposures": [{"id": "a", "lon": 6.9, "lat": 51.2, "value": 1.0, "cls": "x"}],
         "impfs": {"x": {"depth": [0, 1], "mdr": [0, 1]}}, "depth": [[0.0]], "measure_depth": [[0.0]], "cost_eur": 1000}
    r = impact.compute(p)
    json.dumps(r, allow_nan=False)
    assert r["measure"]["benefit_npv"] == 0 and r["measure"]["climada_cost_benefit_ratio"] is None


def test_uncertainty_unsequa():
    p = {"rps": [100, 10, 30],
         "exposures": [{"id": "a", "lon": 6.97, "lat": 51.25, "value": 500000, "cls": "res"},
                       {"id": "b", "lon": 6.971, "lat": 51.25, "value": 300000, "cls": "res"}],
         "impfs": {"res": {"depth": [0, 0.5, 1, 2, 6], "mdr": [0, .25, .4, .6, 1]}},
         "depth": [[0.4, 0.1, 0.2], [0.8, 0.3, 0.5]]}
    r = impact.uncertainty(p, n=64)
    a = r["aai_agg"]
    assert r["n_runs"] == 64 * 5 and a["p5"] < a["p50"] < a["p95"]
    assert set(r["sensitivity_S1"]) == {"curve_scale", "exposure_scale", "hazard_scale"}
    base = impact.compute(p)["aai_agg"]
    assert abs(a["p50"] - base) / base < 0.15


def test_uncertainty_route():
    from fastapi.testclient import TestClient
    import app
    p = {"rps": [10, 100], "exposures": [{"id": "a", "lon": 6.97, "lat": 51.25, "value": 1e5, "cls": "x"}],
         "impfs": {"x": {"depth": [0, 1], "mdr": [0, 1]}}, "depth": [[0.2, 0.5]], "n": 16}
    r = TestClient(app.app).post("/uncertainty", json=p)
    assert r.status_code == 200, r.text
    assert r.json()["aai_agg"]["p50"] > 0
