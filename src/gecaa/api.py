# SPDX-License-Identifier: Apache-2.0
"""REST API.

Run locally:  uvicorn gecaa.api:app --reload
Docs:         http://localhost:8000/docs
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import PACKAGE_DIR, __version__
from .models import Evaluation
from .report import render_html
from .service import default_app
from .spatial import load_parcels
from .spatial.layers import geojson_wgs84, layer_catalogue, raster_overlay

WEB_DIR = PACKAGE_DIR / "web"

app = FastAPI(
    title="GECAA API",
    version=__version__,
    description="Cited, auditable evaluation of climate adaptation measures for a specific site. "
                "Results are decision support; the responsible authority decides.",
    license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
)


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

from .api_v2 import router as v2_router  # noqa: E402

app.include_router(v2_router)
from .api_v3 import router as v3_router  # noqa: E402

app.include_router(v3_router)


@app.get("/", include_in_schema=False)
def web_app():
    return FileResponse(WEB_DIR / "v2.html")


@app.get("/v1", include_in_schema=False)
def web_app_v1():
    return FileResponse(WEB_DIR / "index.html")


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


# --- map layers -------------------------------------------------------------

def _default_params() -> dict:
    gecaa = default_app()
    return gecaa.blueprints["mulden_rigolen_system_nrw"].parameters


@app.get("/v1/dataset")
def dataset_info():
    ds = default_app().dataset
    _, bounds = raster_overlay(ds, "kf", _default_params())
    return {"name": ds.config.get("name"), "synthetic": ds.synthetic, "crs": ds.config.get("crs"),
            "bounds": bounds,
            "layers": {k: {"source": v.get("source"), "licence": v.get("licence"), "description": v.get("description")}
                       for k, v in ds.config["layers"].items()}}


@app.get("/v1/layers")
def list_layers():
    ds = default_app().dataset
    out = []
    for layer in layer_catalogue(ds, _default_params()):
        entry = {**layer, "legend": [[label, f"rgba({c[0]},{c[1]},{c[2]},{c[3] / 255:.2f})"] for label, c in layer["legend"]]}
        if layer["type"] == "raster":
            _, bounds = raster_overlay(ds, layer["id"], _default_params())
            entry["url"] = f"/v1/layers/{layer['id']}.png"
            entry["bounds"] = bounds
        else:
            entry["url"] = f"/v1/layers/{layer['id']}.geojson"
        out.append(entry)
    return out


@app.get("/v1/layers/{layer_id}.png", response_class=Response)
def layer_png(layer_id: str):
    try:
        png, _ = raster_overlay(default_app().dataset, layer_id, _default_params())
    except KeyError:
        raise HTTPException(404, f"unknown raster layer {layer_id}")
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/v1/layers/{layer_id}.geojson")
def layer_geojson(layer_id: str):
    ds = default_app().dataset
    if layer_id == "zones":
        return geojson_wgs84(ds.layer("water_protection_zones")["path"])
    if layer_id == "parcels":
        path = ds.root / "parcels.geojson"
        if not path.exists():
            return {"type": "FeatureCollection", "features": []}
        return geojson_wgs84(path)
    raise HTTPException(404, f"unknown vector layer {layer_id}")


# --- Knowledge Agent ----------------------------------------------------------

@app.get("/v1/knowledge/snapshots/{provision_id}")
def provision_history(provision_id: str):
    gecaa = default_app()
    if provision_id not in gecaa.provisions:
        raise HTTPException(404, f"unknown provision {provision_id}")
    return [{k: v for k, v in snap.items()} for snap in gecaa.snapshots.history(provision_id)]


@app.post("/v1/knowledge/refresh")
def refresh_knowledge():
    """Fetch the current text of every federal provision from NeuRIS and record changes."""
    from .knowledge.neuris import NeurisClient, search_term_for

    gecaa = default_app()
    client = NeurisClient()
    results = []
    for prov in gecaa.provisions.values():
        if not prov.eli:
            results.append({"id": prov.id, "citation": prov.citation, "status": "skipped",
                            "message": "No federal ELI; state law and standards are maintained by hand."})
            continue
        try:
            event = client.refresh(prov, gecaa.snapshots, search_term_for(prov))
        except Exception as exc:
            results.append({"id": prov.id, "citation": prov.citation, "status": "failed", "message": str(exc)[:300]})
            continue
        if event is None:
            results.append({"id": prov.id, "citation": prov.citation, "status": "unchanged", "message": "Text unchanged."})
        else:
            results.append({"id": prov.id, "citation": prov.citation, "status": "new" if event.is_new else "changed",
                            "message": event.describe()})
    gecaa.provisions = gecaa.snapshots.apply(gecaa.provisions)
    return results
