# SPDX-License-Identifier: Apache-2.0
"""German K3 feasibility report (sections 0-6) for a v2 site result."""

from __future__ import annotations

from datetime import datetime

from jinja2 import Environment, PackageLoader, select_autoescape

from ..k3.model import RESULT_DE, InputValue, LogicSpec, Result, SiteResult

_env = Environment(loader=PackageLoader("gecaa", "report/templates"), autoescape=select_autoescape(["html", "j2"]))


def _fmt_m2(v) -> str:
    if v is None:
        return "–"
    return f"{v:,.0f} m²".replace(",", ".")


def render_k3_report(result: SiteResult, spec: LogicSpec, provisions: dict, site_name: str | None = None,
                     synthetic: bool = False) -> str:
    from ..k3.engine import _fmt

    labels = {i.abbr: i.label for i in spec.inputs}
    used = []
    for u in result.units:
        for c in u.checks:
            for b in c.basis:
                if b not in used:
                    used.append(b)
    for b in spec.legal:
        if b not in used:
            used.append(b)
    used = [b for b in used if b in provisions]
    refs = {b: i + 1 for i, b in enumerate(used)}

    def fmtv(iv: InputValue) -> str:
        if iv.value is None:
            return "–"
        if iv.abbr in labels and spec.input_spec(iv.abbr).choices:
            return spec.input_spec(iv.abbr).choices.get(str(iv.value), str(iv.value))
        return _fmt(iv.value, iv.unit)

    counts = {k: sum(1 for u in result.units if u.result.value == k) for k in ("PASSED", "OPEN", "BLOCKED")}
    passed_catchment = sum(u.catchment_m2 or 0 for u in result.units if u.result == Result.PASSED) or None
    created = datetime.fromisoformat(result.created_at).strftime("%d.%m.%Y")
    return _env.get_template("k3_report.html.j2").render(
        r=result, site_name=site_name, synthetic=synthetic, measure=spec.measure, guideline=spec.guideline,
        unit_def=spec.evaluation_unit, labels=labels, refs=refs, sources=[provisions[b] for b in used],
        legal=[provisions[b] for b in spec.legal if b in provisions], counts=counts, passed_catchment=passed_catchment,
        created=created, RESULT_DE={k.value: v for k, v in RESULT_DE.items()}, fmt_m2=_fmt_m2, fmtv=fmtv,
    )
