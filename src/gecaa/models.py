# SPDX-License-Identifier: Apache-2.0
"""Pydantic models mirroring the Regulatory Knowledge Schema (schema/gecaa_rks.yaml)."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ProvisionKind(str, Enum):
    federal_law = "federal_law"
    state_law = "state_law"
    ordinance = "ordinance"
    technical_standard = "technical_standard"
    guidance = "guidance"


class ReviewStatus(str, Enum):
    draft = "draft"
    expert_reviewed = "expert_reviewed"
    validated = "validated"


class Provenance(BaseModel):
    retrieved_from: str | None = None
    retrieved_at: datetime | None = None
    content_sha256: str | None = None
    retrieval_method: str | None = None
    text_span: str | None = None


class Provision(BaseModel):
    id: str
    kind: ProvisionKind
    jurisdiction: str
    citation: str
    title: str
    eli: str | None = None
    source_uri: str | None = None
    summary: str
    text: str | None = None
    licence_note: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    review_status: ReviewStatus = ReviewStatus.draft
    valid_from: date | None = None


class Rule(BaseModel):
    when: dict[str, str | list[str]] = Field(default_factory=dict)
    then: str | dict[str, float]
    explanation: str
    basis: list[str] = Field(default_factory=list)

    def matches(self, parent_states: dict[str, str]) -> bool:
        for parent, wanted in self.when.items():
            allowed = wanted if isinstance(wanted, list) else [wanted]
            if parent_states.get(parent) not in allowed:
                return False
        return True

    def distribution(self, states: list[str]) -> dict[str, float]:
        if isinstance(self.then, str):
            return {s: 1.0 if s == self.then else 0.0 for s in states}
        total = sum(self.then.values())
        return {s: self.then.get(s, 0.0) / total for s in states}


class Node(BaseModel):
    id: str
    label: str
    states: list[str]
    parents: list[str] = Field(default_factory=list)
    evidence_source: str | None = None
    rules: list[Rule] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)
    assumption: str | None = None
    state_labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Node":
        if self.parents and not self.rules:
            raise ValueError(f"node {self.id} has parents but no rules")
        if not self.parents and not self.evidence_source:
            raise ValueError(f"root node {self.id} needs an evidence_source")
        for rule in self.rules:
            if isinstance(rule.then, str) and rule.then not in self.states:
                raise ValueError(f"node {self.id}: rule result {rule.then!r} is not a state")
            if isinstance(rule.then, dict):
                unknown = set(rule.then) - set(self.states)
                if unknown:
                    raise ValueError(f"node {self.id}: unknown states {unknown}")
        return self

    def label_of(self, state: str) -> str:
        return self.state_labels.get(state, state.replace("_", " "))


class Blueprint(BaseModel):
    id: str
    title: str
    measure: str
    jurisdiction: str
    version: str
    review_status: ReviewStatus = ReviewStatus.draft
    outcome: str
    nodes: list[Node]
    gaps: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)


class Finding(BaseModel):
    node_id: str
    label: str
    distribution: dict[str, float]
    state_labels: dict[str, str] = Field(default_factory=dict)
    most_likely: str
    most_likely_label: str
    explanation: str
    basis: list[str]
    evidence: dict[str, Any] = Field(default_factory=dict)
    assumption: str | None = None
    is_input: bool = False


class NextCheck(BaseModel):
    """An uncertain input whose clarification would change the result most."""
    node_id: str
    label: str
    swing: float                      # range of P(best outcome) across the input's states
    outcome_if: dict[str, dict[str, float]]   # input state -> outcome distribution
    advice: str


class Evaluation(BaseModel):
    blueprint_id: str
    blueprint_title: str
    blueprint_version: str
    review_status: ReviewStatus
    verdict: str
    verdict_label: str
    confidence: float
    certainty: str                    # "clear", "leaning" or "open"
    distribution: dict[str, float]
    findings: list[Finding]
    next_checks: list[NextCheck] = Field(default_factory=list)
    inputs: dict[str, Any]
    gaps: list[str]
    provisions: dict[str, Provision]
    created_at: datetime
