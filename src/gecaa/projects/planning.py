# SPDX-License-Identifier: Apache-2.0
"""Implementation planning for adaptation measures: phase template, measure-specific tasks, plan checks,
calendar (ICS) and task list (CSV) export.

No durations or costs are invented: dates, owners and prices come from the user; missing ones are OFFEN.
Task texts are checks to clarify ("klären", "prüfen"), not statements about what the law requires.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone

KIND_DE = {"mulde": "Versickerungsmulde", "gruendach": "Gründach", "entsiegelung": "Entsiegelung",
           "baeume": "Bäume / Baumrigolen"}

PHASES = [
    ("grundlagen", "Grundlagen & Bestand"),
    ("vorplanung", "Vorplanung & Variantenvergleich"),
    ("foerderung", "Finanzierung & Förderantrag"),
    ("genehmigung", "Abstimmung & Genehmigung"),
    ("vergabe", "Ausführungsplanung & Vergabe"),
    ("umsetzung", "Bau / Umsetzung"),
    ("pflege", "Pflege & Wirkungskontrolle"),
]
# phases that count as "Vorhabenbeginn" for funding rules (commissioning/building)
START_PHASES = ("vergabe", "umsetzung")

GENERIC_TASKS = {
    "grundlagen": ["Zuständigkeit und Projektleitung festlegen", "Eigentumsverhältnisse der Flächen klären",
                   "Leitungsauskunft einholen"],
    "vorplanung": ["Varianten mit GECAA-Ergebnissen vergleichen", "Kostenschätzung mit Angeboten oder Kostenkennwerten belegen"],
    "foerderung": ["Passende Förderprogramme prüfen (Reiter Förderung)", "Förderantrag stellen",
                   "Förderzusage abwarten (vor Auftragsvergabe)"],
    "genehmigung": ["Beteiligte Ämter und Anlieger einbinden"],
    "vergabe": ["Leistungsverzeichnis erstellen", "Vergabe durchführen"],
    "umsetzung": ["Bauüberwachung und Abnahme", "Bestandsdokumentation erstellen"],
    "pflege": ["Pflegeverantwortung und -budget festlegen", "Wirkung nach Starkregen dokumentieren",
               "Verwendungsnachweis für Förderung erstellen"],
}

MEASURE_TASKS = {
    "mulde": [("grundlagen", "Versickerungsfähigkeit (kf-Wert) und Grundwasserabstand durch Gutachten klären"),
              ("grundlagen", "Altlastenverdacht im Kataster prüfen"),
              ("genehmigung", "Erlaubnispflicht der Versickerung mit der Unteren Wasserbehörde klären"),
              ("pflege", "Mulde nach Starkregen auf Schlamm und Verdichtung kontrollieren")],
    "gruendach": [("grundlagen", "Tragfähigkeit des Dachs (Statik) prüfen lassen"),
                  ("grundlagen", "Zustand der Dachabdichtung prüfen"),
                  ("genehmigung", "Gestaltungs- und Denkmalschutzvorgaben klären"),
                  ("pflege", "Pflegeintervalle der Dachbegrünung festlegen")],
    "entsiegelung": [("grundlagen", "Bodenbelastung und Altlasten unter der Versiegelung klären"),
                     ("grundlagen", "Leitungen und Nutzungsansprüche (Feuerwehr, Zufahrten) klären"),
                     ("vorplanung", "Entsorgung des Aufbruchmaterials planen")],
    "baeume": [("grundlagen", "Standorte auf Leitungen und Wurzelraum prüfen"),
               ("vorplanung", "Baumarten nach Standort und Klimaverträglichkeit auswählen"),
               ("pflege", "Fertigstellungs- und Entwicklungspflege sicherstellen")],
}


def _tid() -> str:
    return uuid.uuid4().hex[:8]


def make_phases(measures: list[dict]) -> list[dict]:
    kinds = []
    for m in measures:
        if m.get("kind") in MEASURE_TASKS and m["kind"] not in kinds:
            kinds.append(m["kind"])
    phases = []
    for key, label in PHASES:
        tasks = [{"id": _tid(), "text": t, "done": False, "due": None, "owner": None, "measure": None}
                 for t in GENERIC_TASKS[key]]
        for k in kinds:
            tasks += [{"id": _tid(), "text": t, "done": False, "due": None, "owner": None, "measure": KIND_DE[k]}
                      for ph, t in MEASURE_TASKS[k] if ph == key]
        phases.append({"key": key, "label": label, "start": None, "end": None, "owner": None, "tasks": tasks})
    return phases


def _d(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def phase_status(ph: dict, today: date) -> str:
    tasks = ph.get("tasks") or []
    if tasks and all(t.get("done") for t in tasks):
        return "erledigt"
    s, e = _d(ph.get("start")), _d(ph.get("end"))
    if e and e < today:
        return "überfällig"
    if s and s <= today:
        return "läuft"
    if not s or not e:
        return "OFFEN"
    return "geplant"


def check(project: dict, programmes: list[dict] | None = None, today: date | None = None) -> list[dict]:
    """Plan checks -> list of {level: fehler|warnung|offen, text}."""
    today = today or date.today()
    out = []
    phases = project.get("phases") or []
    by = {p["key"]: p for p in phases}
    for p in phases:
        s, e = _d(p.get("start")), _d(p.get("end"))
        if s and e and e < s:
            out.append({"level": "fehler", "text": f"{p['label']}: Ende liegt vor dem Beginn."})
        if not (s and e):
            out.append({"level": "offen", "text": f"{p['label']}: Zeitraum OFFEN."})
        if not p.get("owner"):
            out.append({"level": "offen", "text": f"{p['label']}: Verantwortliche Stelle OFFEN."})
        for t in p.get("tasks") or []:
            if not t.get("done") and _d(t.get("due")) and _d(t["due"]) < today:
                out.append({"level": "warnung", "text": f"Überfällig: {t['text']} (fällig {t['due']})."})
    keys = [k for k, _ in PHASES if k in by]
    for a, b in zip(keys, keys[1:]):
        ea, sb = _d(by[a].get("end")), _d(by[b].get("start"))
        if ea and sb and sb < ea and a == "foerderung" and b in START_PHASES:
            out.append({"level": "warnung", "text": f"{by[b]['label']} beginnt vor Ende von {by[a]['label']}."})
    sel = set((project.get("funding") or {}).get("selected") or [])
    fund_end = _d((by.get("foerderung") or {}).get("end"))
    for prog in programmes or []:
        if prog["id"] not in sel:
            continue
        starts = [d for d in (_d((by.get(k) or {}).get("start")) for k in START_PHASES) if d]
        if prog.get("before_start") and starts and fund_end and min(starts) < fund_end:
            out.append({"level": "fehler", "text": f"{prog['name']}: Die Zusage muss vor Vorhabenbeginn vorliegen – "
                        f"Vergabe/Umsetzung beginnt am {min(starts).isoformat()}, Förderphase endet am {fund_end.isoformat()}."})
        w = prog.get("window") or {}
        closes = _d(w.get("closes"))
        if w.get("type") == "fenster" and closes:
            if closes < today:
                out.append({"level": "warnung", "text": f"{prog['name']}: Förderfenster ist geschlossen ({closes.isoformat()})."})
            else:
                fs = _d((by.get("foerderung") or {}).get("start"))
                if fs and fs > closes:
                    out.append({"level": "fehler", "text": f"{prog['name']}: Förderphase beginnt nach Fristende {closes.isoformat()}."})
    if not project.get("costs"):
        out.append({"level": "offen", "text": "Kosten OFFEN – ohne Kosten keine Förderhöhe."})
    return out


def progress(project: dict) -> dict:
    tasks = [t for p in project.get("phases") or [] for t in p.get("tasks") or []]
    done = sum(1 for t in tasks if t.get("done"))
    return {"tasks": len(tasks), "done": done, "share": round(done / len(tasks), 3) if tasks else 0.0}


def costs_total(project: dict) -> float | None:
    rows = [c for c in project.get("costs") or [] if c.get("qty") is not None and c.get("unit_price") is not None]
    return round(sum(float(c["qty"]) * float(c["unit_price"]) for c in rows), 2) if rows else None


def _ics_text(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def to_ics(project: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GECAA//Projektplan//DE", "CALSCALE:GREGORIAN"]

    def ev(uid, start, end, summary):
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@gecaa", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
                      f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}", f"SUMMARY:{_ics_text(summary)}", "END:VEVENT"])

    name = project.get("name") or "Projekt"
    for p in project.get("phases") or []:
        s, e = _d(p.get("start")), _d(p.get("end"))
        if s and e and e >= s:
            ev(f"{project['id']}-{p['key']}", s, date.fromordinal(e.toordinal() + 1), f"{name}: {p['label']}")
        for t in p.get("tasks") or []:
            d = _d(t.get("due"))
            if d and not t.get("done"):
                ev(f"{project['id']}-{t['id']}", d, date.fromordinal(d.toordinal() + 1), f"{name}: {t['text']}")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_csv(project: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Phase", "Aufgabe", "Maßnahme", "Verantwortlich", "Fällig", "Erledigt"])
    for p in project.get("phases") or []:
        for t in p.get("tasks") or []:
            row = [p["label"], t["text"], t.get("measure") or "", t.get("owner") or p.get("owner") or "OFFEN",
                   t.get("due") or "OFFEN", "ja" if t.get("done") else "nein"]
            w.writerow(["'" + c if c[:1] in "=+-@" else c for c in map(str, row)])   # no spreadsheet formulas
    return "\ufeff" + buf.getvalue()
