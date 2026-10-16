# SPDX-License-Identifier: Apache-2.0
"""Evaluate a K3 logic for evaluation units and aggregate to a site result.

Two layers, kept apart on purpose:

1. The result (PASSED / OPEN / BLOCKED) is deterministic and follows the K3
   conventions: missing input -> OPEN, values compared with the parameters as
   coded, nothing guessed.
2. The probability detail (p_passed) shows how sure the data is. Each numeric
   input may carry an uncertainty (a soil map is not a measurement); a Monte
   Carlo propagation over all inputs gives the chance that a check, and the
   whole unit, passes. It never changes the result, it explains it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from .model import (RESULT_DE, CheckResult, InputValue, LogicSpec, Result, SiteResult,
                    UnitResult)

N_SAMPLES = 4000


def load_logic(path: Path) -> LogicSpec:
    return LogicSpec.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_logics(folder: Path) -> dict[str, LogicSpec]:
    return {spec.id: spec for spec in (load_logic(p) for p in sorted(Path(folder).glob("*.yaml")))}


def validate_logic(spec: LogicSpec, provisions: dict | None = None) -> list[str]:
    problems = []
    abbrs = {i.abbr for i in spec.inputs}
    pids = {p.id for p in spec.parameters}
    cids = {c.id for c in spec.calculations}
    refs = abbrs | pids | cids
    for c in spec.calculations:
        for side in (c.a, c.b):
            if side not in refs:
                problems.append(f"{c.id}: unknown operand {side}")
    for ch in spec.checks:
        if ch.operand not in refs:
            problems.append(f"{ch.id}: unknown operand {ch.operand}")
        for bound in (ch.low, ch.high):
            if bound and bound not in pids | cids:
                problems.append(f"{ch.id}: unknown bound {bound}")
        for i in ch.inputs:
            if i not in abbrs:
                problems.append(f"{ch.id}: unknown input {i}")
        if provisions is not None:
            for b in ch.basis:
                if b not in provisions:
                    problems.append(f"{ch.id}: basis {b} not in provision catalogue")
    ids = [c.id for c in spec.checks]
    if len(ids) != len(set(ids)):
        problems.append("duplicate check ids")
    return problems


# ---------------------------------------------------------------- deterministic layer

def _resolve(spec: LogicSpec, ref: str, values: dict[str, InputValue]):
    """Value of an input abbreviation, parameter id or calculation id. None if missing."""
    for p in spec.parameters:
        if p.id == ref:
            return p.value
    for c in spec.calculations:
        if c.id == ref:
            a, b = _resolve(spec, c.a, values), _resolve(spec, c.b, values)
            if a is None or b is None:
                return None
            return {"sub": a - b, "add": a + b, "mul": a * b, "div": a / b if b else None}[c.op]
    iv = values.get(ref)
    return None if iv is None or iv.missing else iv.value


def _fmt(v, unit: str | None = None) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, (int, float)):
        if v != 0 and (abs(v) < 1e-2 or abs(v) >= 1e5):
            mant, exp = f"{v:.1e}".split("e")
            sup = str(int(exp)).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
            mant = mant.rstrip("0").rstrip(".").replace(".", ",")
            s = f"{mant}·10{sup}"
        else:
            s = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{s} {unit}" if unit else s
    return str(v)


def _unit_of(spec: LogicSpec, ref: str) -> str | None:
    for p in spec.parameters:
        if p.id == ref:
            return p.unit
    for c in spec.calculations:
        if c.id == ref:
            return c.unit
    for i in spec.inputs:
        if i.abbr == ref:
            return i.unit
    return None


def evaluate_check(spec: LogicSpec, check, values: dict[str, InputValue]) -> CheckResult:
    used = [values[a] for a in check.inputs if a in values]
    missing = [a for a in check.inputs if a not in values or values[a].missing]
    unit = _unit_of(spec, check.operand)
    base = dict(id=check.id, question=check.question, theme=check.theme, basis=check.basis,
                section=check.section, derived=check.derived, inputs_used=used, responsible=check.responsible)
    if missing:
        labels = ", ".join(spec.input_spec(a).label for a in missing)
        return CheckResult(**base, result=Result.OPEN, missing_inputs=missing,
                           text=f"Offen: es fehlt {labels}.")
    v = _resolve(spec, check.operand, values)
    low = _resolve(spec, check.low, values) if check.low else None
    high = _resolve(spec, check.high, values) if check.high else None
    if check.rule == "between":
        ok = low <= v <= high
        thr = f"{_fmt(low, unit)} bis {_fmt(high, unit)}"
    elif check.rule == "at_least":
        ok = v >= low
        thr = f"≥ {_fmt(low, unit)}"
    elif check.rule == "at_most":
        ok = v <= high
        thr = f"≤ {_fmt(high, unit)}"
    elif check.rule == "is_true":
        ok = bool(v)
        thr = "ja"
    else:  # in_set
        key = str(v)
        if key in check.passed_values:
            ok = True
        elif key in check.blocked_values:
            ok = False
        elif key in check.open_values:
            return CheckResult(**base, result=Result.OPEN, text=check.text_open or "Offen: Einzelfallprüfung erforderlich.",
                               value=v, value_display=_choice_label(spec, check.operand, v))
        else:
            return CheckResult(**base, result=Result.OPEN, value=v,
                               text=f"Offen: der Wert '{v}' ist in der Logik nicht vorgesehen.")
        thr = ", ".join(_choice_label(spec, check.operand, x) for x in check.passed_values)
    disp = _choice_label(spec, check.operand, v) if check.rule == "in_set" else _fmt(v, unit)
    return CheckResult(**base, result=Result.PASSED if ok else Result.BLOCKED, text=check.text_passed if ok else check.text_blocked,
                       value=v, value_display=disp, threshold_display=thr)


def _choice_label(spec: LogicSpec, abbr: str, v) -> str:
    try:
        return spec.input_spec(abbr).choices.get(str(v), str(v))
    except StopIteration:
        return str(v)


def unit_result(checks: list[CheckResult]) -> Result:
    results = {c.result for c in checks}
    if Result.BLOCKED in results:
        return Result.BLOCKED
    if results == {Result.PASSED}:
        return Result.PASSED
    return Result.OPEN


def site_result(units: list[Result]) -> Result:
    if not units:
        return Result.OPEN
    if all(u == Result.PASSED for u in units):
        return Result.PASSED
    if any(u == Result.PASSED for u in units):
        return Result.PARTLY
    if all(u == Result.BLOCKED for u in units):
        return Result.BLOCKED
    return Result.OPEN


# ---------------------------------------------------------------- probability layer

def _samples(iv: InputValue, rng: np.random.Generator) -> np.ndarray | None:
    if iv.missing or not isinstance(iv.value, (int, float)) or isinstance(iv.value, bool):
        return None
    if not iv.distribution:
        return np.full(N_SAMPLES, float(iv.value))
    kind, a, b = iv.distribution
    if kind == "normal":
        return rng.normal(a, b, N_SAMPLES)
    if kind == "uniform":
        return rng.uniform(a, b, N_SAMPLES)
    if kind == "log10normal":
        return 10 ** rng.normal(a, b, N_SAMPLES)
    raise ValueError(f"unknown distribution {kind}")


def probabilities(spec: LogicSpec, values: dict[str, InputValue], results: list[CheckResult], seed: int = 7):
    """P(PASSED) per numeric check and jointly for the unit, from input uncertainty."""
    rng = np.random.default_rng(seed)
    draws = {abbr: s for abbr, iv in values.items() if (s := _samples(iv, rng)) is not None}

    def resolve(ref):
        for p in spec.parameters:
            if p.id == ref:
                return np.full(N_SAMPLES, float(p.value)) if isinstance(p.value, (int, float)) else None
        for c in spec.calculations:
            if c.id == ref:
                a, b = resolve(c.a), resolve(c.b)
                if a is None or b is None:
                    return None
                return {"sub": a - b, "add": a + b, "mul": a * b, "div": a / np.where(b == 0, np.nan, b)}[c.op]
        return draws.get(ref)

    joint = np.ones(N_SAMPLES, dtype=bool)
    joint_defined = True
    for check, res in zip(spec.checks, results):
        if res.result == Result.OPEN:
            joint_defined = False
            continue
        if check.rule in ("between", "at_least", "at_most"):
            v = resolve(check.operand)
            lo = resolve(check.low) if check.low else None
            hi = resolve(check.high) if check.high else None
            if v is None:
                continue
            ok = np.ones(N_SAMPLES, dtype=bool)
            if lo is not None:
                ok &= v >= lo
            if hi is not None:
                ok &= v <= hi
            res.p_passed = float(ok.mean())
            joint &= ok
        else:
            passed = res.result == Result.PASSED
            res.p_passed = 1.0 if passed else 0.0
            if not passed:
                joint &= False
    return float(joint.mean()) if joint_defined else None


# ---------------------------------------------------------------- public API

def evaluate_unit(spec: LogicSpec, unit_id: str, name: str, values: dict[str, InputValue],
                  geometry: dict | None = None, catchment_m2: float | None = None,
                  location_m2: float | None = None) -> UnitResult:
    checks = [evaluate_check(spec, ch, values) for ch in spec.checks]
    p = probabilities(spec, values, checks)
    return UnitResult(id=unit_id, name=name, result=unit_result(checks), checks=checks, p_passed=p,
                      geometry=geometry, catchment_m2=catchment_m2, location_m2=location_m2,
                      inputs=list(values.values()))


def open_points(spec: LogicSpec, units: list[UnitResult]) -> list[dict]:
    """OPEN checks merged across units, with what is missing and who can settle it."""
    points: dict[str, dict] = {}
    for u in units:
        for c in u.checks:
            if c.result != Result.OPEN:
                continue
            key = f"{c.id}|{c.text}"
            p = points.setdefault(key, {"check": c.id, "question": c.question, "text": c.text, "units": [],
                                        "missing": [spec.input_spec(a).label for a in c.missing_inputs],
                                        "responsible": c.responsible, "basis": c.basis})
            p["units"].append(u.name)
    return list(points.values())


def assess(spec: LogicSpec, units: list[UnitResult], site_inputs: list[InputValue] | None = None,
           data_sources: list[dict] | None = None) -> SiteResult:
    res = site_result([u.result for u in units])
    return SiteResult(logic_id=spec.id, logic_title=spec.title, logic_version=spec.version,
                      review_status=spec.review_status, result=res, result_de=RESULT_DE[res], units=units,
                      site_inputs=site_inputs or [], not_coded=spec.not_coded, open_points=open_points(spec, units),
                      data_sources=data_sources or [], created_at=datetime.now(timezone.utc).isoformat())
