# SPDX-License-Identifier: Apache-2.0
import pytest

from gecaa.engine import evaluate, validate_blueprint
from gecaa.models import Blueprint


def hard(**states):
    return {k: {"distribution": {v: 1.0}} for k, v in states.items()}


GOOD = dict(kf="suitable", groundwater="sufficient", slope="flat", protection_zone="none",
            contamination="none_known", space="sufficient")


def test_blueprint_is_valid(blueprint, gecaa_app):
    assert validate_blueprint(blueprint, gecaa_app.provisions) == []


def test_ideal_site_is_feasible(blueprint, gecaa_app):
    e = evaluate(blueprint, hard(**GOOD), gecaa_app.provisions)
    assert e.verdict == "feasible"
    assert e.confidence == pytest.approx(1.0)
    assert e.certainty == "clear"
    assert e.next_checks == []


@pytest.mark.parametrize("override,verdict", [
    ({"protection_zone": "zone_i_ii"}, "not_feasible"),
    ({"protection_zone": "zone_iii"}, "conditional"),
    ({"kf": "too_low"}, "not_feasible"),
    ({"groundwater": "insufficient"}, "not_feasible"),
    ({"contamination": "suspected"}, "not_feasible"),
    ({"slope": "moderate"}, "conditional"),
    ({"slope": "steep"}, "not_feasible"),
    ({"space": "insufficient"}, "not_feasible"),
])
def test_rules_follow_thresholds(blueprint, gecaa_app, override, verdict):
    e = evaluate(blueprint, hard(**{**GOOD, **override}), gecaa_app.provisions)
    assert e.verdict == verdict


def test_soft_evidence_propagates_exactly(blueprint, gecaa_app):
    ev = hard(**GOOD)
    ev["kf"] = {"distribution": {"too_low": 0.1, "suitable": 0.85, "too_high": 0.05}}
    ev["groundwater"] = {"distribution": {"sufficient": 0.8, "insufficient": 0.2}}
    e = evaluate(blueprint, ev, gecaa_app.provisions)
    # feasible requires suitable kf and sufficient groundwater: 0.85 * 0.8
    assert e.distribution["feasible"] == pytest.approx(0.68, abs=1e-6)


def test_uncertain_input_is_named_as_next_check(blueprint, gecaa_app):
    ev = hard(**GOOD)
    ev["groundwater"] = {"distribution": {"sufficient": 0.5, "insufficient": 0.5}}
    e = evaluate(blueprint, ev, gecaa_app.provisions)
    assert e.certainty == "open"
    assert e.next_checks[0].node_id == "groundwater"
    assert e.next_checks[0].swing == pytest.approx(1.0)


def test_every_finding_is_cited(blueprint, gecaa_app):
    e = evaluate(blueprint, hard(**GOOD), gecaa_app.provisions)
    for f in e.findings:
        if f.node_id != "feasibility":
            assert f.basis, f"{f.node_id} has no basis"
            assert all(b in e.provisions for b in f.basis)


def test_missing_rule_coverage_is_reported(blueprint):
    data = blueprint.model_dump()
    legal = next(n for n in data["nodes"] if n["id"] == "legal")
    legal["rules"] = [r for r in legal["rules"] if r["when"].get("protection_zone") != "zone_iii"]
    problems = validate_blueprint(Blueprint.model_validate(data))
    assert any("no rule covers" in p for p in problems)


def test_missing_evidence_is_an_error(blueprint, gecaa_app):
    ev = hard(**GOOD)
    del ev["kf"]
    with pytest.raises(ValueError, match="missing evidence"):
        evaluate(blueprint, ev, gecaa_app.provisions)
