# SPDX-License-Identifier: Apache-2.0
"""v5: projects (plan, tasks, costs), funding match, free-form reports."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os
    os.environ["GECAA_PROJECTS_DB"] = str(tmp_path_factory.mktemp("db") / "p.sqlite")
    from gecaa.api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def project(client):
    prov = next(p for p in client.get("/v2/providers").json() if p["id"] == "synthetic")
    w, s, e, n = prov["bounds"]
    lon, lat = (w + e) / 2, (s + n) / 2
    d = 0.0004
    poly = {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]}
    body = {"lon": lon, "lat": lat, "box_m": 300, "prefer_worker": False,
            "measures": [{"id": "M1", "name": "Entsiegelung 1", "kind": "entsiegelung", "geometry": poly}]}
    r = client.post("/v3/risk/rain3d?provider=synthetic", json=body)
    assert r.status_code == 200, r.text
    run = r.json()
    p = client.post("/v5/projects", json={"name": "Schulhof Test", "site": {"name": "Demo", "lon": lon, "lat": lat},
                                          "run_id": run["run_id"],
                                          "measures": [{"id": "M1", "name": "Entsiegelung 1", "kind": "entsiegelung", "geometry": poly}]})
    assert p.status_code == 200, p.text
    return p.json()


def test_create_uses_server_result(project):
    assert project["risk"]["run_id"] and project["risk"]["eai"] is not None
    m = project["measures"][0]
    assert m["retained_m3"] is not None and m["area_m2"] > 1000      # ~89 m x 89 m polygon
    keys = [p["key"] for p in project["phases"]]
    assert keys[0] == "grundlagen" and "foerderung" in keys
    texts = [t["text"] for p in project["phases"] for t in p["tasks"]]
    assert any("Altlasten" in t for t in texts)                      # measure-specific task
    assert all(p["start"] is None for p in project["phases"])       # no invented dates


def test_unknown_run_gives_open_risk(client):
    p = client.post("/v5/projects", json={"name": "Ohne Lauf", "run_id": "nope"}).json()
    assert p["risk"] == {} and p["measures"] == []


def _put(client, p, **kw):
    r = client.put(f"/v5/projects/{p['id']}", json={"version": p["version"], **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_checks_costs_funding_and_conflict(client, project):
    p = client.get(f"/v5/projects/{project['id']}").json()
    assert any(c["text"].startswith("Kosten OFFEN") for c in p["checks"])
    phases = p["phases"]
    by = {ph["key"]: ph for ph in phases}
    by["foerderung"].update(start="2026-11-01", end="2027-03-31", owner="Umweltamt")
    by["vergabe"].update(start="2027-01-15", end="2027-04-30")
    p = _put(client, p, applicant="kommune", phases=phases, funding={"selected": ["kfw-444-nkk"]},
             costs=[{"id": "c1", "item": "Entsiegelung", "qty": 1500, "unit": "m²", "unit_price": 120, "source": "Angebot A"}],
             note="Test")
    assert p["costs_total"] == 180000
    assert any(c["level"] == "fehler" and "vor Vorhabenbeginn" in c["text"] for c in p["checks"])
    stale = client.put(f"/v5/projects/{p['id']}", json={"version": p["version"] - 1, "name": "x"})
    assert stale.status_code == 409
    res = {r["id"]: r for r in client.get(f"/v5/projects/{p['id']}/funding").json()}
    k = res["kfw-444-nkk"]
    assert k["rate"] == 0.5 and k["grant_eur"] == 90000 and "abgeleitet" in k["grant_note"]
    assert res["nrw-klimaanpassung-kommunen"]["rate"] is None and res["nrw-klimaanpassung-kommunen"]["grant_eur"] is None
    assert any("Verfahren 2" in x for x in res["nrw-klimaanpassung-kommunen"]["reasons"])
    p = _put(client, p, budget_weak=True)
    k = {r["id"]: r for r in client.get(f"/v5/projects/{p['id']}/funding").json()}["kfw-444-nkk"]
    assert k["rate"] == 0.8 and k["grant_eur"] == 144000
    p = _put(client, p, applicant="privat")
    k = {r["id"]: r for r in client.get(f"/v5/projects/{p['id']}/funding").json()}["kfw-444-nkk"]
    assert k["fit"] == "passt nicht"
    _put(client, p, applicant="kommune")


def test_funding_rules_unit():
    from gecaa.projects import funding
    proj = {"applicant": "kommune", "measures": [{"kind": "entsiegelung"}],
            "costs": [{"qty": 1, "unit_price": 2_000_000}]}
    r = {x["id"]: x for x in funding.match(proj, today=date(2026, 10, 17))}
    assert r["kfw-444-nkk"]["grant_eur"] == 500000                          # capped at 1 Mio € eligible cost
    assert r["anpaso"]["window_state"] == "offen" and "30.11.2026" in r["anpaso"]["window"]
    assert r["das-anpassung"]["window_state"] == "geschlossen"
    small = {x["id"]: x for x in funding.match({**proj, "costs": [{"qty": 1, "unit_price": 20000}]})}
    assert any("Mindestbetrag" in b for b in small["kfw-444-nkk"]["blockers"])   # 10.000 € < 15.000 € minimum
    assert {x["id"]: x for x in funding.match(proj, today=date(2026, 9, 1))}["anpaso"]["window_state"] == "bald"


def test_exports(client, project):
    ics = client.get(f"/v5/projects/{project['id']}/plan.ics")
    assert ics.status_code == 200 and "BEGIN:VEVENT" in ics.text and "DTSTART;VALUE=DATE:20261101" in ics.text
    csv = client.get(f"/v5/projects/{project['id']}/tasks.csv")
    assert csv.status_code == 200 and "Phase;Aufgabe" in csv.text


def test_report_draft_edit_pdf(client, project):
    aud = client.get("/v5/report/audiences").json()
    assert set(aud) == {"rat", "foerder", "buerger", "sachstand"}
    d = client.get(f"/v5/projects/{project['id']}/report/draft", params={"audience": "foerder"}).json()
    titles = [s["title"] for s in d["sections"]]
    assert "Finanzierung und Förderung" in titles
    fund = next(s for s in d["sections"] if s["key"] == "foerderung")
    assert "KfW" in fund["md"] or "NKK" in fund["md"]
    secs = d["sections"] + [{"key": "eigen-1", "title": "Eigener Text", "include": True,
                             "md": "# Zwischenüberschrift\n**fett** und *kursiv* #import \"x\" $ x^2 $ @ref <tag> [link](x)\n\n- Punkt 1\n- Punkt 2\n\n| a | b |\n|---|---|\n| 1 | 2 |"}]
    secs[1]["include"] = False
    p = client.get(f"/v5/projects/{project['id']}").json()
    _put(client, p, report={"audience": "foerder", "sections": secs})
    pdf = client.get(f"/v5/projects/{project['id']}/report.pdf")
    assert pdf.status_code == 200, pdf.text[:500]
    from pypdf import PdfReader
    text = "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Eigener Text" in text and "#import" in text and secs[1]["title"] not in text.split("Inhalt")[-1]
    md = client.get(f"/v5/projects/{project['id']}/report.md").text
    assert md.startswith("# Projektskizze") and "## Eigener Text" in md
    one = client.get(f"/v5/projects/{project['id']}/report/draft", params={"audience": "rat", "section": "kosten"}).json()
    assert "180.000 €" in one["md"]


def test_md_blocks():
    from gecaa.projects.report import md_blocks
    b = md_blocks("# T\nText **b**\n\n- a\n- b\n\n| x | y |\n|--|--|\n| 1 | 2 |")
    assert [x["k"] for x in b] == ["h", "p", "ul", "table"]
    assert b[1]["runs"][1] == {"t": "b", "b": True} and b[3]["cols"] == 2 and len(b[3]["rows"]) == 2


def test_limits_and_pages(client):
    assert client.post("/v5/projects", json={"name": "x" * 500}).status_code == 422
    assert client.get("/v5/projects/nope").status_code == 404
    r = client.get("/projekte")
    assert r.status_code == 200 and "projects.js" in r.text
    assert client.get("/static/projects.js").status_code == 200
