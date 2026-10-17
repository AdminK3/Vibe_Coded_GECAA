# SPDX-License-Identifier: Apache-2.0
"""v5 API: projects for implementing measures (plan, tasks, costs), funding match, free-form reports."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .projects import funding, planning, report, store

router = APIRouter(prefix="/v5", tags=["v5 (Projekte, Förderung, Berichte)"])

Applicant = Literal["kommune", "oeffentlich", "sozial", "verband", "unternehmen", "privat"]
Kind = Literal["mulde", "gruendach", "entsiegelung", "baeume"]
S = Field(default=None, max_length=300)


class MeasureIn(BaseModel):
    id: str = Field(max_length=40)
    name: str = Field(max_length=120)
    kind: Kind
    variant: str | None = Field(default=None, max_length=20)
    geometry: dict | None = None
    trees: int | None = Field(default=None, ge=0, le=5000)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    applicant: Applicant | None = None
    site: dict = Field(default_factory=dict)
    run_id: str | None = Field(default=None, max_length=64)
    measures: list[MeasureIn] = Field(default_factory=list, max_length=50)


class Task(BaseModel):
    id: str = Field(max_length=16)
    text: str = Field(max_length=300)
    done: bool = False
    due: str | None = Field(default=None, max_length=10)
    owner: str | None = Field(default=None, max_length=120)
    measure: str | None = Field(default=None, max_length=120)


class Phase(BaseModel):
    key: str = Field(max_length=40)
    label: str = Field(max_length=120)
    start: str | None = Field(default=None, max_length=10)
    end: str | None = Field(default=None, max_length=10)
    owner: str | None = Field(default=None, max_length=120)
    tasks: list[Task] = Field(default_factory=list, max_length=200)


class Cost(BaseModel):
    id: str = Field(max_length=16)
    item: str = Field(max_length=200)
    measure: str | None = Field(default=None, max_length=120)
    qty: float | None = Field(default=None, ge=0, le=1e7)
    unit: str | None = Field(default=None, max_length=20)
    unit_price: float | None = Field(default=None, ge=0, le=1e8)
    source: str | None = S


class Section(BaseModel):
    key: str = Field(max_length=40)
    title: str = Field(max_length=200)
    md: str = Field(default="", max_length=30000)
    include: bool = True
    auto: bool = False


class ProjectUpdate(BaseModel):
    version: int
    note: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=160)
    applicant: Applicant | None = None
    budget_weak: bool | None = None
    status: Literal["idee", "planung", "beantragt", "umsetzung", "abgeschlossen"] | None = None
    phases: list[Phase] | None = Field(default=None, max_length=20)
    costs: list[Cost] | None = Field(default=None, max_length=300)
    funding: dict | None = None
    report: dict | None = None


def _area_m2(geom: dict | None) -> float | None:
    if not geom or geom.get("type") != "Polygon":
        return None
    try:
        from pyproj import Transformer
        from shapely.geometry import Polygon
        t = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
        ring = [t.transform(x, y) for x, y in geom["coordinates"][0][:5000]]
        return round(Polygon(ring).area, 0)
    except Exception:  # noqa: BLE001 - bad geometry -> OFFEN
        return None


def _risk_from_run(run_id: str | None) -> tuple[dict, dict]:
    """Risk summary and per-measure retention from the server's own stored run (never from client numbers)."""
    from .api_v3 import _RESULTS
    r = _RESULTS.get(run_id or "")
    if not r:
        return {}, {}
    sc = r["scenarios"][0]
    rp_max = r["rps"][-1]
    imp = (r.get("impact") or {}).get(sc, {})
    thr = r.get("threshold_m", 0.1)
    wet = [b for b in r.get("buildings") or [] if (b.get("depth") or {}).get(str(rp_max), 0) > thr]
    persons = [b["persons_est"] for b in wet if b.get("persons_est") is not None]
    risk = {"run_id": run_id, "scenario": sc, "rps": r["rps"], "rp_max": rp_max, "threshold_m": thr,
            "buildings_affected": len(wet), "buildings_total": len(r.get("buildings") or []),
            "eai": round(imp["aai_agg"], 0) if imp.get("aai_agg") is not None else None,
            "eai_with_measures": round((imp.get("measure") or {}).get("aai_agg"), 0)
            if (imp.get("measure") or {}).get("aai_agg") is not None else None,
            "persons": round(sum(persons)) if persons else None,
            "engine": (r.get("hydraulic_engine") or {}).get("engine"), "rain": (r.get("rain") or {}).get("source")}
    ret = {m["id"]: m for m in r.get("measures") or [] if m.get("id")}
    return risk, ret


def _get(pid: str) -> dict:
    p = store.get(pid)
    if not p:
        raise HTTPException(404, "Projekt nicht gefunden")
    return p


@router.get("/projects")
def list_projects():
    return store.list_all()


