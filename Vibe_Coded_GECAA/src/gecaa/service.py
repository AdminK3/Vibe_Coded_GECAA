# SPDX-License-Identifier: Apache-2.0
"""Wiring: load knowledge, blueprints and site data, then evaluate a parcel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import data_dir
from .engine import evaluate, load_blueprints, validate_blueprint
from .knowledge import SnapshotStore, load_provisions
from .models import Blueprint, Evaluation, Provision
from .spatial import Dataset, Parcel, site_evidence
from .spatial.analysis import parcel_summary


@dataclass
class Gecaa:
    blueprints: dict[str, Blueprint]
    provisions: dict[str, Provision]
    snapshots: SnapshotStore
    dataset: Dataset

    @classmethod
    def from_home(cls, home: Path | None = None, dataset: Path | None = None) -> "Gecaa":
        home = Path(home or data_dir())
        snapshots = SnapshotStore(home / "data" / "knowledge" / "snapshots")
        provisions = snapshots.apply(load_provisions(home / "data" / "knowledge" / "provisions.yaml"))
        blueprints = load_blueprints(home / "blueprints")
        for bp in blueprints.values():
            problems = validate_blueprint(bp, provisions)
            if problems:
                raise ValueError(f"blueprint {bp.id} is invalid: {problems}")
        ds_path = dataset or os.environ.get("GECAA_DATASET") or home / "data" / "samples" / "dataset.yaml"
        return cls(blueprints, provisions, snapshots, Dataset.load(Path(ds_path)))

    def evaluate_parcel(self, blueprint_id: str, parcel: Parcel, context: dict[str, Any] | None = None) -> tuple[Evaluation, dict]:
        bp = self.blueprints[blueprint_id]
        evidence = site_evidence(self.dataset, parcel, bp.parameters, context)
        summary = parcel_summary(parcel, self.dataset)
        ctx = {**parcel.properties, **(context or {})}
        inputs = {
            "Parcel": f"{summary['name']} ({summary['id']}), {summary['area_m2']} m²",
            "Connected sealed area": f"{ctx.get('connected_area_m2', 'not given')} m²",
            "Free area for the swale": f"{ctx.get('available_area_m2', 'not given')} m²",
            "Contamination status": ctx.get("contamination", "unknown"),
            "Site dataset": self.dataset.config.get("name"),
        }
        return evaluate(bp, evidence, self.provisions, inputs), summary


@lru_cache(maxsize=1)
def default_app() -> Gecaa:
    return Gecaa.from_home()
