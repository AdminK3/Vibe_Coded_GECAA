# SPDX-License-Identifier: Apache-2.0
"""Match a project against data/knowledge/funding.yaml.

Grant amounts are only computed (label "abgeleitet") when the user entered costs and the source states a rate;
otherwise OFFEN. Every result carries source, source date and the date the entry was checked.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

FILE = Path(__file__).resolve().parents[3] / "data" / "knowledge" / "funding.yaml"
APPLICANTS = {"kommune": "Kommune", "oeffentlich": "Öffentliche Einrichtung", "sozial": "Soziale Einrichtung",
              "verband": "Verband/Verein", "unternehmen": "Unternehmen", "privat": "Privateigentümer"}


@lru_cache(maxsize=1)
def programmes() -> list[dict]:
    data = yaml.safe_load(FILE.read_text(encoding="utf-8"))
    for p in data["programmes"]:
        for k in ("checked",):
            p[k] = str(p.get(k)) if p.get(k) else None
        w = p.get("window") or {}
        for k in ("opens", "closes", "valid_until"):
            if w.get(k):
                w[k] = str(w[k])
    return data["programmes"]


def _window_state(w: dict, today: date) -> tuple[str, str]:
    t = w.get("type")
    o = date.fromisoformat(w["opens"]) if w.get("opens") else None
    c = date.fromisoformat(w["closes"]) if w.get("closes") else None
    if t == "laufend":
        return "offen", "laufend beantragbar"
    if t == "fenster":
        if c and c < today:
            return "geschlossen", f"Fenster geschlossen seit {c.strftime('%d.%m.%Y')}"
        if o and o > today:
            return "bald", f"öffnet am {o.strftime('%d.%m.%Y')}"
        if c:
            return "offen", f"offen bis {c.strftime('%d.%m.%Y')} (noch {(c - today).days} Tage)"
    return "OFFEN", "Frist OFFEN"


def match(project: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    applicant = project.get("applicant")
    kinds = sorted({m.get("kind") for m in project.get("measures") or [] if m.get("kind")})
    in_nrw = (project.get("site") or {}).get("state", "NRW") == "NRW"
    total = None
    rows = [c for c in project.get("costs") or [] if c.get("qty") is not None and c.get("unit_price") is not None]
    if rows:
        total = sum(float(c["qty"]) * float(c["unit_price"]) for c in rows)
    out = []
    for p in programmes():
        reasons, open_q, blockers = [], [], []
        if not applicant:
            open_q.append("Antragsteller OFFEN.")
        elif applicant in p["applicants"]:
            reasons.append(f"Antragsteller passt ({APPLICANTS.get(applicant, applicant)}).")
        else:
            blockers.append(f"{APPLICANTS.get(applicant, applicant)} ist nicht antragsberechtigt.")
        if p["region"] == "NRW" and not in_nrw:
            blockers.append("Nur für Nordrhein-Westfalen.")
        covered = {k: p["measures"].get(k) for k in kinds if (p.get("measures") or {}).get(k)}
        for k, txt in covered.items():
            reasons.append(f"{k.capitalize()}: {txt}.")
        not_cov = [k for k in kinds if k not in covered]
        if kinds and not covered:
            blockers.append("Keine der geplanten Maßnahmen ist in der Quelle als förderfähig genannt.")
        elif not_cov:
            open_q.append(f"Nicht abgedeckt: {', '.join(not_cov)}.")
        if p.get("concept_required"):
            open_q.append("Umsetzung setzt in der Regel ein Anpassungskonzept voraus – vorhanden?")
        if p.get("size_rule") and total is not None:
            sr = p["size_rule"]
            reasons.append(f"Kosten {'≥' if total >= sr['threshold_net_eur'] else '<'} {sr['threshold_net_eur']:,.0f} € netto → "
                           f"{sr['above'] if total >= sr['threshold_net_eur'] else sr['below']}.".replace(",", "."))
        state, wtext = _window_state(p.get("window") or {}, today)
        if state == "geschlossen":
            open_q.append("Derzeit kein offenes Förderfenster.")
        elif state == "OFFEN":
            open_q.append("Antragsfrist in der Quelle nicht genannt – beim Fördergeber klären.")
        rate = p.get("rate")
        if rate is not None and project.get("budget_weak") and p.get("rate_reduced_budget"):
            rate = p["rate_reduced_budget"]
        grant, grant_note = None, "OFFEN"
        if total is not None and rate is not None:
            basis = total
            cap = ((p.get("caps") or {}).get(kinds[0]) or {}).get("max_total_cost_eur") if len(kinds) == 1 else None
            if cap and basis > cap:
                basis = cap
                open_q.append(f"Förderfähige Kosten auf {cap:,.0f} € begrenzt.".replace(",", "."))
            grant = round(basis * rate, 0)
            grant_note = f"abgeleitet: {rate * 100:.0f} % × {basis:,.0f} € (alle Kosten als förderfähig angenommen)".replace(",", ".")
            if p.get("max_grant_eur") and grant > p["max_grant_eur"]:
                grant = float(p["max_grant_eur"])
                grant_note += f", gedeckelt auf Höchstbetrag {p['max_grant_eur']:,.0f} €".replace(",", ".")
            if p.get("min_grant_eur") and grant < p["min_grant_eur"]:
                blockers.append(f"Zuschuss unter dem Mindestbetrag von {p['min_grant_eur']:,.0f} €.".replace(",", "."))
        elif total is None:
            grant_note = "OFFEN – Kosten eintragen"
        fit = "passt nicht" if blockers else ("passt" if reasons and not open_q and state not in ("geschlossen", "OFFEN") else "prüfen")
        out.append({"id": p["id"], "name": p["name"], "funder": p["funder"], "level": p["level"], "fit": fit,
                    "reasons": reasons, "open": open_q, "blockers": blockers, "window_state": state, "window": wtext,
                    "window_note": (p.get("window") or {}).get("note"), "rate": rate, "rate_note": p.get("rate_note"),
                    "grant_eur": grant, "grant_note": grant_note, "before_start": p.get("before_start"),
                    "notes": [n for n in (p.get("duration_note"), p.get("binding_note"), p.get("max_note"),
                                          ((p.get("caps") or {}).get(kinds[0]) or {}).get("note") if len(kinds) == 1 else None,
                                          p.get("applicant_note")) if n],
                    "source": p["source"], "source_label": p["source_label"], "checked": p.get("checked")})
    order = {"passt": 0, "prüfen": 1, "passt nicht": 2}
    return sorted(out, key=lambda r: (order[r["fit"]], r["window_state"] == "geschlossen"))
