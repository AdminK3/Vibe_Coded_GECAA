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
