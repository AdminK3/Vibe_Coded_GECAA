# SPDX-License-Identifier: Apache-2.0
"""REST API.

Run locally:  uvicorn gecaa.api:app --reload
Docs:         http://localhost:8000/docs
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .models import Evaluation
from .report import render_html
from .service import default_app
from .spatial import load_parcels

app = FastAPI(
    title="GECAA API",
    version=__version__,
    description="Cited, auditable evaluation of climate adaptation measures for a specific site. "
                "Results are decision support; the responsible authority decides.",
    license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
)


class SiteContext(BaseModel):
    connected_area_m2: float | None = Field(None, description="Sealed area that drains into the system, m²")
    available_area_m2: float | None = Field(None, description="Free, unsealed area available for the swale, m²")
    contamination: Literal["none_known", "suspected", "unknown"] = "unknown"


class EvaluationRequest(BaseModel):
    blueprint_id: str = "mulden_rigolen_system_nrw"
    parcel: dict[str, Any] = Field(..., description="GeoJSON Feature or geometry. WGS84 unless a 'crs' member says otherwise.")
    context: SiteContext = Field(default_factory=SiteContext)


class EvaluationResponse(BaseModel):
    parcel: dict[str, Any]
    evaluation: Evaluation


def _run(req: EvaluationRequest):
    gecaa = default_app()
    if req.blueprint_id not in gecaa.blueprints:
        raise HTTPException(404, f"unknown blueprint {req.blueprint_id}")
    try:
        parcels = load_parcels(req.parcel)
    except Exception as exc:
        raise HTTPException(422, f"could not read parcel GeoJSON: {exc}") from exc
    if len(parcels) != 1:
        raise HTTPException(422, "send exactly one parcel")
    context = {k: v for k, v in req.context.model_dump().items() if v is not None}
    try:
        return gecaa.evaluate_parcel(req.blueprint_id, parcels[0], context)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/v1/blueprints")
def list_blueprints():
    return [{"id": b.id, "title": b.title, "measure": b.measure, "jurisdiction": b.jurisdiction,
             "version": b.version, "review_status": b.review_status, "gaps": b.gaps}
            for b in default_app().blueprints.values()]


@app.get("/v1/blueprints/{blueprint_id}")
def get_blueprint(blueprint_id: str):
    bp = default_app().blueprints.get(blueprint_id)
    if not bp:
        raise HTTPException(404, f"unknown blueprint {blueprint_id}")
    return bp


@app.get("/v1/provisions")
def list_provisions():
    return list(default_app().provisions.values())


@app.post("/v1/evaluations", response_model=EvaluationResponse)
def create_evaluation(req: EvaluationRequest):
    evaluation, summary = _run(req)
    return EvaluationResponse(parcel=summary, evaluation=evaluation)


@app.post("/v1/evaluations/report", response_class=HTMLResponse)
def create_report(req: EvaluationRequest):
    evaluation, summary = _run(req)
    return render_html(evaluation, summary, synthetic=default_app().dataset.synthetic)


@app.get("/v1/demo/{parcel_id}", response_class=HTMLResponse, summary="Report for a sample parcel")
def demo_report(parcel_id: str):
    gecaa = default_app()
    sample = gecaa.dataset.root / "parcels.geojson"
    parcels = {p.id: p for p in load_parcels(sample)} if sample.exists() else {}
    if parcel_id not in parcels:
        raise HTTPException(404, f"sample parcels: {sorted(parcels)}")
    evaluation, summary = gecaa.evaluate_parcel("mulden_rigolen_system_nrw", parcels[parcel_id])
    return render_html(evaluation, summary, synthetic=gecaa.dataset.synthetic)
