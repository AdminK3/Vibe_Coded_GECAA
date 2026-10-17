# SPDX-License-Identifier: Apache-2.0
"""Free-form project reports: audience templates produce editable Markdown sections from the project data;
the user rewrites, reorders, adds or drops sections; export to PDF (Typst) or Markdown.

Drafts only state figures that are in the project (user input or stored GECAA results, labelled); gaps are OFFEN.
Markdown is parsed into plain JSON blocks and rendered by the Typst template as strings, so user text is never
evaluated as Typst markup.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from . import funding, planning

TEMPLATE = Path(__file__).resolve().parents[3] / "report" / "templates" / "projektbericht.typ"

AUDIENCES = {
    "rat": ("Beschlussvorlage für Rat/Ausschuss", ["beschluss", "anlass", "risiko", "massnahmen", "kosten", "foerderung",
                                                   "zeitplan", "offen", "quellen"]),
    "foerder": ("Projektskizze für einen Förderantrag", ["ausgangslage", "ziele", "massnahmen", "kosten", "foerderung",
                                                         "zeitplan", "pflege", "quellen"]),
    "buerger": ("Information für Bürgerinnen und Bürger", ["worum", "massnahmen_kurz", "zeitplan_kurz", "mitmachen"]),
    "sachstand": ("Sachstandsbericht", ["stand", "erledigt", "naechste", "offen"]),
}


def _eur(v) -> str:
    return "OFFEN" if v is None else f"{v:,.0f} €".replace(",", ".")


def _num(v, unit="", d=0) -> str:
    if v is None:
        return "OFFEN"
    return (f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") + (f" {unit}" if unit else "")).strip()


def _fmt_date(s) -> str:
    try:
        return date.fromisoformat(str(s)[:10]).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return "OFFEN"


def _measures_table(p: dict) -> str:
    ms = p.get("measures") or []
    if not ms:
        return "Noch keine Maßnahmen übernommen – OFFEN."
    rows = ["| Maßnahme | Art | Fläche | Rückhalt (abgeleitet) |", "|---|---|---|---|"]
    rp = (p.get("risk") or {}).get("rp_max")
    for m in ms:
        rows.append(f"| {m.get('name', '')} | {planning.KIND_DE.get(m.get('kind'), m.get('kind', ''))}"
                    f"{' (' + m['variant'] + ')' if m.get('kind') == 'gruendach' and m.get('variant') else ''} | "
                    f"{_num(m.get('area_m2'), 'm²')} | {_num(m.get('retained_m3'), 'm³', 1)} |")
    note = f"\n\nRückhalt beim {rp}-jährlichen Regen, berechnet mit GECAA (Screening, abgeleitet)." if rp else ""
    return "\n".join(rows) + note


def _risk_text(p: dict) -> str:
    r = p.get("risk") or {}
    if not r:
        return "Für dieses Projekt ist kein Starkregen-Ergebnis gespeichert – OFFEN."
    lines = [f"- Betrachtete Wiederkehrintervalle: {', '.join(str(x) + ' Jahre' for x in r.get('rps') or []) or 'OFFEN'}",
             f"- Gebäude über der Schwelle von {_num((r.get('threshold_m') or 0) * 100, 'cm')} beim seltensten Ereignis: "
             f"{_num(r.get('buildings_affected'))}",
             f"- Erwarteter Jahresschaden (Untergrenze/Größenordnung, abgeleitet): {_eur(r.get('eai'))} pro Jahr"]
    if r.get("eai_with_measures") is not None:
        lines.append(f"- … mit den Maßnahmen (abgeleitet): {_eur(r['eai_with_measures'])} pro Jahr")
    if r.get("persons") is not None:
        lines.append(f"- Bewohner in betroffenen Wohngebäuden (Zensus 2022, abgeleitet): ≈ {_num(r['persons'])}")
    lines.append(f"- Rechenkern: {r.get('engine') or 'OFFEN'}")
    return "\n".join(lines)


def _cost_table(p: dict) -> str:
    rows = [c for c in p.get("costs") or []]
    if not rows:
        return "Kosten OFFEN – bitte im Reiter Kosten aus Angeboten oder Kostenkennwerten eintragen."
    out = ["| Position | Maßnahme | Menge | Einheitspreis | Summe | Quelle |", "|---|---|---|---|---|---|"]
    for c in rows:
        s = float(c["qty"]) * float(c["unit_price"]) if c.get("qty") is not None and c.get("unit_price") is not None else None
        out.append(f"| {c.get('item', '')} | {c.get('measure') or ''} | {_num(c.get('qty'), c.get('unit') or '', 1)} | "
                   f"{_eur(c.get('unit_price'))} | {_eur(s)} | {c.get('source') or 'OFFEN'} |")
    out.append(f"\n**Summe (netto, Eingabe): {_eur(planning.costs_total(p))}**")
    return "\n".join(out)


def _funding_text(p: dict, short: bool = False) -> str:
    sel = set((p.get("funding") or {}).get("selected") or [])
    res = [r for r in funding.match(p) if r["id"] in sel] if sel else []
    if not res:
        return "Noch kein Förderprogramm ausgewählt – OFFEN (siehe Reiter Förderung)."
    out = []
    for r in res:
        out.append(f"**{r['name']}** ({r['funder']})\n\n- Förderquote: "
                   f"{'%d %%' % round(r['rate'] * 100) if r['rate'] is not None else 'OFFEN'}\n"
                   f"- Zuschuss: {_eur(r['grant_eur'])} ({r['grant_note']})\n- Frist: {r['window']}\n"
                   + ("" if short else "".join(f"- Offen: {q}\n" for q in r["open"]))
                   + f"- Quelle: {r['source_label']}, geprüft am {_fmt_date(r['checked'])}")
    total, grant = planning.costs_total(p), sum(r["grant_eur"] or 0 for r in res)
    if total is not None and any(r["grant_eur"] for r in res):
        out.append(f"Eigenanteil (abgeleitet, ohne Prüfung der Kumulierbarkeit): {_eur(max(total - grant, 0))}")
    return "\n\n".join(out)


def _schedule(p: dict, short: bool = False) -> str:
    rows = ["| Phase | Zeitraum | Verantwortlich | Stand |", "|---|---|---|---|"] if not short else []
    today = date.today()
    for ph in p.get("phases") or []:
        span = f"{_fmt_date(ph.get('start'))} – {_fmt_date(ph.get('end'))}"
        if short:
            if ph.get("start"):
                rows.append(f"- {ph['label']}: {span}")
        else:
            rows.append(f"| {ph['label']} | {span} | {ph.get('owner') or 'OFFEN'} | {planning.phase_status(ph, today)} |")
    return "\n".join(rows) or "Zeitplan OFFEN."


def _open_points(p: dict) -> str:
    items = planning.check(p, funding.programmes())
    if not items:
        return "Keine offenen Punkte."
    lab = {"fehler": "Konflikt", "warnung": "Hinweis", "offen": "OFFEN"}
    return "\n".join(f"- {lab[i['level']]}: {i['text']}" for i in items[:30])


def _tasks(p: dict, done: bool) -> str:
    rows = [f"- {t['text']}" + (f" ({t['measure']})" if t.get("measure") else "") +
            ("" if done else f" – fällig {_fmt_date(t.get('due'))}, {t.get('owner') or ph.get('owner') or 'Verantwortung OFFEN'}")
            for ph in p.get("phases") or [] for t in ph.get("tasks") or [] if bool(t.get("done")) == done]
    return "\n".join(rows[:25]) or ("Noch nichts erledigt." if done else "Keine offenen Aufgaben.")


def draft_section(key: str, p: dict) -> tuple[str, str]:
    site = p.get("site") or {}
    loc = site.get("address") or site.get("name")
    where = f"am Standort „{loc}“" if loc else "am Standort"
    kinds = ", ".join(sorted({planning.KIND_DE.get(m.get("kind"), m.get("kind")) for m in p.get("measures") or []})) or "OFFEN"
    pr = planning.progress(p)
    S = {
        "beschluss": ("Beschlussvorschlag", f"Der Rat beschließt die Umsetzung des Projekts „{p.get('name')}“ ({kinds}) {where} "
                      f"und beauftragt die Verwaltung, die Förderung zu beantragen. Haushaltsmittel: OFFEN."),
        "anlass": ("Anlass und Sachverhalt", f"{where[0].upper() + where[1:]} besteht bei Starkregen ein Überflutungsrisiko. Die Verwaltung hat mit GECAA "
                   "Gefährdung, mögliche Schäden und die Wirkung von Anpassungsmaßnahmen abgeschätzt (Screening, kein Gutachten)."),
        "ausgangslage": ("Ausgangslage", f"Projekt „{p.get('name')}“ {where}.\n\n" + _risk_text(p)),
        "risiko": ("Klimarisiko am Standort", _risk_text(p)),
        "ziele": ("Ziele", "- Überflutungsschäden an Gebäuden verringern\n- Regenwasser vor Ort zurückhalten\n"
                  "- Hitzebelastung im Umfeld mindern (qualitativ)\n- Messbare Zielwerte: OFFEN"),
        "massnahmen": ("Geplante Maßnahmen und Wirkung", _measures_table(p)),
        "massnahmen_kurz": ("Was ist geplant?", f"Geplant sind: {kinds}. Sie halten bei starkem Regen Wasser zurück, "
                            "damit weniger in Keller und Gebäude läuft."),
        "kosten": ("Kosten", _cost_table(p)),
        "foerderung": ("Finanzierung und Förderung", _funding_text(p)),
        "zeitplan": ("Zeitplan", _schedule(p)),
        "zeitplan_kurz": ("Wann passiert was?", _schedule(p, short=True)),
        "pflege": ("Pflege und Verstetigung", "Pflegeverantwortung, Pflegebudget und Wirkungskontrolle: OFFEN.\n\n"
                   "Bei Förderungen Zweckbindung und Verwendungsnachweis beachten (siehe Förderung)."),
        "offen": ("Offene Punkte und Risiken", _open_points(p)),
        "worum": ("Worum geht es?", f"Starker Regen kann {where} Straßen und Keller fluten. Die Stadt plant deshalb Maßnahmen, "
                  "die Regenwasser zurückhalten und die Umgebung kühlen."),
        "mitmachen": ("Wie kann ich mitmachen?", "Ansprechperson und Termine für Rückfragen: OFFEN.\n\n"
                      "Tipp: Auch auf dem eigenen Grundstück helfen Entsiegelung, Gründächer und Rückstausicherungen."),
        "stand": ("Stand des Projekts", f"Stand {date.today().strftime('%d.%m.%Y')}: {pr['done']} von {pr['tasks']} Aufgaben erledigt "
                  f"({round(pr['share'] * 100)} %).\n\n" + _schedule(p)),
        "erledigt": ("Erledigt", _tasks(p, True)),
        "naechste": ("Nächste Schritte", _tasks(p, False)),
        "quellen": ("Quellen und Methodik", "- Starkregen-Screening: GECAA (KOSTRA-DWD-2020, DGM1/ALKIS NRW, CLIMADA); "
                    "Schäden als Untergrenze/Größenordnung\n- Förderprogramme: siehe Quellenangaben im Abschnitt Förderung\n"
                    "- Alle Werte „abgeleitet“ sind Rechenergebnisse, „OFFEN“ markiert fehlende Angaben."),
    }
    return S[key]


def draft(p: dict, audience: str) -> list[dict]:
    keys = AUDIENCES[audience][1]
    return [{"key": k, "title": draft_section(k, p)[0], "md": draft_section(k, p)[1], "include": True, "auto": True}
            for k in keys]


# ---------------------------------------------------------------- Markdown subset -> blocks

_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*)")


def _runs(text: str) -> list[dict]:
    out = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append({"t": part[2:-2], "b": True})
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append({"t": part[1:-1], "i": True})
        else:
            out.append({"t": part})
    return out


def md_blocks(md: str) -> list[dict]:
    blocks, para, lst, table = [], [], [], []

    def flush():
        nonlocal para, lst, table
        if para:
            blocks.append({"k": "p", "runs": _runs(" ".join(para))})
        if lst:
            blocks.append({"k": "ul", "items": [_runs(x) for x in lst]})
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|?\s*:?-{2,}.*", r.replace(" ", ""))]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            n = max(len(r) for r in cells)
            blocks.append({"k": "table", "cols": n, "rows": [[_runs(c) for c in r + [""] * (n - len(r))] for r in cells]})
        para, lst, table = [], [], []

    for line in (md or "").splitlines():
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            blocks.append({"k": "h", "level": min(len(s) - len(s.lstrip("#")), 3), "runs": _runs(s.lstrip("#").strip())})
        elif s.startswith(("- ", "* ")):
            if para or table:
                flush()
            lst.append(s[2:])
        elif s.startswith("|"):
            if para or lst:
                flush()
            table.append(s)
        else:
            if lst or table:
                flush()
            para.append(s)
    flush()
    return blocks


def to_markdown(p: dict, title: str, sections: list[dict]) -> str:
    parts = [f"# {title}", f"*{p.get('name')}* · Stand {date.today().strftime('%d.%m.%Y')} · erstellt mit GECAA"]
    parts += [f"## {s['title']}\n\n{s['md']}" for s in sections if s.get("include", True)]
    return "\n\n".join(parts) + "\n"


def build_pdf(p: dict, title: str, sections: list[dict]) -> bytes:
    import typst
    data = {"title": title, "project": p.get("name") or "", "date": date.today().strftime("%d.%m.%Y"),
            "sections": [{"title": s["title"], "blocks": md_blocks(s["md"])} for s in sections if s.get("include", True)]}
    return typst.compile(str(TEMPLATE), root=str(TEMPLATE.parent), format="pdf",
                         sys_inputs={"data": json.dumps(data, ensure_ascii=False)})
