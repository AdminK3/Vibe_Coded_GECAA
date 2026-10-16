# SPDX-License-Identifier: Apache-2.0
"""Tests for the v2 K3 logic: generator rule, thresholds, aggregation, evaluation units."""

from pathlib import Path

import pytest
from shapely.geometry import box

from gecaa.geodata.base import to_wgs
from gecaa.geodata.synthetic import SyntheticProvider
from gecaa.k3 import InputValue, Result, evaluate_unit, load_logic, site_result, unit_result, validate_logic
from gecaa.k3.service import AssessmentRequest, run_assessment

ROOT = Path(__file__).resolve().parent.parent
X0, Y0 = 356000.0, 5680400.0


@pytest.fixture(scope="module")
def spec():
    return load_logic(ROOT / "blueprints" / "k3" / "dwa_a_138_1_mulden_rigolen.yaml")


@pytest.fixture(scope="module")
def provider():
    return SyntheticProvider(ROOT / "data" / "samples")


def iv(abbr, value, **kw):
    return InputValue(abbr=abbr, value=value, **kw)


GOOD = dict(LUB=True, KF=3e-5, GWF=3.0, TA=1.2, ABST=10.0, KT=2.0, WSG="kein", ALT="keine")


def run(spec, **over):
    vals = {k: iv(k, v) for k, v in {**GOOD, **over}.items()}
    return evaluate_unit(spec, "u", "Standort", vals)


def by_id(unit, cid):
    return next(c for c in unit.checks if c.id == cid)


def test_logic_is_valid(spec, gecaa_app):
    assert validate_logic(spec, gecaa_app.provisions) == []


def test_all_conditions_met_passes(spec):
    u = run(spec)
    assert u.result == Result.PASSED
    assert all(c.result == Result.PASSED for c in u.checks)


@pytest.mark.parametrize("abbr", ["LUB", "KF", "GWF", "TA", "ABST", "KT", "WSG", "ALT"])
def test_missing_input_gives_open_never_a_guess(spec, abbr):
    u = run(spec, **{abbr: None})
    assert u.result == Result.OPEN
    opened = [c for c in u.checks if c.result == Result.OPEN]
    assert opened and all(abbr in c.missing_inputs for c in opened)


@pytest.mark.parametrize("kf,expected", [
    (1e-6, Result.PASSED), (1e-3, Result.PASSED),          # bounds are inclusive
    (9.9e-7, Result.BLOCKED), (1.1e-3, Result.BLOCKED),
])
def test_kf_bounds(spec, kf, expected):
    assert by_id(run(spec, KF=kf), "CH02").result == expected


@pytest.mark.parametrize("gwf,expected", [(2.2, Result.PASSED), (2.19, Result.BLOCKED), (3.0, Result.PASSED)])
def test_unsaturated_zone_at_least_one_metre(spec, gwf, expected):
    # TA = 1.2 m, so GWF - TA must be >= 1.0 m
    assert by_id(run(spec, GWF=gwf), "CH03").result == expected


def test_building_distance_scales_with_basement_depth(spec):
    assert by_id(run(spec, ABST=3.0, KT=2.0), "CH04").result == Result.PASSED     # 3.0 >= 1.5 * 2.0
    assert by_id(run(spec, ABST=2.9, KT=2.0), "CH04").result == Result.BLOCKED
    assert by_id(run(spec, ABST=0.5, KT=0.0), "CH04").result == Result.PASSED     # no basement


@pytest.mark.parametrize("zone,expected", [("kein", Result.PASSED), ("IIIA", Result.OPEN), ("III", Result.OPEN),
                                           ("II", Result.BLOCKED), ("I", Result.BLOCKED), ("X", Result.OPEN)])
def test_protection_zones(spec, zone, expected):
    assert by_id(run(spec, WSG=zone), "CH05").result == expected


@pytest.mark.parametrize("alt,expected", [("keine", Result.PASSED), ("verdacht", Result.OPEN), ("belastet", Result.BLOCKED)])
def test_contamination(spec, alt, expected):
    assert by_id(run(spec, ALT=alt), "CH06").result == expected


def test_blocked_beats_open_in_a_unit(spec):
    u = run(spec, WSG="II", ALT=None)
    assert u.result == Result.BLOCKED


@pytest.mark.parametrize("units,expected", [
    ([Result.PASSED, Result.PASSED], Result.PASSED),
    ([Result.PASSED, Result.OPEN], Result.PARTLY),
    ([Result.PASSED, Result.BLOCKED], Result.PARTLY),
    ([Result.OPEN, Result.BLOCKED], Result.OPEN),
    ([Result.BLOCKED, Result.BLOCKED], Result.BLOCKED),
    ([], Result.OPEN),
])
def test_site_aggregation_follows_k3(units, expected):
    assert site_result(units) == expected


def test_probability_detail_does_not_change_result(spec):
    vals = {k: iv(k, v) for k, v in GOOD.items()}
    vals["GWF"] = iv("GWF", 2.3, distribution=("uniform", 1.5, 4.0))
    u = evaluate_unit(spec, "u", "Standort", vals)
    assert u.result == Result.PASSED                      # value 2.3 - 1.2 = 1.1 >= 1.0
    assert 0.4 < by_id(u, "CH03").p_passed < 0.9          # but the class range makes it uncertain


def _loc(x0, y0, x1, y1):
    return to_wgs(box(X0 + x0, Y0 - y1, X0 + x1, Y0 - y0))


def test_location_on_building_is_blocked(spec, provider):
    req = AssessmentRequest(units=[{"id": "S", "name": "auf Gebäude", "location": _loc(50, 70, 60, 80)}],
                            site_inputs={"ALT": "keine", "KT": 0})
    unit = run_assessment(spec, provider, req).units[0]
    assert by_id(unit, "CH01").result == Result.BLOCKED


def test_user_measurement_overrides_map(spec, provider):
    req = AssessmentRequest(units=[{"id": "S", "name": "Mulde", "location": _loc(85, 100, 105, 125),
                                    "inputs": {"TA": 1.2, "KF": "5e-7"}}],
                            site_inputs={"ALT": "keine", "KT": 2})
    unit = run_assessment(spec, provider, req).units[0]
    kf = next(i for i in unit.inputs if i.abbr == "KF")
    assert kf.source == "Eingabe" and kf.quality.value == "hoch"
    assert by_id(unit, "CH02").result == Result.BLOCKED


def test_two_locations_give_partly(spec, provider):
    req = AssessmentRequest(units=[
        {"id": "S1", "name": "Mulde Schulhof", "location": _loc(85, 100, 105, 125), "inputs": {"TA": 1.2}},
        {"id": "S2", "name": "Mulde am Bach", "location": _loc(70, 360, 90, 385), "inputs": {"TA": 1.2}},
    ], site_inputs={"ALT": "keine", "KT": 2})
    res = run_assessment(spec, provider, req)
    assert [u.result for u in res.units] == [Result.PASSED, Result.BLOCKED]   # groundwater too shallow at the stream
    assert res.result == Result.PARTLY and res.result_de == "teilweise versickerbar"


def test_missing_planning_depth_is_an_open_point(spec, provider):
    req = AssessmentRequest(units=[{"id": "S1", "name": "Mulde", "location": _loc(85, 100, 105, 125)}],
                            site_inputs={"ALT": "keine", "KT": 2})
    res = run_assessment(spec, provider, req)
    assert res.result == Result.OPEN
    point = next(p for p in res.open_points if p["check"] == "CH03")
    assert "Bautiefe" in " ".join(point["missing"]) and point["units"] == ["Mulde"]
