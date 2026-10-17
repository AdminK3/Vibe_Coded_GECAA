// SPDX-License-Identifier: Apache-2.0
// GECAA Klimarisiko-Bericht. Daten kommen als JSON über sys.inputs.data (siehe src/gecaa/report/typst_report.py).
#let d = json(bytes(sys.inputs.data))
#set document(title: "Klimarisiko-Bericht", author: "GECAA")
#set page(paper: "a4", margin: (x: 2cm, y: 2.2cm),
  footer: context [#set text(8pt, fill: gray); GECAA · Entscheidungshilfe, kein Gutachten #h(1fr) Seite #counter(page).display()])
#set text(lang: "de", size: 10pt)
#set heading(numbering: "1.")
#show heading.where(level: 1): set text(13pt)

#let kv(rows) = table(columns: (auto, 1fr), stroke: none, inset: (x: 0pt, y: 3pt),
  ..rows.map(r => (strong(r.at(0)), r.at(1))).flatten())
#let grid-table(head, rows) = table(columns: head.len(), stroke: 0.4pt + gray, inset: 4pt,
  fill: (_, y) => if y == 0 { luma(230) },
  ..head.map(h => strong(h)), ..rows.flatten().map(c => [#c]))
#let none-note(t) = text(fill: gray)[#emph(t)]

// simple native chart: pts = array of (x-label, value); kind = "line" | "bar"
#let chart(pts, kind: "line", w: 15cm, h: 5cm, ylabel: "", xlabel: "", top: none) = {
  if pts.len() == 0 { return none-note("Keine Werte.") }
  let vmax = calc.max(..pts.map(p => p.at(1)), 1e-9)
  let n = pts.len()
  let dx = w / n
  block(width: w + 1.2cm, height: h + 1.4cm, {
    place(dx: 1.2cm, line(start: (0pt, 0pt), end: (0pt, h), stroke: 0.5pt))
    place(dx: 1.2cm, dy: h, line(start: (0pt, 0pt), end: (w, 0pt), stroke: 0.5pt))
    place(dx: 0pt, dy: -0.1cm, text(7pt, if top == none { str(calc.round(vmax, digits: 1)) } else { top }))
    place(dx: 0pt, dy: h - 0.3cm, text(7pt, "0"))
    place(dx: 1.4cm, dy: -0.4cm, text(7pt, fill: gray, ylabel))
    for (i, p) in pts.enumerate() {
      let x = 1.2cm + dx * i + dx / 2
      let y = h - h * p.at(1) / vmax
      if kind == "bar" {
        place(dx: x - dx * 0.35, dy: y, rect(width: dx * 0.7, height: h - y, fill: rgb("#d9534f"), stroke: none))
      } else {
        place(dx: x - 2pt, dy: y - 2pt, circle(radius: 2pt, fill: rgb("#1f6fb2")))
        if i > 0 {
          let q = pts.at(i - 1)
          place(line(start: (x - dx, h - h * q.at(1) / vmax), end: (x, y), stroke: 1.2pt + rgb("#1f6fb2")))
        }
      }
      if kind == "line" or calc.rem(i, calc.max(1, int(n / 8))) == 0 {
        place(dx: x - 0.4cm, dy: h + 0.15cm, box(width: 0.8cm, align(center, text(7pt, p.at(0)))))
      }
    }
    place(dx: 1.2cm, dy: h + 0.7cm, text(7pt, fill: gray, xlabel))
  })
}

#align(center)[#text(18pt, weight: "bold")[Klimarisiko-Bericht] \ #text(11pt)[#d.site.title] \ #text(9pt, fill: gray)[Erstellt #d.created · GECAA]]
#v(0.4cm)
#rect(width: 100%, fill: rgb("#fff4e5"), stroke: 0.5pt + rgb("#e0a040"))[
  *Haftungshinweis:* Dieser Bericht ist eine Entscheidungshilfe, kein Gutachten. Die zuständige Stelle entscheidet.]

= Standort
#kv(d.site.rows)

= Zusammenfassung
#if d.summary.len() == 0 [#none-note("Keine Ergebnisse übergeben.")] else [#list(..d.summary)]

= Versickerung
#if d.check == none [#none-note("Keine Versickerungsprüfung übergeben.")] else [
  #kv(d.check.rows)
  #if d.check.findings.len() > 0 { grid-table(("Merkmal", "Ergebnis", "Begründung"), d.check.findings) }
]

= Starkregen
#if d.rain == none [#none-note("Keine Starkregenanalyse übergeben.")] else [
  #kv(d.rain.rows)
  == Risikokurve (Schaden je Wiederkehrintervall)
  #chart(d.rain.curve, top: d.rain.curve_top, ylabel: "Schaden EUR", xlabel: "Wiederkehrintervall (Jahre)")
  == Betroffene Gebäude
  #if d.rain.buildings.len() == 0 [#none-note("Keine Gebäude mit Wasser am Gebäude.")] else {
    grid-table(d.rain.building_head, d.rain.buildings)
  }
  #if d.rain.more > 0 [#text(8pt)[… und #d.rain.more weitere Gebäude.]]
]

= Hitze
#if d.heat == none [#none-note("Keine Hitzeanalyse übergeben.")] else [
  #kv(d.heat.rows)
  #if d.heat.hot_days.len() > 0 [
    == Heiße Tage je Jahr (Tmax ≥ 30 °C)
    #chart(d.heat.hot_days, kind: "bar", ylabel: "Tage", xlabel: "Jahr")
  ]
]

= Risikobewertung
#if d.cra.len() == 0 [#none-note("Keine Risikobewertung übergeben.")] else {
  grid-table(("Gefahr", "Schwere heute", "Schwere künftig", "Dringlichkeit", "Kapazität", "Priorität", "Grundlage"), d.cra)
}

= Annahmen und Quellen
#if d.assumptions.len() == 0 [#none-note("Keine Annahmen übergeben.")] else {
  grid-table(("ID", "Art", "Quelle", "Text"), d.assumptions)
}
#if d.sources.len() > 0 [*Quellen:* #list(..d.sources)]

= Haftungshinweis
Entscheidungshilfe, kein Gutachten. Die Ergebnisse beruhen auf frei verfügbaren Daten und vereinfachten Modellen
(siehe Annahmen). Der erwartete Jahresschaden ist eine Untergrenze/Größenordnung, keine Prognose. Für
Planungs- und Genehmigungsentscheidungen sind fachliche Prüfungen vor Ort erforderlich; die zuständige Stelle entscheidet.
