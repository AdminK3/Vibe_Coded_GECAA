# SPDX-License-Identifier: Apache-2.0
"""Render an Evaluation as a self-contained HTML report with numbered citations."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from ..models import Evaluation

_env = Environment(loader=PackageLoader("gecaa", "report/templates"), autoescape=select_autoescape(["html", "j2"]))

TONE = {"feasible": "good", "conditional": "warn", "not_feasible": "bad"}
CERTAINTY_TEXT = {
    "clear": "The evidence points clearly in one direction.",
    "leaning": "The evidence leans one way, but some inputs are uncertain.",
    "open": "The result is open: the available data does not decide it. See what would settle it below.",
}


def render_html(evaluation: Evaluation, parcel: dict[str, Any] | None = None, synthetic: bool = False) -> str:
    order: list[str] = []
    for f in evaluation.findings:
        for b in f.basis:
            if b not in order and b in evaluation.provisions:
                order.append(b)
    refs = {pid: i + 1 for i, pid in enumerate(order)}
    findings = evaluation.findings
    outcome = next(f for f in reversed(findings) if not f.is_input and set(f.distribution) == set(evaluation.distribution))
    best_state = next(iter(evaluation.distribution))
    template = _env.get_template("report.html.j2")
    return template.render(
        e=evaluation, parcel=parcel or {}, synthetic=synthetic, refs=refs,
        sources=[evaluation.provisions[p] for p in order],
        inputs=[f for f in findings if f.is_input],
        derived=[f for f in findings if not f.is_input],
        outcome=outcome, best_state=best_state,
        tone=TONE.get(evaluation.verdict, "warn"), certainty_text=CERTAINTY_TEXT[evaluation.certainty],
    )
