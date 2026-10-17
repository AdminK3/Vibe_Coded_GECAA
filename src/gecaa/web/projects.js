// SPDX-License-Identifier: Apache-2.0
// GECAA v5 project workspace: plan, tasks, costs, funding and a free-form report editor. All text is escaped.
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v == null ? "OFFEN" : `${Math.round(v).toLocaleString("de-DE")} €`);
const num = (v, u = "", d = 0) => (v == null ? "OFFEN" : `${Number(v).toLocaleString("de-DE", { maximumFractionDigits: d })}${u ? ` ${u}` : ""}`);
const dde = (s) => (s ? new Date(`${s}T00:00:00`).toLocaleDateString("de-DE") : "OFFEN");
const KIND = { mulde: "Versickerungsmulde", gruendach: "Gründach", entsiegelung: "Entsiegelung", baeume: "Bäume / Baumrigolen" };
const APPL = { kommune: "Kommune", oeffentlich: "Öffentliche Einrichtung", sozial: "Soziale Einrichtung", verband: "Verband/Verein", unternehmen: "Unternehmen", privat: "Privateigentümer" };
const LV = { fehler: ["Konflikt", "bad"], warnung: ["Hinweis", "warn"], offen: ["OFFEN", "open"] };
const uid = () => Math.random().toString(36).slice(2, 10);
let P = null, AUD = {}, saveTimer = null, tab = "overview", dirtyNote = null;

function toast(msg, err) {
  const t = $("#toast");
  t.textContent = msg; t.className = `toast${err ? " err" : ""}`; t.hidden = false;
  clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, err ? 7000 : 3000);
}
async function api(url, opt = {}) {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opt });
  if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).detail || m; } catch (e) { /* not json */ } throw Object.assign(new Error(typeof m === "string" ? m : JSON.stringify(m)), { status: r.status }); }
  return r.json();
}

// ------------------------------------------------------------ saving (debounced, optimistic locking)
function change(note) {
  dirtyNote = note || dirtyNote;
  $("#save-state").textContent = "Ungespeichert …";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 900);
}
async function saveNow() {
  clearTimeout(saveTimer);
  if (!P) return;
  const body = { version: P.version, note: dirtyNote, name: P.name, applicant: P.applicant, budget_weak: !!P.budget_weak, status: P.status,
    phases: P.phases, costs: P.costs, funding: P.funding, report: P.report };
  dirtyNote = null;
  try {
    const fresh = await api(`/v5/projects/${P.id}`, { method: "PUT", body: JSON.stringify(body) });
    const keepTab = tab;
    P = fresh;
    $("#save-state").textContent = `Gespeichert ${new Date().toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`;
    renderHead(); renderList();
    if (!["report", "costs", "tasks"].includes(keepTab)) render(keepTab);   // avoid stealing focus while typing
    else if (keepTab !== "report") renderChecksBadge();
  } catch (e) {
    $("#save-state").textContent = "Nicht gespeichert";
    toast(e.status === 409 ? "Jemand anderes hat das Projekt geändert – Seite wird neu geladen." : `Speichern fehlgeschlagen: ${e.message}`, true);
    if (e.status === 409) setTimeout(() => openProject(P.id), 1200);
  }
}

// ------------------------------------------------------------ list + head
async function renderList() {
  const items = await api("/v5/projects");
  $("#plist").innerHTML = items.length ? items.map((p) => `<a href="#${esc(p.id)}" class="pitem${P && P.id === p.id ? " on" : ""}">
    <strong>${esc(p.name)}</strong><small>${esc(APPL[p.applicant] || "Antragsteller OFFEN")} · ${esc(p.status || "idee")}</small>
    <span class="mini"><span style="width:${Math.round((p.share || 0) * 100)}%"></span></span></a>`).join("")
    : `<p class="note">Noch keine Projekte.</p>`;
}
function renderHead() {
  $("#p-name").value = P.name;
  $("#p-status").value = P.status || "idee";
  $("#p-prog").style.width = `${Math.round((P.progress.share || 0) * 100)}%`;
  $("#p-prog-t").textContent = `${P.progress.done}/${P.progress.tasks} Aufgaben`;
  renderChecksBadge();
}
function renderChecksBadge() {
  const n = (P.checks || []).filter((c) => c.level === "fehler").length;
  const b = $('[data-t="overview"]');
  b.innerHTML = `Übersicht${n ? ` <span class="cnt bad">${n}</span>` : ""}`;
}

