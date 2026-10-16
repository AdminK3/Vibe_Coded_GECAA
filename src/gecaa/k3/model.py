# SPDX-License-Identifier: Apache-2.0
"""K3 logic model (v2).

Follows the K3 logic conventions:

* every check returns PASSED, OPEN or BLOCKED;
* a check with a missing input is OPEN (generator rule), never filled with a guess;
* thresholds are named parameters (PAxx), derived values are calculations (CAxx),
  checks are CHxx, each with a German question and its source;
* the evaluation unit for DWA-A 138-1 seepage is the system location on unbuilt
  land together with its catchment; roofs are catchment only;
* unit results aggregate to a site result PASSED / PARTLY / OPEN / BLOCKED.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Result(str, Enum):
    PASSED = "PASSED"
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"
    PARTLY = "PARTLY"   # site level only


# Ampel wording used in K3 reports.
RESULT_DE = {
    Result.PASSED: "möglich",
    Result.OPEN: "potenziell möglich (offen)",
    Result.BLOCKED: "nicht möglich",
    Result.PARTLY: "teilweise versickerbar",
}


class Quality(str, Enum):
    """Datengüte of an input, following the K3 report convention."""
    hoch = "hoch"                      # survey, report, measurement, official dataset at site scale
    annahme_belegt = "Annahme belegt"  # statement of an authority with name and date
    mittel = "mittel"                  # general value, small-scale map (e.g. BK50)
    annahme_offen = "Annahme offen"    # user statement without proof


class InputValue(BaseModel):
    """One input as it enters the logic. `value` is None when missing."""
    abbr: str
    value: Any = None
    unit: str | None = None
    # Uncertainty for numeric inputs, used only for the probability detail:
    # ("normal", mean, sd) or ("uniform", low, high) or ("log10normal", mean_log10, sd_log10)
    distribution: tuple[str, float, float] | None = None
    source: str | None = None          # layer and date, or "Eingabe"
    level: Literal["unit", "parcel", "building", "municipality"] = "unit"
    quality: Quality = Quality.annahme_offen
    note: str | None = None

    @property
    def missing(self) -> bool:
        return self.value is None or self.value == ""


class Parameter(BaseModel):
    id: str                 # PA01
    abbr: str
    value: Any
    unit: str | None = None
    label: str
    basis: list[str] = Field(default_factory=list)
    derived: bool = False   # "abgeleitet": not stated as such in the source


class Operand(BaseModel):
    """Reference to an input abbreviation, a parameter id, a calculation id, or a constant."""
    ref: str | None = None
    const: float | None = None


class Calculation(BaseModel):
    id: str                 # CA01
    label: str
    op: Literal["sub", "add", "mul", "div"]
    a: str
    b: str
    unit: str | None = None
    derived: bool = False


class Check(BaseModel):
    id: str                 # CH01
    question: str           # German question
    text_passed: str        # German text when PASSED
    text_blocked: str       # German text when BLOCKED
    text_open: str | None = None   # German text when OPEN for a reason other than a missing input
    note_en: str | None = None
    rule: Literal["between", "at_least", "at_most", "is_true", "in_set"]
    operand: str            # input abbreviation or calculation id
    low: str | None = None  # parameter ids for between / at_least / at_most
    high: str | None = None
    passed_values: list[str] = Field(default_factory=list)
    open_values: list[str] = Field(default_factory=list)
    blocked_values: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)   # all inputs the check needs (for OPEN on missing)
    theme: str
    basis: list[str] = Field(default_factory=list)
    section: str | None = None
    derived: bool = False
    responsible: str | None = None   # office that can settle an OPEN result

    @model_validator(mode="after")
    def _check(self) -> "Check":
        if self.rule == "between" and not (self.low and self.high):
            raise ValueError(f"{self.id}: 'between' needs low and high")
        if self.rule == "at_least" and not self.low:
            raise ValueError(f"{self.id}: 'at_least' needs low")
        if self.rule == "at_most" and not self.high:
            raise ValueError(f"{self.id}: 'at_most' needs high")
        if self.rule == "in_set" and not (self.passed_values or self.open_values or self.blocked_values):
            raise ValueError(f"{self.id}: 'in_set' needs value lists")
        return self


class InputSpec(BaseModel):
    abbr: str
    label: str
    unit: str | None = None
    level: Literal["unit", "parcel", "building", "municipality"] = "unit"
    kind: Literal["number", "choice", "bool", "geometry"] = "number"
    choices: dict[str, str] = Field(default_factory=dict)   # value -> German label
    sources: list[str] = Field(default_factory=list)        # where GECAA can get it (geodata layer ids)
    help: str | None = None


class NotCoded(BaseModel):
    topic: str
    reason: str
    basis: list[str] = Field(default_factory=list)


class LogicSpec(BaseModel):
    """A K3 logic for one guideline and measure (the code equivalent of a logic workbook)."""
    id: str
    title: str
    measure: str
    guideline: str
    evaluation_unit: str
    version: str
    review_status: Literal["draft", "audited", "done"] = "draft"
    inputs: list[InputSpec]
    parameters: list[Parameter]
    calculations: list[Calculation] = Field(default_factory=list)
    checks: list[Check]
    not_coded: list[NotCoded] = Field(default_factory=list)
    legal: list[str] = Field(default_factory=list)

    def param(self, pid: str) -> Parameter:
        return next(p for p in self.parameters if p.id == pid)

    def input_spec(self, abbr: str) -> InputSpec:
        return next(i for i in self.inputs if i.abbr == abbr)


class CheckResult(BaseModel):
    id: str
    question: str
    theme: str
    result: Result
    text: str
    value: Any = None
    value_display: str | None = None
    threshold_display: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    inputs_used: list[InputValue] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)
    section: str | None = None
    derived: bool = False
    p_passed: float | None = None     # probability detail from data uncertainty (None if not applicable)
    responsible: str | None = None


class UnitResult(BaseModel):
    id: str
    name: str
    result: Result
    checks: list[CheckResult]
    p_passed: float | None = None
    catchment_m2: float | None = None
    location_m2: float | None = None
    geometry: dict | None = None
    inputs: list[InputValue] = Field(default_factory=list)


class SiteResult(BaseModel):
    logic_id: str
    logic_title: str
    logic_version: str
    review_status: str
    result: Result
    result_de: str
    units: list[UnitResult]
    site_inputs: list[InputValue] = Field(default_factory=list)
    not_coded: list[NotCoded] = Field(default_factory=list)
    open_points: list[dict] = Field(default_factory=list)
    data_sources: list[dict] = Field(default_factory=list)
    created_at: str
