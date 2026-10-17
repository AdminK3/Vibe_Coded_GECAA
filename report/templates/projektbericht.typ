// SPDX-License-Identifier: Apache-2.0
// GECAA Projektbericht. Inhalte kommen als JSON-Blöcke (sys.inputs.data, siehe src/gecaa/projects/report.py);
// Text wird nur als Zeichenkette gesetzt, nie als Typst-Markup ausgewertet.
#let d = json(bytes(sys.inputs.data))
#set document(title: d.title, author: "GECAA")
#set page(paper: "a4", margin: (x: 2.2cm, y: 2.4cm),
  header: context { if counter(page).get().first() > 1 [#set text(8pt, fill: gray); #d.project #h(1fr) #d.title] },
  footer: context [#set text(8pt, fill: gray); GECAA · Entscheidungshilfe, kein Gutachten #h(1fr) Seite #counter(page).display() von #counter(page).final().first()])
#set text(lang: "de", size: 10.5pt)
#set par(justify: true, leading: 0.62em)
#set heading(numbering: "1.")
#show heading.where(level: 1): it => block(above: 1.4em, below: 0.7em, text(14pt, fill: rgb("#0b5d4b"), it))

#let runs(rs) = rs.map(r => {
  let t = r.t
  if r.at("b", default: false) { strong(t) } else if r.at("i", default: false) { emph(t) } else { t }
}).join()

#let block-of(b) = {
  if b.k == "p" { par(runs(b.runs)) }
  else if b.k == "h" { text(weight: "bold", size: if b.level == 1 { 12pt } else { 11pt }, runs(b.runs)) }
  else if b.k == "ul" { list(..b.items.map(i => runs(i))) }
  else if b.k == "table" {
    table(columns: b.cols, stroke: 0.4pt + luma(170), inset: 4pt, fill: (_, y) => if y == 0 { luma(232) },
      ..b.rows.enumerate().map(((y, row)) => row.map(c => if y == 0 { strong(runs(c)) } else { text(9pt, runs(c)) })).flatten())
  }
}

#block(fill: rgb("#0b5d4b"), inset: 14pt, radius: 4pt, width: 100%)[
  #set text(fill: white)
  #text(9pt)[GECAA · Klimaanpassung] \
  #text(18pt, weight: "bold", d.title) \
  #text(11pt, d.project) #h(1fr) #text(9pt, "Stand " + d.date)
]
#v(0.6em)
#if d.sections.len() > 2 { outline(title: "Inhalt", depth: 1) }

#for s in d.sections {
  heading(level: 1, s.title)
  for b in s.blocks { block-of(b) }
}

#v(1fr)
#text(8pt, fill: gray)[„abgeleitet“ = Rechenergebnis aus den Projektdaten; „OFFEN“ = Angabe fehlt. Schadenswerte sind Untergrenze/Größenordnung. Förderangaben vor Antragstellung beim Fördergeber prüfen.]
