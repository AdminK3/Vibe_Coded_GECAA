# SPDX-License-Identifier: Apache-2.0
"""German PDF report (Typst) from the v3 results: rain run, heat, CRA rows and the infiltration check.

Only numbers from the inputs are printed (formatted, never derived). Expected damage is labelled as a lower
bound / order of magnitude. Typst (Apache-2.0) via the `typst` PyPI package.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[3] / "report" / "templates" / "klimarisiko.typ"
MAX_BUILDINGS = 40


def _n(v, digits: int = 0, unit: str = "") -> str:
    if v is None or v == "":
        return "–"
    if not isinstance(v, (int, float)):
        return str(v)
    s = f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")
    return f"{s} {unit}".strip()


def _site(site: dict) -> dict:
    rows = []
    labels = {"name": "Bezeichnung", "address": "Adresse", "municipality": "Kommune", "parcel": "Flurstück",
              "lon": "Länge (WGS84)", "lat": "Breite (WGS84)", "provider": "Datengrundlage"}
    for k, lab in labels.items():
        if site.get(k) not in (None, ""):
            rows.append([lab, _n(site[k], 5) if k in ("lon", "lat") else str(site[k])])
    for k, v in site.items():
        if k not in labels and isinstance(v, (str, int, float)) and k != "title":
            rows.append([str(k), str(v)])
    title = site.get("title") or site.get("name") or site.get("address") or "Standort"
    return {"title": str(title), "rows": rows or [["Standort", "keine Angaben"]]}


def _check(check: dict | None) -> dict | None:
    if not check:
        return None
    rows = [[k, str(check[f])] for k, f in (("Maßnahme", "blueprint_title"), ("Ergebnis", "verdict_label"),
                                             ("Sicherheit", "certainty")) if check.get(f) is not None]
    if isinstance(check.get("confidence"), (int, float)):
        rows.append(["Wahrscheinlichkeit", _n(check["confidence"] * 100, 0, "%")])
    for g in check.get("gaps") or []:
        rows.append(["Datenlücke", str(g)])
    findings = [[str(f.get("label", "")), str(f.get("most_likely_label", "")), str(f.get("explanation", ""))[:400]]
                for f in check.get("findings") or []]
    return {"rows": rows or [["Prüfung", "keine Angaben"]], "findings": findings}


def _rain(rain: dict | None) -> tuple[dict | None, list[str]]:
    if not rain:
        return None, []
    sc = (rain.get("scenarios") or ["heute"])[0]
    rps = rain.get("rps") or []
    rp_max = str(max(rps)) if rps else None
    imp = (rain.get("impact") or {}).get(sc, {})
    thr = rain.get("threshold_m", 0.10)
    r = rain.get("rain") or {}
    rows = [["Szenario", str(sc)], ["Wiederkehrintervalle", ", ".join(f"{x} a" for x in rps)],
            ["Regenquelle", str(r.get("source", "–"))], ["Dauerstufe", _n(r.get("duration_min"), 0, "min")],
            ["Schwellenhöhe", _n(thr * 100, 0, "cm")]]
    if rain.get("client_supplied"):
        rows.insert(0, ["Herkunft der Werte", "vom Browser übermittelt (Server-Ergebnis abgelaufen), nicht geprüft"])
    eai = imp.get("aai_agg")
    if eai is not None:
        rows.append(["Erwarteter Jahresschaden (Untergrenze/Größenordnung)", _n(eai, 0, "EUR/Jahr")])
    if imp.get("engine"):
        rows.append(["Rechenkern", str(imp["engine"])])
    ev_top = next((e for e in rain.get("events") or [] if e.get("scenario") == sc and str(e.get("rp")) == rp_max), None)
    if ev_top:
        b = ev_top.get("base") or {}
        rows += [[f"Regen {rp_max}-jährlich", _n(ev_top.get("rain_mm"), 1, "mm")],
                 ["Größte Wassertiefe", _n(b.get("max_depth_m"), 2, "m")],
                 ["Fläche über 10 cm", _n(b.get("area_over_10cm_m2"), 0, "m²")]]
    curve = [[str(c.get("rp")), float(c.get("eur") or 0)] for c in imp.get("freq_curve") or []]
    top = _n(max([c[1] for c in curve] or [0]), 0, "EUR")
    head = ["Gebäude", "Art", f"Tiefe {rp_max}-j. (m)", "Jahresschaden (Untergrenze)"]
    bl = [b for b in rain.get("buildings") or [] if (b.get("depth") or {}).get(rp_max, 0) > 0 or b.get("eai_eur")]
    rows_b = [[str(b.get("label") or b.get("id")), str(b.get("cls_de") or b.get("cls") or ""),
               _n((b.get("depth") or {}).get(rp_max), 2), _n(b.get("eai_eur"), 0, "EUR")] for b in bl[:MAX_BUILDINGS]]
    summary = [f"Starkregen: {len(bl)} Gebäude mit Wasser am Gebäude"
               + (f"; erwarteter Jahresschaden (Untergrenze/Größenordnung) {_n(eai, 0, 'EUR')}" if eai is not None else "")]
    return {"rows": rows, "curve": curve, "curve_top": top, "building_head": head, "buildings": rows_b,
            "more": max(0, len(bl) - MAX_BUILDINGS)}, summary


def _heat(heat: dict | None) -> tuple[dict | None, list[str]]:
    if not heat:
        return None, []
    ca = heat.get("climate_analysis") or {}
    hd = heat.get("hot_days") or {}
    rows = []
    if ca.get("pet_typisch") is not None:
        rows.append(["PET typischer Sommertag", f"{_n(ca['pet_typisch'], 1, '°C')} ({ca.get('pet_typisch_klasse') or '–'})"])
    if ca.get("pet_extrem") is not None:
        rows.append(["PET extremer Sommertag", f"{_n(ca['pet_extrem'], 1, '°C')} ({ca.get('pet_extrem_klasse') or '–'})"])
    for k, lab in (("mean_1961_1990", "Heiße Tage 1961–1990 (Mittel)"), ("mean_1991_2020", "Heiße Tage 1991–2020 (Mittel)"),
                   ("mean_last10", "Heiße Tage letzte 10 Jahre"), ("trend_per_decade", "Trend je Jahrzehnt")):
        if hd.get(k) is not None:
            rows.append([lab, _n(hd[k], 1, "Tage")])
    for k in ("hot_days_error", "climate_analysis_error"):
        if heat.get(k):
            rows.append(["Hinweis", f"Daten nicht verfügbar: {heat[k]}"])
    pts = [[str(y), float(v)] for y, v in zip(hd.get("years") or [], hd.get("values") or [])]
    summ = []
    if ca.get("pet_typisch") is not None:
        summ.append(f"Hitze: PET typischer Sommertag {_n(ca['pet_typisch'], 1, '°C')}")
    if hd.get("mean_last10") is not None:
        summ.append(f"Hitze: {_n(hd['mean_last10'], 1)} heiße Tage je Jahr (letzte 10 Jahre)")
    return {"rows": rows or [["Hitze", "keine Daten"]], "hot_days": pts}, summ


def _sources(rain, heat, cra_src) -> list[str]:
    s = []
    for a in (rain or {}).get("assumptions") or []:
        if a.get("quelle") and a["quelle"] not in s:
            s.append(str(a["quelle"]))
    for blk in ((heat or {}).get("climate_analysis") or {}, (heat or {}).get("hot_days") or {}):
        if blk.get("source") and str(blk["source"]) not in s:
            s.append(str(blk["source"]))
    if cra_src:
        s.append(str(cra_src))
    return s


def view_model(rain, heat, cra, check, site) -> dict:
    r, s1 = _rain(rain)
    h, s2 = _heat(heat)
    rows = cra.get("rows") if isinstance(cra, dict) else (cra or [])
    cra_rows = [[str(x.get("hazard", "")), str(x.get("severity_now", "")), str(x.get("severity_future", "")),
                 str(x.get("urgency", "")), str(x.get("capacity", "")), str(x.get("priority", "")),
                 str(x.get("basis", ""))] for x in rows]
    summary = s1 + s2 + [f"Risikobewertung {x[0]}: Priorität {x[5]}" for x in cra_rows]
    ch = _check(check)
    if check and check.get("verdict_label"):
        summary.insert(0, f"Versickerung: {check['verdict_label']}")
    ass = [[str(a.get("id", "")), str(a.get("art", "")), str(a.get("quelle", "")), str(a.get("text", ""))]
           for a in (rain or {}).get("assumptions") or []]
    return {"created": date.today().strftime("%d.%m.%Y"), "site": _site(site or {}), "summary": summary,
            "check": ch, "rain": r, "heat": h, "cra": cra_rows, "assumptions": ass,
            "sources": _sources(rain, heat, cra.get("source") if isinstance(cra, dict) else None)}


def build_pdf(rain: dict | None, heat: dict | None, cra: list | None, check: dict | None, site: dict) -> bytes:
    import typst

    data = json.dumps(view_model(rain, heat, cra, check, site), ensure_ascii=False, default=str)
    return typst.compile(str(TEMPLATE), root=str(TEMPLATE.parent), format="pdf", sys_inputs={"data": data})