// ------------------------------------------------------------ tabs
function render(t) {
  tab = t;
  $$(".tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.t === t)));
  $$(".tp").forEach((s) => { s.hidden = s.id !== `t-${t}`; });
  ({ overview, plan, tasks, costs, funding, report, log })[t]();
}

function overview() {
  const r = P.risk || {};
  const kpi = (lab, val, note) => `<div class="kpi"><span>${esc(lab)}</span><strong>${esc(val)}</strong>${note ? `<small>${esc(note)}</small>` : ""}</div>`;
  const next = P.phases.flatMap((ph) => ph.tasks.filter((x) => !x.done).map((x) => ({ ...x, ph: ph.label }))).slice(0, 6);
  $("#t-overview").innerHTML = `
    <div class="kpis">
      ${kpi("Erwarteter Jahresschaden", eur(r.eai), "Untergrenze/Größenordnung, abgeleitet")}
      ${kpi("… mit Maßnahmen", eur(r.eai_with_measures), "abgeleitet")}
      ${kpi("Betroffene Gebäude", r.buildings_affected == null ? "OFFEN" : `${r.buildings_affected} von ${r.buildings_total}`, r.rp_max ? `${r.rp_max}-jährlicher Regen` : "")}
      ${kpi("Kosten (Eingabe)", eur(P.costs_total), "netto")}
    </div>
    ${r.run_id ? `<p><a class="btn secondary sm" href="/zwilling?run=${encodeURIComponent(r.run_id)}&project=${encodeURIComponent(P.id)}" target="_blank" rel="noopener">Im digitalen Zwilling ansehen (mit Zeitachse)</a></p>` : `<p class="note">Kein Starkregen-Ergebnis verknüpft – Risikowerte OFFEN.</p>`}
    <div class="cols">
      <div class="card"><h3>Prüfung des Plans</h3>${checksHtml(P.checks)}</div>
      <div class="card"><h3>Nächste Aufgaben</h3>${next.length ? `<ul class="clean">${next.map((x) => `<li>${esc(x.text)}<small>${esc(x.ph)} · fällig ${esc(dde(x.due))}</small></li>`).join("")}</ul>` : `<p class="note">Alles erledigt.</p>`}</div>
    </div>
    <div class="card"><h3>Maßnahmen</h3><table class="data"><tr><th>Maßnahme</th><th>Art</th><th>Fläche</th><th>Rückhalt (abgeleitet)</th><th>Kosten (Eingabe)</th><th>€ je m³ Rückhalt</th><th>Hitze</th></tr>
      ${P.measures.map((m) => `<tr><td>${esc(m.name)}</td><td>${esc(KIND[m.kind] || m.kind)}${m.kind === "gruendach" && m.variant ? ` (${esc(m.variant)})` : ""}</td>
        <td>${esc(num(m.area_m2, "m²"))}</td><td>${esc(num(m.retained_m3, "m³", 1))}</td>${(() => {
          const rows = P.costs.filter((c) => c.measure === m.name && c.qty != null && c.unit_price != null);
          const k = rows.length ? rows.reduce((x, c) => x + c.qty * c.unit_price, 0) : null;
          return `<td>${esc(eur(k))}</td><td>${k != null && m.retained_m3 ? `${esc(eur(k / m.retained_m3))} <small>abgeleitet</small>` : "OFFEN"}</td>`;
        })()}<td><small>${esc(m.heat_note || "")}</small></td></tr>`).join("") || `<tr><td colspan="7">Keine Maßnahmen.</td></tr>`}</table></div>`;
}
function checksHtml(list) {
  if (!list || !list.length) return `<p class="ok">Keine Konflikte oder offenen Punkte.</p>`;
  const order = { fehler: 0, warnung: 1, offen: 2 };
  return `<ul class="checks">${[...list].sort((a, b) => order[a.level] - order[b.level]).slice(0, 14)
    .map((c) => `<li class="${LV[c.level][1]}"><b>${LV[c.level][0]}</b> ${esc(c.text)}</li>`).join("")}</ul>
    ${list.length > 14 ? `<p class="note">… und ${list.length - 14} weitere.</p>` : ""}`;
}

// ------------------------------------------------------------ schedule (Gantt)
function gantt() {
  const ds = P.phases.flatMap((p) => [p.start, p.end]).filter(Boolean).map((s) => new Date(`${s}T00:00:00`));
  if (!ds.length) return `<p class="note">Tragen Sie Beginn und Ende der Phasen ein – dann erscheint hier der Balkenplan.</p>`;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  let a = new Date(Math.min(...ds, today)), b = new Date(Math.max(...ds, today));
  a = new Date(a.getFullYear(), a.getMonth(), 1); b = new Date(b.getFullYear(), b.getMonth() + 1, 1);
  const W = 760, L = 190, rowH = 28, H = P.phases.length * rowH + 30, span = b - a || 1;
  const x = (d) => L + ((d - a) / span) * (W - L - 10);
  let months = "";
  for (let d = new Date(a); d < b; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
    months += `<line x1="${x(d)}" x2="${x(d)}" y1="18" y2="${H}" class="gl"/>`;
    if ((b - a) / 2.6e9 < 30 || d.getMonth() % 3 === 0) months += `<text x="${x(d) + 3}" y="12" class="gt">${d.toLocaleDateString("de-DE", { month: "short", year: "2-digit" })}</text>`;
  }
  const bars = P.phases.map((p, i) => {
    const y = 24 + i * rowH, st = P.phase_status[p.key];
    const lab = `<text x="4" y="${y + 14}" class="gl-lab">${esc(p.label)}</text>`;
    if (!p.start || !p.end) return `${lab}<text x="${L}" y="${y + 14}" class="gt">Zeitraum OFFEN</text>`;
    const s = new Date(`${p.start}T00:00:00`), e = new Date(`${p.end}T00:00:00`);
    return `${lab}<rect x="${x(s)}" y="${y + 3}" width="${Math.max(3, x(e) - x(s))}" height="16" rx="4" class="bar ${esc(st)}"><title>${esc(p.label)}: ${dde(p.start)} – ${dde(p.end)} (${esc(st)})</title></rect>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="gantt" role="img" aria-label="Balkenplan">${months}${bars}
    <line x1="${x(today)}" x2="${x(today)}" y1="16" y2="${H}" class="today"/><text x="${x(today) + 3}" y="${H - 2}" class="gt today-t">heute</text></svg>`;
}
function plan() {
  $("#t-plan").innerHTML = `<div class="card">${gantt()}</div>
    <div class="card"><table class="data edit"><tr><th>Phase</th><th>Beginn</th><th>Ende</th><th>Verantwortlich</th><th>Stand</th></tr>
    ${P.phases.map((p, i) => `<tr><td>${esc(p.label)}</td><td><input type="date" data-i="${i}" data-f="start" value="${esc(p.start || "")}"></td>
      <td><input type="date" data-i="${i}" data-f="end" value="${esc(p.end || "")}"></td>
      <td><input type="text" data-i="${i}" data-f="owner" maxlength="120" placeholder="Amt / Person" value="${esc(p.owner || "")}"></td>
      <td><span class="st ${esc(P.phase_status[p.key])}">${esc(P.phase_status[p.key])}</span></td></tr>`).join("")}</table>
    <div class="row"><button class="btn secondary sm" id="shift-btn" type="button">Plan verschieben …</button>
      <a class="btn secondary sm" href="/v5/projects/${esc(P.id)}/plan.ics">Kalender (ICS)</a>
      <a class="btn secondary sm" href="/v5/projects/${esc(P.id)}/tasks.csv">Aufgabenliste (CSV)</a></div>
    <p class="note">Dauern schlägt GECAA bewusst nicht vor – sie hängen von Verwaltung, Förderverfahren und Vergabe ab.</p></div>
    <div class="card"><h3>Prüfung</h3>${checksHtml(P.checks.filter((c) => c.level !== "offen"))}</div>`;
  $$("#t-plan [data-f]").forEach((el) => el.addEventListener("change", () => {
    P.phases[+el.dataset.i][el.dataset.f] = el.value || null; change("Zeitplan geändert");
  }));
  $("#shift-btn").onclick = () => {
    const d = Number(prompt("Alle Termine um wie viele Tage verschieben? (negativ = früher)", "14"));
    if (!d) return;
    const sh = (s) => { if (!s) return s; const x = new Date(`${s}T00:00:00`); x.setDate(x.getDate() + d); return x.toISOString().slice(0, 10); };
    P.phases.forEach((p) => { p.start = sh(p.start); p.end = sh(p.end); p.tasks.forEach((t) => { t.due = sh(t.due); }); });
    change(`Plan um ${d} Tage verschoben`); plan();
  };
}

// ------------------------------------------------------------ tasks
function tasks() {
  const flt = $("#t-tasks").dataset.f || "alle";
  const today = new Date().toISOString().slice(0, 10);
  const show = (t) => flt === "alle" || (flt === "offen" && !t.done) || (flt === "faellig" && !t.done && t.due && t.due <= today);
  $("#t-tasks").innerHTML = `<div class="row wrap filters">${["alle", "offen", "faellig"].map((f) => `<button class="chip${f === flt ? " on" : ""}" data-flt="${f}" type="button">${{ alle: "Alle", offen: "Offen", faellig: "Fällig/überfällig" }[f]}</button>`).join("")}</div>
    ${P.phases.map((ph, i) => `<div class="card phase"><h3>${esc(ph.label)} <small>${ph.tasks.filter((t) => t.done).length}/${ph.tasks.length}</small></h3>
      <ul class="tasks">${ph.tasks.map((t, j) => show(t) ? `<li class="${t.done ? "done" : ""}${!t.done && t.due && t.due < today ? " late" : ""}">
        <input type="checkbox" data-i="${i}" data-j="${j}" data-f="done" ${t.done ? "checked" : ""} aria-label="erledigt">
        <input type="text" class="tt" data-i="${i}" data-j="${j}" data-f="text" maxlength="300" value="${esc(t.text)}">
        ${t.measure ? `<span class="tag">${esc(t.measure)}</span>` : ""}
        <input type="text" class="to" data-i="${i}" data-j="${j}" data-f="owner" maxlength="120" placeholder="wer?" value="${esc(t.owner || "")}">
        <input type="date" data-i="${i}" data-j="${j}" data-f="due" value="${esc(t.due || "")}">
        <button class="icon-btn" type="button" data-del="${i}:${j}" aria-label="Aufgabe löschen">×</button></li>` : "").join("")}</ul>
      <button class="btn link sm" type="button" data-add="${i}">+ Aufgabe</button></div>`).join("")}`;
  $$("#t-tasks [data-flt]").forEach((b) => b.addEventListener("click", () => { $("#t-tasks").dataset.f = b.dataset.flt; tasks(); }));
  $$("#t-tasks [data-f]").forEach((el) => el.addEventListener("change", () => {
    const t = P.phases[+el.dataset.i].tasks[+el.dataset.j];
    t[el.dataset.f] = el.type === "checkbox" ? el.checked : (el.value || null);
    if (el.type === "checkbox") { el.closest("li").classList.toggle("done", el.checked); change(`${el.checked ? "Erledigt" : "Wieder offen"}: ${t.text}`); } else change();
  }));
  $$("#t-tasks [data-del]").forEach((b) => b.addEventListener("click", () => {
    const [i, j] = b.dataset.del.split(":").map(Number); const [t] = P.phases[i].tasks.splice(j, 1); change(`Aufgabe gelöscht: ${t.text}`); tasks();
  }));
  $$("#t-tasks [data-add]").forEach((b) => b.addEventListener("click", () => {
    P.phases[+b.dataset.add].tasks.push({ id: uid(), text: "Neue Aufgabe", done: false, due: null, owner: null, measure: null });
    change("Aufgabe ergänzt"); tasks(); const ins = $$(`#t-tasks [data-i="${b.dataset.add}"].tt`); ins[ins.length - 1].select();
  }));
}

// ------------------------------------------------------------ costs
function costs() {
  const tot = () => P.costs.reduce((a, c) => a + (c.qty != null && c.unit_price != null ? c.qty * c.unit_price : 0), 0);
  const hint = P.measures.map((m) => `${esc(m.name)}: ${esc(num(m.area_m2, "m²"))}${m.trees ? `, ${m.trees} Bäume` : ""}`).join(" · ");
  $("#t-costs").innerHTML = `<div class="card"><p class="note">Tragen Sie Kosten aus Angeboten, Kostenschätzungen oder Kennwerten ein und nennen Sie die Quelle. GECAA setzt keine Preise ein.<br>Mengen aus der Karte: ${hint || "–"}</p>
    <table class="data edit"><tr><th>Position</th><th>Maßnahme</th><th>Menge</th><th>Einheit</th><th>Einheitspreis €</th><th>Summe</th><th>Quelle</th><th></th></tr>
    ${P.costs.map((c, i) => `<tr><td><input type="text" data-i="${i}" data-f="item" maxlength="200" value="${esc(c.item)}"></td>
      <td><select data-i="${i}" data-f="measure"><option value="">–</option>${P.measures.map((m) => `<option ${c.measure === m.name ? "selected" : ""}>${esc(m.name)}</option>`).join("")}<option ${c.measure === "Planung/Nebenkosten" ? "selected" : ""}>Planung/Nebenkosten</option></select></td>
      <td><input type="number" min="0" step="any" data-i="${i}" data-f="qty" value="${c.qty ?? ""}"></td>
      <td><input type="text" data-i="${i}" data-f="unit" maxlength="20" value="${esc(c.unit || "")}" placeholder="m²"></td>
      <td><input type="number" min="0" step="any" data-i="${i}" data-f="unit_price" value="${c.unit_price ?? ""}"></td>
      <td class="sum" data-sum="${i}">${eur(c.qty != null && c.unit_price != null ? c.qty * c.unit_price : null)}</td>
      <td><input type="text" data-i="${i}" data-f="source" maxlength="300" value="${esc(c.source || "")}" placeholder="Angebot / Kennwert"></td>
      <td><button class="icon-btn" data-del="${i}" type="button" aria-label="Zeile löschen">×</button></td></tr>`).join("")}
    <tr class="total"><td colspan="5">Summe netto</td><td id="c-tot">${eur(P.costs.length ? tot() : null)}</td><td colspan="2"></td></tr></table>
    <button class="btn secondary sm" type="button" id="c-add">+ Position</button></div>`;
  $$("#t-costs [data-f]").forEach((el) => el.addEventListener("input", () => {
    const c = P.costs[+el.dataset.i];
    c[el.dataset.f] = el.type === "number" ? (el.value === "" ? null : Number(el.value)) : (el.value || null);
    $(`[data-sum="${el.dataset.i}"]`).textContent = eur(c.qty != null && c.unit_price != null ? c.qty * c.unit_price : null);
    $("#c-tot").textContent = eur(tot()); change("Kosten geändert");
  }));
  $$("#t-costs [data-del]").forEach((b) => b.addEventListener("click", () => { P.costs.splice(+b.dataset.del, 1); change("Kostenposition gelöscht"); costs(); }));
  $("#c-add").onclick = () => { P.costs.push({ id: uid(), item: "Neue Position", measure: null, qty: null, unit: null, unit_price: null, source: null }); change(); costs(); };
}

// ------------------------------------------------------------ funding
async function funding() {
  const box = $("#t-funding");
  box.innerHTML = `<div class="card row wrap">
    <label class="inline">Antragsteller <select id="f-app"><option value="">OFFEN</option>${Object.entries(APPL).map(([k, v]) => `<option value="${k}" ${P.applicant === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <label class="inline"><input type="checkbox" id="f-weak" ${P.budget_weak ? "checked" : ""}> Haushaltsnotlage nachgewiesen (finanzschwache Kommune)</label></div>
    <div id="f-list"><p class="note">Lade Programme …</p></div>`;
  const reload = async () => { await saveNow(); funding(); };
  $("#f-app").onchange = (e) => { P.applicant = e.target.value || null; dirtyNote = "Antragsteller geändert"; reload(); };
  $("#f-weak").onchange = (e) => { P.budget_weak = e.target.checked; reload(); };
  const res = await api(`/v5/projects/${P.id}/funding`);
  const sel = new Set(P.funding.selected || []);
  const cls = { passt: "good", "prüfen": "warn", "passt nicht": "bad" };
  $("#f-list").innerHTML = res.map((r) => `<article class="card fund ${cls[r.fit]}">
    <header><label><input type="checkbox" data-sel="${esc(r.id)}" ${sel.has(r.id) ? "checked" : ""} ${r.fit === "passt nicht" ? "disabled" : ""}> <strong>${esc(r.name)}</strong></label>
      <span class="pill ${cls[r.fit]}">${esc(r.fit)}</span></header>
    <p class="note">${esc(r.funder)} · ${esc(r.level)} · <span class="win ${esc(r.window_state)}">${esc(r.window)}</span></p>
    <div class="fgrid"><div><span>Förderquote</span><strong>${r.rate == null ? "OFFEN" : `${Math.round(r.rate * 100)} %`}</strong><small>${esc(r.rate_note || "")}</small></div>
      <div><span>Zuschuss</span><strong>${esc(eur(r.grant_eur))}</strong><small>${esc(r.grant_note)}</small></div></div>
    ${r.blockers.length ? `<ul class="checks">${r.blockers.map((x) => `<li class="bad"><b>passt nicht</b> ${esc(x)}</li>`).join("")}</ul>` : ""}
    ${r.reasons.length ? `<ul class="clean ok">${r.reasons.map((x) => `<li>✓ ${esc(x)}</li>`).join("")}</ul>` : ""}
    ${r.open.length ? `<ul class="checks">${r.open.map((x) => `<li class="open"><b>prüfen</b> ${esc(x)}</li>`).join("")}</ul>` : ""}
    <details><summary>Bedingungen</summary><ul class="clean">${[r.window_note, ...r.notes].filter(Boolean).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>
    <p class="src">Quelle: <a href="${esc(r.source)}" target="_blank" rel="noopener noreferrer">${esc(r.source_label)}</a> · geprüft am ${esc(dde(r.checked))}</p></article>`).join("")
    + `<p class="note">Angaben aus den verlinkten Quellen; nicht genannte Werte sind OFFEN. Kumulierbarkeit und Förderfähigkeit einzelner Kosten klärt der Fördergeber.</p>`;
  $$("#f-list [data-sel]").forEach((c) => c.addEventListener("change", () => {
    const s = new Set(P.funding.selected || []);
    c.checked ? s.add(c.dataset.sel) : s.delete(c.dataset.sel);
    P.funding = { ...P.funding, selected: [...s] }; change(`Förderauswahl geändert`);
  }));
}

// ------------------------------------------------------------ report editor
function mdHtml(md) {   // small Markdown subset, escaped first
  const inl = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\b(OFFEN)\b/g, '<mark class="open">$1</mark>').replace(/\b(abgeleitet)\b/g, '<mark class="der">$1</mark>');
  const out = []; let list = [], tbl = [];
  const flush = () => {
    if (list.length) out.push(`<ul>${list.map((x) => `<li>${inl(x)}</li>`).join("")}</ul>`);
    if (tbl.length) {
      const rows = tbl.filter((r) => !/^\|?\s*:?-{2,}/.test(r.replace(/\s/g, ""))).map((r) => r.trim().replace(/^\||\|$/g, "").split("|"));
      out.push(`<table class="data">${rows.map((r, i) => `<tr>${r.map((c) => `<${i ? "td" : "th"}>${inl(c.trim())}</${i ? "td" : "th"}>`).join("")}</tr>`).join("")}</table>`);
    }
    list = []; tbl = [];
  };
  for (const raw of (md || "").split("\n")) {
    const s = raw.trim();
    if (!s) { flush(); continue; }
    if (s.startsWith("#")) { flush(); out.push(`<h4>${inl(s.replace(/^#+/, "").trim())}</h4>`); } else if (/^[-*] /.test(s)) { if (tbl.length) flush(); list.push(s.slice(2)); } else if (s.startsWith("|")) { if (list.length) flush(); tbl.push(s); } else { flush(); out.push(`<p>${inl(s)}</p>`); }
  }
  flush();
  return out.join("");
}
async function report() {
  const box = $("#t-report");
  if (!Object.keys(AUD).length) AUD = await api("/v5/report/audiences");
  const R = P.report || (P.report = { audience: "rat", sections: [] });
  box.innerHTML = `<div class="card row wrap rep-bar">
      <label class="inline">Berichtsart <select id="r-aud">${Object.entries(AUD).map(([k, v]) => `<option value="${k}" ${R.audience === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
      <button class="btn secondary sm" id="r-gen" type="button">${R.sections.length ? "Neu aus Projektdaten erzeugen" : "Entwurf erzeugen"}</button>
      <span class="grow"></span>
      <button class="btn secondary sm" id="r-add" type="button">+ Eigener Abschnitt</button>
      <button class="btn secondary sm" id="r-md" type="button">Markdown</button>
      <button class="btn primary sm" id="r-pdf" type="button">PDF</button></div>
    ${R.sections.length ? "" : `<p class="note">Wählen Sie eine Berichtsart und erzeugen Sie einen Entwurf. Alle Abschnitte sind danach frei bearbeitbar: umschreiben, verschieben, ausblenden, ergänzen.</p>`}
    <div id="r-secs">${R.sections.map((s, i) => `<article class="card sec${s.include ? "" : " off"}" draggable="true" data-i="${i}">
      <header><span class="drag" title="Ziehen zum Verschieben" aria-hidden="true">⋮⋮</span>
        <input class="sec-name" data-i="${i}" data-f="title" maxlength="200" value="${esc(s.title)}" aria-label="Abschnittstitel">
        <label class="inline sm"><input type="checkbox" data-i="${i}" data-f="include" ${s.include ? "checked" : ""}> im Bericht</label>
        <button class="icon-btn" data-mv="${i}:-1" type="button" aria-label="nach oben">↑</button><button class="icon-btn" data-mv="${i}:1" type="button" aria-label="nach unten">↓</button>
        ${s.auto ? `<button class="icon-btn" data-regen="${i}" type="button" title="Abschnitt neu aus Projektdaten" aria-label="Abschnitt neu erzeugen">↻</button>` : ""}
        <button class="icon-btn" data-del="${i}" type="button" aria-label="Abschnitt löschen">×</button></header>
      <div class="sec-body"><textarea data-i="${i}" data-f="md" rows="${Math.min(18, Math.max(4, s.md.split("\n").length + 1))}" maxlength="30000" aria-label="Text (Markdown)">${esc(s.md)}</textarea>
        <div class="preview" data-pv="${i}">${mdHtml(s.md)}</div></div></article>`).join("")}</div>
    <p class="note">Formatierung: <code># Überschrift</code>, <code>- Aufzählung</code>, <code>**fett**</code>, <code>*kursiv*</code>, Tabellen mit <code>| a | b |</code>. Markierungen: <mark class="open">OFFEN</mark> fehlt noch, <mark class="der">abgeleitet</mark> berechnet.</p>`;
  const S = R.sections;
  $("#r-aud").onchange = (e) => { R.audience = e.target.value; change("Berichtsart geändert"); };
  $("#r-gen").onclick = async () => {
    if (S.length && !confirm("Alle Abschnitte durch einen neuen Entwurf ersetzen? Eigene Änderungen gehen verloren.")) return;
    await saveNow();
    const d = await api(`/v5/projects/${P.id}/report/draft?audience=${encodeURIComponent(R.audience)}`);
    P.report = { audience: R.audience, sections: d.sections }; change("Berichtsentwurf erzeugt"); report();
  };
  $("#r-add").onclick = () => { S.push({ key: `eigen-${uid()}`, title: "Neuer Abschnitt", md: "", include: true, auto: false }); change("Abschnitt ergänzt"); report(); };
  const dl = async (ext) => {
    await saveNow();
    const a = document.createElement("a"); a.href = `/v5/projects/${P.id}/report.${ext}`; a.click();
  };
  $("#r-pdf").onclick = () => dl("pdf");
  $("#r-md").onclick = () => dl("md");
  $$("#r-secs [data-f]").forEach((el) => el.addEventListener("input", () => {
    const i = +el.dataset.i, f = el.dataset.f;
    S[i][f] = el.type === "checkbox" ? el.checked : el.value;
    if (f === "md") { $(`[data-pv="${i}"]`).innerHTML = mdHtml(el.value); S[i].auto = S[i].auto && false; }
    if (f === "include") el.closest(".sec").classList.toggle("off", !el.checked);
    change("Bericht bearbeitet");
  }));
  $$("#r-secs [data-mv]").forEach((b) => b.addEventListener("click", () => {
    const [i, d] = b.dataset.mv.split(":").map(Number), j = i + d;
    if (j < 0 || j >= S.length) return;
    [S[i], S[j]] = [S[j], S[i]]; change("Abschnitte umsortiert"); report();
  }));
  $$("#r-secs [data-del]").forEach((b) => b.addEventListener("click", () => {
    if (!confirm(`Abschnitt „${S[+b.dataset.del].title}“ löschen?`)) return;
    S.splice(+b.dataset.del, 1); change("Abschnitt gelöscht"); report();
  }));
  $$("#r-secs [data-regen]").forEach((b) => b.addEventListener("click", async () => {
    const s = S[+b.dataset.regen]; await saveNow();
    const n = await api(`/v5/projects/${P.id}/report/draft?audience=${encodeURIComponent(R.audience)}&section=${encodeURIComponent(s.key)}`);
    s.md = n.md; change(`Abschnitt „${s.title}“ aktualisiert`); report();
  }));
  let from = null;   // drag & drop reorder
  $$("#r-secs .sec").forEach((el) => {
    el.addEventListener("dragstart", (e) => { if (e.target.closest("textarea,input")) { e.preventDefault(); return; } from = +el.dataset.i; el.classList.add("dragging"); });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
    el.addEventListener("dragover", (e) => e.preventDefault());
    el.addEventListener("drop", (e) => {
      e.preventDefault(); const to = +el.dataset.i;
      if (from == null || from === to) return;
      const [m] = S.splice(from, 1); S.splice(to, 0, m); from = null; change("Abschnitte umsortiert"); report();
    });
  });
}

function log() {
  $("#t-log").innerHTML = `<div class="card"><ul class="log">${[...(P.log || [])].reverse().map((l) => `<li><time>${esc(new Date(l.at).toLocaleString("de-DE"))}</time> ${esc(l.text)}</li>`).join("")}</ul></div>`;
}

// ------------------------------------------------------------ boot
async function openProject(id) {
  try {
    P = await api(`/v5/projects/${encodeURIComponent(id)}`);
  } catch (e) { toast(`Projekt nicht gefunden (${e.message})`, true); $("#proj").hidden = true; $("#empty").hidden = false; return; }
  P.funding = P.funding || { selected: [] };
  $("#empty").hidden = true; $("#proj").hidden = false;
  document.title = `GECAA · ${P.name}`;
  renderHead(); renderList(); render(tab);
}
function boot() {
  try { const t = JSON.parse(localStorage.getItem("gecaa.v3") || "{}").theme; if (t) document.documentElement.dataset.theme = t; } catch (e) { /* storage blocked */ }
  $("#theme-btn").onclick = () => {
    const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { const s = JSON.parse(localStorage.getItem("gecaa.v3") || "{}"); s.theme = document.documentElement.dataset.theme; localStorage.setItem("gecaa.v3", JSON.stringify(s)); } catch (e) { /* storage blocked */ }
  };
  $$(".tabs [role=tab]").forEach((b) => b.addEventListener("click", () => render(b.dataset.t)));
  $("#p-name").addEventListener("input", (e) => { P.name = e.target.value || "Projekt"; change("Projekt umbenannt"); });
  $("#p-status").addEventListener("change", (e) => { P.status = e.target.value; change(`Status: ${e.target.selectedOptions[0].text}`); });
  $("#p-del").onclick = async () => {
    if (!confirm(`Projekt „${P.name}“ endgültig löschen?`)) return;
    await api(`/v5/projects/${P.id}`, { method: "DELETE" }); P = null; location.hash = ""; $("#proj").hidden = true; $("#empty").hidden = false; renderList();
  };
  window.addEventListener("hashchange", () => location.hash.length > 1 && openProject(location.hash.slice(1)));
  window.addEventListener("beforeunload", (e) => { if (saveTimer && $("#save-state").textContent.startsWith("Unge")) { saveNow(); e.preventDefault(); } });
  renderList();
  if (location.hash.length > 1) openProject(location.hash.slice(1));
}
boot();
