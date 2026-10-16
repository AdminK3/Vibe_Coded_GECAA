# SPDX-License-Identifier: Apache-2.0
"""Load blueprints and compile them into pgmpy Bayesian networks."""

from __future__ import annotations

import itertools
import logging
import warnings
from pathlib import Path

import yaml

from ..models import Blueprint, Provision

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from pgmpy.factors.discrete import TabularCPD
    from pgmpy.models import DiscreteBayesianNetwork

logging.getLogger("pgmpy").setLevel(logging.ERROR)


def load_blueprint(path: Path) -> Blueprint:
    return Blueprint.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_blueprints(folder: Path) -> dict[str, Blueprint]:
    out = {}
    for path in sorted(Path(folder).glob("*.yaml")):
        bp = load_blueprint(path)
        out[bp.id] = bp
    return out


def parent_combinations(bp: Blueprint, node_id: str):
    node = bp.node(node_id)
    parent_states = [bp.node(p).states for p in node.parents]
    for combo in itertools.product(*parent_states):
        yield dict(zip(node.parents, combo))


def first_matching_rule(bp: Blueprint, node_id: str, parent_states: dict[str, str]):
    for rule in bp.node(node_id).rules:
        if rule.matches(parent_states):
            return rule
    return None


def validate_blueprint(bp: Blueprint, provisions: dict[str, Provision] | None = None) -> list[str]:
    """Return a list of problems. An empty list means the blueprint is sound."""
    problems = []
    ids = [n.id for n in bp.nodes]
    if len(ids) != len(set(ids)):
        problems.append("duplicate node ids")
    known = set(ids)
    for n in bp.nodes:
        for p in n.parents:
            if p not in known:
                problems.append(f"{n.id}: unknown parent {p}")
        for rule in n.rules:
            for parent, wanted in rule.when.items():
                if parent not in n.parents:
                    problems.append(f"{n.id}: rule refers to {parent}, which is not a parent")
                    continue
                allowed = wanted if isinstance(wanted, list) else [wanted]
                bad = set(allowed) - set(bp.node(parent).states)
                if bad:
                    problems.append(f"{n.id}: rule uses unknown states {sorted(bad)} of {parent}")
    if problems:
        return problems
    # every parent combination must be covered by a rule
    for n in bp.nodes:
        if not n.parents:
            continue
        for combo in parent_combinations(bp, n.id):
            if first_matching_rule(bp, n.id, combo) is None:
                problems.append(f"{n.id}: no rule covers {combo}")
                break
    if bp.outcome not in known:
        problems.append(f"outcome node {bp.outcome} not found")
    if provisions is not None:
        for n in bp.nodes:
            refs = list(n.basis) + [b for r in n.rules for b in r.basis]
            for ref in refs:
                if ref not in provisions:
                    problems.append(f"{n.id}: basis {ref} is not in the provision catalogue")
    try:
        build_network(bp)
    except Exception as exc:  # cycles or CPD errors
        problems.append(f"network does not compile: {exc}")
    return problems


def build_network(bp: Blueprint) -> DiscreteBayesianNetwork:
    model = DiscreteBayesianNetwork()
    model.add_nodes_from([n.id for n in bp.nodes])
    model.add_edges_from([(p, n.id) for n in bp.nodes for p in n.parents])
    cpds = []
    for n in bp.nodes:
        if not n.parents:
            k = len(n.states)
            cpds.append(TabularCPD(n.id, k, [[1.0 / k] for _ in n.states], state_names={n.id: n.states}))
            continue
        columns = []
        for combo in parent_combinations(bp, n.id):
            rule = first_matching_rule(bp, n.id, combo)
            dist = rule.distribution(n.states)
            columns.append([dist[s] for s in n.states])
        values = [list(row) for row in zip(*columns)]
        state_names = {n.id: n.states, **{p: bp.node(p).states for p in n.parents}}
        cpds.append(TabularCPD(
            n.id, len(n.states), values,
            evidence=n.parents, evidence_card=[len(bp.node(p).states) for p in n.parents],
            state_names=state_names,
        ))
    model.add_cpds(*cpds)
    if not model.check_model():
        raise ValueError("model check failed")
    return model