@router.post("/projects")
def create_project(body: ProjectIn):
    risk, ret = _risk_from_run(body.run_id)
    measures = []
    for m in body.measures:
        r = ret.get(m.id) or {}
        measures.append({"id": m.id, "name": m.name, "kind": m.kind, "variant": m.variant, "trees": m.trees,
                         "area_m2": r.get("area_m2") if r.get("area_m2") is not None else _area_m2(m.geometry), "retained_m3": r.get("retained_m3"),
                         "heat_note": r.get("heat_note"), "geometry": m.geometry})
    site = {k: v for k, v in (body.site or {}).items() if isinstance(v, (str, int, float)) and len(str(v)) < 300}
    doc = {"name": body.name, "applicant": body.applicant, "budget_weak": False, "status": "idee", "site": site,
           "risk": risk, "measures": measures, "phases": planning.make_phases(measures), "costs": [],
           "funding": {"selected": []}, "report": {"audience": "rat", "sections": []}}
    return store.create(doc)


@router.get("/projects/{pid}")
def get_project(pid: str):
    p = _get(pid)
    return {**p, "checks": planning.check(p, funding.programmes()), "progress": planning.progress(p),
            "costs_total": planning.costs_total(p),
            "phase_status": {ph["key"]: planning.phase_status(ph, date.today()) for ph in p.get("phases") or []}}


@router.put("/projects/{pid}")
def update_project(pid: str, body: ProjectUpdate):
    p = _get(pid)
    upd = body.model_dump(exclude_none=True, exclude={"version", "note"})
    if "funding" in upd:
        ids = {x["id"] for x in funding.programmes()}
        upd["funding"] = {"selected": [s for s in (upd["funding"].get("selected") or []) if s in ids][:10],
                          "notes": str(upd["funding"].get("notes") or "")[:5000]}
    if "report" in upd:
        aud = upd["report"].get("audience")
        secs = [Section(**s).model_dump() for s in (upd["report"].get("sections") or [])[:40]]
        upd["report"] = {"audience": aud if aud in report.AUDIENCES else "rat", "sections": secs}
    p.update(upd)
    try:
        saved = store.save(p, body.version, body.note)
    except store.Conflict:
        raise HTTPException(409, "Das Projekt wurde zwischenzeitlich geändert – bitte neu laden.")
    return get_project(saved["id"])


@router.delete("/projects/{pid}")
def delete_project(pid: str):
    if not store.delete(pid):
        raise HTTPException(404, "Projekt nicht gefunden")
    return {"deleted": pid}


@router.get("/projects/{pid}/funding")
def project_funding(pid: str):
    return funding.match(_get(pid))


@router.get("/funding/programmes")
def list_programmes():
    return funding.programmes()


@router.get("/projects/{pid}/report/draft")
def report_draft(pid: str, audience: str = "rat", section: str | None = None):
    if audience not in report.AUDIENCES:
        raise HTTPException(422, "Unbekannte Zielgruppe")
    p = _get(pid)
    if section:
        try:
            title, md = report.draft_section(section, p)
        except KeyError:
            raise HTTPException(422, "Unbekannter Abschnitt")
        return {"key": section, "title": title, "md": md, "include": True, "auto": True}
    return {"audience": audience, "title": report.AUDIENCES[audience][0], "sections": report.draft(p, audience)}


@router.get("/report/audiences")
def audiences():
    return {k: v[0] for k, v in report.AUDIENCES.items()}


def _sections(p: dict) -> tuple[str, list[dict]]:
    rep = p.get("report") or {}
    aud = rep.get("audience") or "rat"
    secs = rep.get("sections") or report.draft(p, aud)
    return report.AUDIENCES[aud][0], secs


def _fname(p: dict, ext: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in (p.get("name") or "projekt"))[:40].strip("-") or "projekt"
    return f'attachment; filename="gecaa-{safe}-{date.today().isoformat()}.{ext}"'


@router.get("/projects/{pid}/report.pdf")
def report_pdf(pid: str):
    p = _get(pid)
    title, secs = _sections(p)
    try:
        pdf = report.build_pdf(p, title, secs)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"PDF konnte nicht erzeugt werden: {exc}") from exc
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": _fname(p, "pdf")})


@router.get("/projects/{pid}/report.md")
def report_md(pid: str):
    p = _get(pid)
    title, secs = _sections(p)
    return Response(report.to_markdown(p, title, secs), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": _fname(p, "md")})


@router.get("/projects/{pid}/plan.ics")
def plan_ics(pid: str):
    p = _get(pid)
    return Response(planning.to_ics(p), media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": _fname(p, "ics")})


@router.get("/projects/{pid}/tasks.csv")
def tasks_csv(pid: str):
    p = _get(pid)
    return Response(planning.to_csv(p), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": _fname(p, "csv")})
