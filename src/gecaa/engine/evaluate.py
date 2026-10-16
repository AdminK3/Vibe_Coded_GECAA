# SPDX-License-Identifier: Apache-2.0
"""Run a blueprint for one site and build the cited reasoning chain."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from typing import Any

from ..models import Blueprint, Evaluation, Finding, NextCheck, Provision
from .blueprint import build_network, first_matching_rule

# What a municipality can do to settle each uncertain input.
CHECK_ADVICE = {
    "kf": "Run an infiltration test on site (for example a double-ring or open-pit test) to measure kf.",
    "groundwater": "Check groundwater levels with the lower water authority or a monitoring well near the site.",
    "slope": "Survey the exact location of the swale; a flatter part of the parcel may be enough.",
    "contamination": "Check the contaminated sites register (Altlastenkataster) for this parcel.",
    "space": "Measure the free, unsealed area and the sealed area that will drain into the system.",
    "protection_zone": "Confirm the zone boundaries with the water authority.",
}

CLEAR, LEANING = 0.8, 0.6

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from pgmpy.factors.discrete import TabularCPD
    from pgmpy.inference import VariableElimination


def _normalise(dist: dict[str, float], states: list[str]) -> dict[str, float]:
    total = sum(max(0.0, dist.get(s, 0.0)) for s in states)
    if total <= 0:
        raise ValueError(f"evidence for states {states} sums to zero")
    return {s: max(0.0, dist.get(s, 0.0)) / total for s in states}


def evaluate(bp: Blueprint, evidence: dict[str, dict[str, Any]], provisions: dict[str, Provision],
             inputs: dict[str, Any] | None = None) -> Evaluation:
    """Evaluate a blueprint.

    evidence maps each root node id to {"distribution": {state: p}, "detail": {...}}.
    A distribution with one state at 1.0 is hard evidence; anything else is
    soft evidence that carries the uncertainty of the underlying data.
    """
    roots = [n for n in bp.nodes if not n.parents]
    missing = [n.id for n in roots if n.id not in evidence]
    if missing:
        raise ValueError(f"missing evidence for {missing}")

    model = build_network(bp)
    virtual = []
    root_dists = {}
    for n in roots:
        dist = _normalise(evidence[n.id]["distribution"], n.states)
        root_dists[n.id] = dist
        virtual.append(TabularCPD(n.id, len(n.states), [[dist[s]] for s in n.states], state_names={n.id: n.states}))

    infer = VariableElimination(model)
    posteriors: dict[str, dict[str, float]] = {}
    inner = [n.id for n in bp.nodes if n.parents]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = infer.query(inner, virtual_evidence=virtual, joint=False, show_progress=False)
    for node_id, factor in result.items():
        names = factor.state_names[node_id]
        posteriors[node_id] = {s: float(v) for s, v in zip(names, factor.values)}
    posteriors.update(root_dists)

    def most_likely(node_id: str) -> str:
        dist = posteriors[node_id]
        return max(dist, key=dist.get)

    findings: list[Finding] = []
    order = _topological(bp)
    for node_id in order:
        node = bp.node(node_id)
        top = most_likely(node_id)
        if not node.parents:
            ev = evidence[node_id]
            findings.append(Finding(
                node_id=node_id, label=node.label, distribution=posteriors[node_id],
                state_labels={s: node.label_of(s) for s in node.states}, most_likely=top, most_likely_label=node.label_of(top),
                explanation=ev.get("explanation", ""), basis=list(node.basis),
                evidence=ev.get("detail", {}), assumption=node.assumption, is_input=True,
            ))
            continue
        parent_states = {p: most_likely(p) for p in node.parents}
        rule = first_matching_rule(bp, node_id, parent_states)
        findings.append(Finding(
            node_id=node_id, label=node.label, distribution=posteriors[node_id],
            state_labels={s: node.label_of(s) for s in node.states}, most_likely=top, most_likely_label=node.label_of(top),
            explanation=rule.explanation if rule else "",
            basis=list(dict.fromkeys((rule.basis if rule else []) + node.basis)),
            evidence={"most likely inputs": {bp.node(p).label: bp.node(p).label_of(s) for p, s in parent_states.items()}},
            assumption=node.assumption,
        ))

    outcome = bp.node(bp.outcome)
    verdict = most_likely(bp.outcome)
    confidence = posteriors[bp.outcome][verdict]
    certainty = "clear" if confidence >= CLEAR else "leaning" if confidence >= LEANING else "open"
    next_checks = _sensitivity(bp, infer, roots, root_dists) if certainty != "clear" else []
    used = {b for f in findings for b in f.basis}
    return Evaluation(
        blueprint_id=bp.id, blueprint_title=bp.title, blueprint_version=bp.version,
        review_status=bp.review_status, verdict=verdict, verdict_label=outcome.label_of(verdict),
        confidence=confidence, certainty=certainty, distribution=posteriors[bp.outcome],
        findings=findings, next_checks=next_checks, inputs=inputs or {}, gaps=list(bp.gaps),
        provisions={pid: provisions[pid] for pid in sorted(used) if pid in provisions},
        created_at=datetime.now(timezone.utc),
    )


def _sensitivity(bp: Blueprint, infer, roots, root_dists, limit: int = 3) -> list[NextCheck]:
    """For each uncertain input, how much would knowing it for sure move the result?"""
    best = bp.node(bp.outcome).states[0]
    checks = []
    for n in roots:
        dist = root_dists[n.id]
        if max(dist.values()) >= 0.95:
            continue
        outcome_if = {}
        for state in n.states:
            if dist[state] < 0.01:
                continue
            virtual = [
                TabularCPD(r.id, len(r.states),
                           [[(1.0 if s == state else 0.0) if r.id == n.id else root_dists[r.id][s]] for s in r.states],
                           state_names={r.id: r.states})
                for r in roots
            ]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                f = infer.query([bp.outcome], virtual_evidence=virtual, show_progress=False)
            outcome_if[state] = {s: float(v) for s, v in zip(f.state_names[bp.outcome], f.values)}
        if len(outcome_if) < 2:
            continue
        p_best = [o[best] for o in outcome_if.values()]
        swing = max(p_best) - min(p_best)
        if swing < 0.05:
            continue
        checks.append(NextCheck(node_id=n.id, label=n.label, swing=swing,
                                outcome_if={n.label_of(s): o for s, o in outcome_if.items()},
                                advice=CHECK_ADVICE.get(n.id, "Verify this input on site.")))
    checks.sort(key=lambda c: c.swing, reverse=True)
    return checks[:limit]


def _topological(bp: Blueprint) -> list[str]:
    done: list[str] = []
    pending = [n.id for n in bp.nodes]
    while pending:
        progressed = False
        for nid in list(pending):
            if all(p in done for p in bp.node(nid).parents):
                done.append(nid)
                pending.remove(nid)
                progressed = True
        if not progressed:
            raise ValueError("blueprint has a cycle")
    return done
