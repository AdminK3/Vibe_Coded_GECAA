# SPDX-License-Identifier: GPL-3.0-or-later
# GECAA CLIMADA worker. Copyright 2026 Khoch3 KlimaKarten UG. GNU GPL v3 or later (see LICENSE).
"""HTTP service around CLIMADA: GECAA (Apache-2.0) sends JSON, gets JSON back. No code is shared."""

from __future__ import annotations

from importlib.metadata import version

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import impact

app = FastAPI(title="GECAA CLIMADA worker", version="0.3.0",
              description="CLIMADA (GPL-3.0) impact and cost-benefit for GECAA. Source: services/climada_worker.")


class ImpactRequest(BaseModel):
    rps: list[float] = Field(min_length=1, max_length=10)
    exposures: list[dict] = Field(max_length=20000)
    impfs: dict[str, dict]
    depth: list[list[float]]
    measure_depth: list[list[float]] | None = None
    cost_eur: float | None = None
    years: int = Field(30, ge=1, le=100)
    discount_rate: float = Field(0.02, ge=0, le=0.2)


@app.get("/health")
def health():
    return {"status": "ok", "engine": "CLIMADA", "climada": version("climada"), "licence": "GPL-3.0"}


@app.post("/impact")
def run_impact(req: ImpactRequest):
    if len(req.depth) != len(req.exposures):
        raise HTTPException(422, "depth needs one row per exposure")
    try:
        return impact.compute(req.model_dump())
    except Exception as exc:  # report CLIMADA errors as 500 with a message
        raise HTTPException(500, f"CLIMADA: {type(exc).__name__}: {exc}") from exc
