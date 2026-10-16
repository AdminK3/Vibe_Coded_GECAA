// SPDX-License-Identifier: Apache-2.0
// GECAA v3: heavy-rain risk (CLIMADA), heat, CLIMAAX risk assessment, theme, export. Uses `st`, `api`, `esc`
// and `switchTab` from v2.js (same page).
"use strict";

const R = {
  rain: null, heat: null, cra: null, scales: null,
  view: { rp: null, sc: "heute", measure: false, depth: true, flow: false, relief: true, opacity: 0.85 },
  ov: {}, bLayer: null, boxLayer: null, health: null,
};
const fmtEUR = (v) => (v == null ? "–" : `${Math.round(v).toLocaleString("de-DE")} €`);
const fmtEURk = (v) => (v == null ? "–" : v >= 10000 ? `${Math.round(v / 1000).toLocaleString("de-DE")} Tsd. €` : fmtEUR(v));
const fmtM = (v) => (v == null ? "–" : `${v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} m`);
const fmt1 = (v) => (v == null ? "–" : v.toLocaleString("de-DE", { maximumFractionDigits: 1 }));
const DEPTH_COL = (d) => d >= 1 ? "#0e3480" : d >= 0.5 ? "#1a5eb0" : d >= 0.3 ? "#4292d6" : d >= 0.1 ? "#8cc8ee" : d >= 0.05 ? "#c6e6f7" : null;
const SC_COL = { "heute": "#1a5eb0", "+1 K": "#d0812e", "+2 K": "#b0262b" };
const LS = "gecaa.v3";

function toast(msg, err) {
  const t = document.createElement("div");
  t.className = `toast${err ? " err" : ""}`;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), err ? 7000 : 3500);
}
function save(patch) {
  try { localStorage.setItem(LS, JSON.stringify({ ...load(), ...patch })); } catch (e) { /* storage blocked */ }
}
function load() {
  try { return JSON.parse(localStorage.getItem(LS) || "{}"); } catch (e) { return {}; }
}

// ------------------------------------------------------------------ site centre

function siteCenter() {
  if (st.parcel) {
    const b = L.geoJSON(st.parcel).getBounds().getCenter();
    return { lat: b.lat, lon: b.lng, from: "Flurstück" };
  }
  if (st.units.length) {
    const b = L.featureGroup(st.units.map((u) => u.layer)).getBounds().getCenter();
    return { lat: b.lat, lon: b.lng, from: "Anlagenstandorte" };
  }
  const c = st.map.getCenter();
  return { lat: c.lat, lon: c.lng, from: "Kartenmitte" };
}

function boxBounds(c, m) {
  const dLat = m / 2 / 111320, dLon = dLat / Math.cos((c.lat * Math.PI) / 180);
  return [[c.lat - dLat, c.lon - dLon], [c.lat + dLat, c.lon + dLon]];
}

function showBox() {
  if (R.boxLayer) st.map.removeLayer(R.boxLayer);
  const c = siteCenter();
  R.boxLayer = L.rectangle(boxBounds(c, Number($("#rain-box").value)), {
    color: "#0a5c8f", weight: 2, fill: false, dashArray: "6 5", interactive: false, className: "box-outline" }).addTo(st.map);
  $("#rain-units-count").textContent = st.units.length ? `(${st.units.length} Standort${st.units.length > 1 ? "e" : ""})` : "(noch keine eingezeichnet)";
}

// ------------------------------------------------------------------ heavy rain

const RAIN_STEPS = ["Gelände (DGM)", "Gebäude und Nutzung (ALKIS)", "Bemessungsregen (KOSTRA)", "Senken und Fließwege",
  "Überflutung je Ereignis", "Gebäude als Exposition", "Schadensberechnung", "Ergebnis"];

async function runRain() {
  const c = siteCenter();
  const box = Number($("#rain-box").value);
  if (st.provider.id !== "synthetic" && st.map.getZoom() < 14) {
    toast("Bitte zuerst den Standort suchen oder nah heranzoomen.", true);
    return;
  }
  const measures = $("#rain-use-units").checked ? st.units.map((u) => ({
    id: u.id, name: u.name, geometry: u.layer.toGeoJSON().geometry, roof_ids: [...u.roofs],
    depth_m: Number($("#rain-depth").value) || 0.3 })) : [];
  const cost = Number($("#rain-cost").value) || null;
  const body = { lon: c.lon, lat: c.lat, box_m: box, duration_min: Number($("#rain-duration").value),
    threshold_m: (Number($("#rain-threshold").value) || 0) / 100, sewer_mm: Number($("#rain-sewer").value) || 0,
    measures, cost_eur: measures.length ? cost : null };
  const btn = $("#rain-run");
  btn.disabled = true;
  $("#rain-error").hidden = true;
  const list = $("#rain-steps");
  list.hidden = false;
  list.innerHTML = RAIN_STEPS.map((s) => `<li>${esc(s)}<span class="t"></span></li>`).join("");
  let i = 0;
  const items = [...list.children];
  items[0].classList.add("active");
  const timer = setInterval(() => {
    if (i < items.length - 2) { items[i].classList.replace("active", "done"); items[++i].classList.add("active"); }
  }, st.provider.id === "synthetic" ? 150 : 1400);
  try {
    const res = await api(`/v3/risk/rain?provider=${st.provider.id}`, { method: "POST", body: JSON.stringify(body) });
    clearInterval(timer);
    items.forEach((li, k) => {
      li.classList.remove("active"); li.classList.add("done");
      const s = res.steps.find((x) => x.step === RAIN_STEPS[k]);
      if (s) li.querySelector(".t").textContent = `${s.s.toFixed(1)} s`;
    });
    R.rain = res;
    R.view.rp = res.rps[res.rps.length - 1];
    R.view.sc = res.scenarios[0];
    R.view.measure = false;
    renderRain();
    drawRainMap(true);
    save({ rainSettings: { box, duration: body.duration_min } });
    toast(`Analyse fertig in ${res.seconds.toLocaleString("de-DE")} s – ${res.buildings.length} Gebäude bewertet`);
  } catch (err) {
    clearInterval(timer);
    items.forEach((li) => li.classList.remove("active"));
    $("#rain-error").textContent = `Analyse fehlgeschlagen: ${err.message}`;
    $("#rain-error").hidden = false;
  } finally {
    btn.disabled = false;
  }
}

function bDepth(b, sc, rp, measure) {
  const src = measure && b.depth_measure_by_scenario ? b.depth_measure_by_scenario : b.depth_by_scenario;
  return (src[sc] || {})[String(rp)] ?? 0;
}

function renderRain() {
  const r = R.rain, sc0 = r.scenarios[0], rpMax = r.rps[r.rps.length - 1];
  const imp = r.impact[sc0];
  const thr = r.threshold_m;
  const wet = r.buildings.filter((b) => bDepth(b, sc0, rpMax) > thr);
  const maxB = Math.max(0, ...r.buildings.map((b) => bDepth(b, sc0, rpMax)));
  const ev = r.events.find((e) => e.scenario === sc0 && e.rp === rpMax);
  const fut = r.scenarios.includes("+2 K") ? r.impact["+2 K"] : null;
  const m = imp.measure;
  const engine = imp.worker ? `CLIMADA ${esc(imp.version)}` : "GECAA-Fallback";
  const alarm = maxB - thr >= 0.3;
  let head = `Bei einem <strong>${rpMax}-jährlichen Starkregen</strong> (${r.rain.duration_min} min, ${fmt1(ev.rain_mm)} mm) stehen an
    <strong>${wet.length} von ${r.buildings.length} Gebäuden</strong> mehr als ${Math.round(thr * 100)} cm Wasser, höchstens <strong>${fmtM(maxB)}</strong>.
    Erwarteter Schaden: <strong>${fmtEUR(imp.aai_agg)} pro Jahr</strong> (Größenordnung).`;
  if (fut) head += ` Mit +2 K: ${fmtEUR(fut.aai_agg)} pro Jahr.`;
  const kept = ev.measure ? ev.base.runoff_m3 - ev.measure.runoff_m3 : 0;
  if (m) head += ` Die Mulden halten dabei <strong>${fmt1(kept)} m³</strong> zurück (${ev.base.runoff_m3 ? fmt1((kept / ev.base.runoff_m3) * 100) : 0} % des Abflusses im Gebiet);
    der erwartete Schaden sinkt um <strong>${imp.aai_agg > 0 ? Math.round((m.benefit_annual / imp.aai_agg) * 100) : 0} %</strong>.`;
  const shift = (r.rp_shift || []).find((s) => s.warming_k === 2) || (r.rp_shift || [])[0];
  const kpi = (v, l, d, cls) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div>${d ? `<div class="d ${cls || ""}">${d}</div>` : ""}</div>`;
  const kpis = [
    kpi(fmtM(maxB), `max. Wassertiefe an Gebäuden (${rpMax} a)`),
    kpi(`${wet.length}`, `Gebäude über Schwellenhöhe (${rpMax} a)`, `von ${r.buildings.length} im Gebiet`),
    kpi(fmtEUR(imp.aai_agg), "erwarteter Schaden pro Jahr", fut ? `+2 K: ${fmtEUR(fut.aai_agg)} (${imp.aai_agg ? `+${Math.round(((fut.aai_agg - imp.aai_agg) / imp.aai_agg) * 100)} %` : "–"})` : "", "bad"),
    m ? kpi(`${fmt1(kept)} m³`, `Rückhalt der Mulden (${rpMax} a)`, `Speicher ${fmt1(r.measures.reduce((a, x) => a + x.storage_m3, 0))} m³`) : "",
    m ? kpi(fmtEURk(m.benefit_npv), "Nutzen der Maßnahme (30 J., 2 %)", m.bcr != null ? `Nutzen/Kosten ${m.bcr.toLocaleString("de-DE", { maximumFractionDigits: 2 })}` : "Kosten angeben für Nutzen/Kosten", m.bcr != null && m.bcr >= 1 ? "good" : "")
      : kpi(`${fmt1(ev.base.area_over_10cm_m2)} m²`, `Fläche > 10 cm Wasser (${rpMax} a)`),
  ].join("");
  const top = r.buildings.filter((b) => b.eai_eur > 0 || bDepth(b, sc0, rpMax) > 0.05).slice(0, 12);
  const rows = top.map((b) => `<tr class="clickable" data-b="${esc(b.id)}"><td>${esc(b.label || "Gebäude")}<br><small>${esc(b.cls_de)} · ${fmt1(b.footprint_m2)} m²</small></td>
      ${r.rps.map((rp) => { const d = bDepth(b, sc0, rp); return `<td><span class="depth-chip" style="background:${DEPTH_COL(d) || "transparent"};color:${d >= 0.3 ? "#fff" : "inherit"}">${d.toFixed(2)}</span></td>`; }).join("")}
      <td>${fmtEUR(b.eai_eur)}</td></tr>`).join("");
  const cmp = (r.bkg_comparison || []).filter((c) => c.bkg_agw != null || c.bkg_extrem != null);
  $("#rain-result").innerHTML = `
    <div class="headline ${alarm ? "alarm" : ""}">${head}</div>
    <div class="kpis">${kpis}</div>
    <div class="engine-note">Rechenkern: <strong>${engine}</strong>${imp.worker ? " (separater GPL-Dienst)" : ` – ${esc(imp.fallback_reason || "")}, gleiche Methode wie CLIMADA ImpactCalc`} ·
      Regen: ${esc(r.rain.source)} · Raster ${r.res_m} m, ${r.grid[0]}×${r.grid[1]} Zellen · ${r.seconds.toLocaleString("de-DE")} s</div>
    <h3 class="sec-title">Schaden nach Wiederkehrintervall</h3>
    <div id="risk-curve"></div>
    ${shift ? `<p class="note">Klimawandel (CLIMAAX-Methode <em>magnitude_frequency_shift</em>): Ein heute ${shift.rp_today}-jährlicher Regen (${fmt1(shift.mm_today)} mm)
      tritt bei +${shift.warming_k} K etwa <strong>alle ${shift.rp_future} Jahre</strong> auf; das ${shift.rp_today}-jährliche Ereignis bringt dann ${fmt1(shift.mm_future_same_rp)} mm (× ${shift.factor.toLocaleString("de-DE")}).</p>` : ""}
    <h3 class="sec-title">Betroffene Gebäude (heute)</h3>
    ${rows ? `<table class="data"><tr><th>Gebäude</th>${r.rps.map((rp) => `<th>${rp} a</th>`).join("")}<th>Schaden/Jahr</th></tr>${rows}</table>
      <p class="note">Wassertiefe in m am Gebäude (2-m-Streifen). Klick zeigt das Gebäude in der Karte.</p>` : `<p class="note">Kein Gebäude im Gebiet ist betroffen.</p>`}
    ${cmp.length ? `<h3 class="sec-title">Vergleich mit der BKG-Hinweiskarte</h3><table class="data"><tr><th>Gebäude</th><th>GECAA 100 a</th><th>BKG 100 a</th><th>BKG extrem</th></tr>
      ${cmp.map((c) => `<tr><td>${esc(c.label)}</td><td>${fmtM(c.gecaa_rp100)}</td><td>${fmtM(c.bkg_agw)}</td><td>${fmtM(c.bkg_extrem)}</td></tr>`).join("")}</table>
      <p class="note">BKG: 2D-Simulation, Wassertiefe am Gebäudemittelpunkt – Abweichungen sind bei einer Screening-Methode zu erwarten.</p>` : ""}
    <details><summary class="sec-title" style="cursor:pointer">Ereignisse im Detail</summary>
      <table class="data"><tr><th>Szenario</th><th>Jahre</th><th>Regen</th><th>max. Tiefe</th><th>&gt; 10 cm</th><th>gespeichert</th></tr>
      ${r.events.map((e) => `<tr><td>${esc(e.scenario)}</td><td>${e.rp}</td><td>${fmt1(e.rain_mm)} mm</td><td>${fmtM(e.base.max_depth_m)}</td>
        <td>${fmt1(e.base.area_over_10cm_m2)} m²${e.measure ? ` → ${fmt1(e.measure.area_over_10cm_m2)}` : ""}</td><td>${fmt1(e.base.stored_m3)} m³</td></tr>`).join("")}</table></details>
    <h3 class="sec-title">Annahmen (${r.assumptions.length})</h3>
    <ul class="assumptions">${r.assumptions.map((a) => `<li><span class="art ${esc(a.art)}">${esc(a.art)}</span>${esc(a.text)} <small>Quelle: ${esc(a.quelle)}</small></li>`).join("")}</ul>
    <div class="row" style="margin-top:12px">
      <button class="btn secondary" type="button" id="rain-to-cra">In die Risikobewertung übernehmen</button>
      <button class="btn secondary" type="button" id="rain-json">Ergebnis (JSON)</button>
      <button class="btn secondary" type="button" onclick="window.print()">Drucken / PDF</button>
    </div>`;
  $("#rain-result").hidden = false;
  // risk curve
  const series = r.scenarios.map((sc) => ({ name: `ohne Maßnahme · ${sc}`, color: SC_COL[sc] || "#555",
    points: r.impact[sc].freq_curve.map((p) => [p.rp, p.eur]) }));
  r.scenarios.forEach((sc) => {
    if (r.impact[sc].measure) series.push({ name: `mit Maßnahme · ${sc}`, color: SC_COL[sc] || "#555", dashed: true,
      points: r.impact[sc].measure.freq_curve.map((p) => [p.rp, p.eur]) });
  });
  Charts.line($("#risk-curve"), { series, xLog: true, xTicks: r.rps, xLabel: "Wiederkehrintervall (Jahre)", yLabel: "Schaden",
    yFmt: (v) => (v >= 1e6 ? `${(v / 1e6).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Mio` : v >= 1000 ? `${Math.round(v / 1000)} T` : `${Math.round(v)}`),
    title: "Schaden je Ereignis" });
  $("#rain-result").querySelectorAll("tr[data-b]").forEach((tr) => tr.addEventListener("click", () => focusBuilding(tr.dataset.b)));
  $("#rain-to-cra").onclick = () => switchTab("cra");
  $("#rain-json").onclick = () => download("gecaa-starkregen.json", JSON.stringify(R.rain, null, 2), "application/json");
}

function layerUrl(name) {
  return `/v3/risk/${R.rain.run_id}/layer?name=${encodeURIComponent(name)}`;
}

function drawRainMap(fit) {
  const r = R.rain;
  if (!r) return;
  if (!st.map.getPane("risk")) { st.map.createPane("risk").style.zIndex = 390; st.map.createPane("relief").style.zIndex = 380; }
  Object.values(R.ov).forEach((l) => st.map.removeLayer(l));
  R.ov = {};
  const v = R.view;
  if (v.relief) R.ov.relief = L.imageOverlay(layerUrl("hillshade"), r.bounds, { pane: "relief", opacity: 0.55 }).addTo(st.map);
  if (v.depth) {
    const variant = v.measure && r.layers.includes(`depth|${v.sc}|${v.rp}|measure`) ? "measure" : "base";
    R.ov.depth = L.imageOverlay(layerUrl(`depth|${v.sc}|${v.rp}|${variant}`), r.bounds, { pane: "risk", opacity: v.opacity }).addTo(st.map);
  }
  if (v.flow) R.ov.flow = L.imageOverlay(layerUrl("flow"), r.bounds, { pane: "risk", opacity: 0.9 }).addTo(st.map);
  // buildings coloured by depth at the building
  if (R.bLayer) st.map.removeLayer(R.bLayer);
  R.bLayer = L.geoJSON({ type: "FeatureCollection", features: r.buildings.map((b) => ({ type: "Feature", geometry: b.geometry, properties: { id: b.id } })) }, {
    style: (f) => {
      const b = r.buildings.find((x) => x.id === f.properties.id);
      const d = bDepth(b, v.sc, v.rp, v.measure) - r.threshold_m;
      const c = d >= 0.3 ? "#b0262b" : d >= 0.1 ? "#ec6b3a" : d > 0 ? "#f8b26a" : "#7f909a";
      return { color: c, weight: d > 0 ? 2.4 : 1, fillColor: c, fillOpacity: d > 0 ? 0.55 : 0.12 };
    },
    onEachFeature: (f, layer) => {
      const b = r.buildings.find((x) => x.id === f.properties.id);
      layer.bindPopup(() => `<h4>${esc(b.label || "Gebäude")}</h4>${esc(b.cls_de)} · ${fmt1(b.footprint_m2)} m² · Wert ${fmtEURk(b.value_eur)}<br>
        <table class="data">${r.rps.map((rp) => `<tr><td>${rp}-jährlich</td><td>${fmtM(bDepth(b, R.view.sc, rp))}</td>${b.depth_measure_by_scenario ? `<td>mit M.: ${fmtM(bDepth(b, R.view.sc, rp, true))}</td>` : ""}</tr>`).join("")}</table>
        Erwarteter Schaden: <strong>${fmtEUR(b.eai_by_scenario[R.view.sc])}/Jahr</strong> (${esc(R.view.sc)})`);
    },
  }).addTo(st.map);
  if (fit) st.map.fitBounds(r.bounds, { padding: [20, 20] });
  renderToolbar();
  renderLegend();
}

function focusBuilding(id) {
  R.bLayer.eachLayer((l) => {
    if (l.feature.properties.id === id) { st.map.fitBounds(l.getBounds(), { maxZoom: 19, padding: [60, 60] }); l.openPopup(); }
  });
}

function seg(name, values, current, label) {
  return `<span class="lbl">${label}</span><span class="seg" data-seg="${name}">${values.map((v) =>
    `<button type="button" data-v="${esc(v)}" aria-pressed="${String(v) === String(current)}">${esc(v)}</button>`).join("")}</span>`;
}

function renderToolbar() {
  const r = R.rain, v = R.view, tb = $("#map-toolbar");
  if (!r) { tb.hidden = true; return; }
  const hasM = r.measures.length > 0;
  tb.innerHTML = `${seg("rp", r.rps.map(String), v.rp, "Jährlichkeit")}${seg("sc", r.scenarios, v.sc, "Klima")}
    ${hasM ? `<label class="switch"><input type="checkbox" id="tb-measure" ${v.measure ? "checked" : ""}> mit Maßnahme</label>` : ""}
    <label class="switch"><input type="checkbox" id="tb-flow" ${v.flow ? "checked" : ""}> Fließwege</label>
    <label class="switch"><input type="checkbox" id="tb-relief" ${v.relief ? "checked" : ""}> Relief</label>
    <label class="switch" title="Deckkraft der Wassertiefe">◐ <input type="range" id="tb-op" min="0.2" max="1" step="0.05" value="${v.opacity}"></label>`;
  tb.hidden = !document.querySelector("#tab-rain[aria-selected=true]");
  tb.querySelectorAll("[data-seg] button").forEach((b) => b.addEventListener("click", () => {
    const k = b.parentElement.dataset.seg;
    v[k] = k === "rp" ? Number(b.dataset.v) : b.dataset.v;
    drawRainMap(false);
  }));
  const on = (id, k) => { const e = $(id); if (e) e.addEventListener("change", () => { v[k] = e.checked; drawRainMap(false); }); };
  on("#tb-measure", "measure"); on("#tb-flow", "flow"); on("#tb-relief", "relief");
  $("#tb-op").addEventListener("input", (e) => { v.opacity = Number(e.target.value); if (R.ov.depth) R.ov.depth.setOpacity(v.opacity); });
}

function renderLegend() {
  const r = R.rain, lg = $("#map-legend");
  if (!r) { lg.hidden = true; return; }
  lg.innerHTML = `<h4>Wassertiefe · ${R.view.rp}-jährlich · ${esc(R.view.sc)}${R.view.measure ? " · mit Maßnahme" : ""}</h4>
    ${r.legend.map(([l, c]) => `<div><span class="sw" style="background:${c}"></span>${esc(l)}</div>`).join("")}
    <div style="margin-top:6px"><span class="sw" style="background:#b0262b"></span>Gebäude &gt; 30 cm über Schwelle</div>
    <div><span class="sw" style="background:#ec6b3a"></span>Gebäude 10–30 cm über Schwelle</div>`;
  lg.hidden = !document.querySelector("#tab-rain[aria-selected=true]");
}

function setRainVisible(on) {
  Object.values(R.ov).forEach((l) => (on ? l.addTo(st.map) : st.map.removeLayer(l)));
  if (R.bLayer) on ? R.bLayer.addTo(st.map) : st.map.removeLayer(R.bLayer);
  if (R.boxLayer) on ? R.boxLayer.addTo(st.map) : st.map.removeLayer(R.boxLayer);
  $("#map-toolbar").hidden = !on || !R.rain;
  $("#map-legend").hidden = !on || !R.rain;
}

// ------------------------------------------------------------------ heat

async function runHeat() {
  const c = siteCenter();
  $("#heat-error").hidden = true;
  $("#heat-result").hidden = false;
  $("#heat-result").innerHTML = `<div class="skeleton"></div><div class="skeleton" style="width:70%"></div><div class="skeleton" style="width:85%"></div>
    <p class="note">DWD-Jahresraster werden beim ersten Mal geladen (ca. 65 Dateien) …</p>`;
  $("#heat-run").disabled = true;
  try {
    R.heat = await api(`/v3/heat?provider=${st.provider.id}&lon=${c.lon}&lat=${c.lat}`);
    renderHeat(c);
  } catch (err) {
    $("#heat-result").hidden = true;
    $("#heat-error").textContent = `Analyse fehlgeschlagen: ${err.message}`;
    $("#heat-error").hidden = false;
  } finally {
    $("#heat-run").disabled = false;
  }
}

function petColour(p) { return p == null ? "var(--line)" : p >= 41 ? "#f08a7e" : p >= 35 ? "#f8b26a" : p >= 29 ? "#fbd38d" : p >= 23 ? "#fde7c2" : "#e8f1d6"; }

function renderHeat(c) {
  const h = R.heat, ca = h.climate_analysis, hd = h.hot_days;
  const tile = (v, l, sub, col) => `<div class="tile-heat" style="background:${col}"><div class="v">${v}</div><div class="l">${l}</div>${sub ? `<small style="color:#4a2a10">${sub}</small>` : ""}</div>`;
  let html = `<p class="note">Ort: ${c.lat.toFixed(5)}, ${c.lon.toFixed(5)} (${esc(c.from)})</p>`;
  if (ca) {
    html += `<h3 class="sec-title">Klimaanalyse NRW (LANUK)</h3><div class="kpis">
      ${tile(`${fmt1(ca.pet_typisch)} °C`, "PET typischer Sommertag (14 Uhr)", esc(ca.pet_typisch_klasse || ""), petColour(ca.pet_typisch))}
      ${tile(`${fmt1(ca.pet_extrem)} °C`, "PET extremer Sommertag", esc(ca.pet_extrem_klasse || ""), petColour(ca.pet_extrem))}
      ${tile(`${fmt1(ca.nacht_typisch)} °C`, "Lufttemperatur Nacht (typisch, 4 Uhr)", ca.nacht_typisch >= 20 ? "Tropennacht-Niveau" : "", ca.nacht_typisch >= 20 ? "#f8b26a" : "#e3edf4")}
      ${tile(`${fmt1(ca.nacht_extrem)} °C`, "Lufttemperatur Nacht (extrem)", ca.nacht_extrem >= 20 ? "Tropennacht-Niveau" : "", ca.nacht_extrem >= 20 ? "#f8b26a" : "#e3edf4")}
      </div>${ca.versiegelung_pct != null ? `<p class="note">Versiegelungsgrad der Blockfläche: <strong>${fmt1(ca.versiegelung_pct)} %</strong>.</p>` : ""}
      <p class="note">PET = physiologisch äquivalente Temperatur; Klassen nach VDI 3787 Blatt 2. Quelle: ${esc(ca.source)}.</p>`;
  } else if (h.climate_analysis_error) {
    html += `<p class="error">Klimaanalyse nicht erreichbar: ${esc(h.climate_analysis_error)}</p>`;
  } else {
    html += `<p class="note">Für diese Datenquelle gibt es keine Klimaanalyse (nur NRW live).</p>`;
  }
  if (hd) {
    html += `<h3 class="sec-title">Heiße Tage (Tmax ≥ 30 °C) seit ${hd.years[0]}</h3><div id="hot-chart"></div>
      <div class="kpis">
        <div class="kpi"><div class="v">${fmt1(hd.mean_1961_1990)}</div><div class="l">pro Jahr 1961–1990</div></div>
        <div class="kpi"><div class="v">${fmt1(hd.mean_1991_2020)}</div><div class="l">pro Jahr 1991–2020</div></div>
        <div class="kpi"><div class="v">${fmt1(hd.mean_last10)}</div><div class="l">pro Jahr, letzte 10 Jahre</div></div>
        <div class="kpi"><div class="v">${hd.trend_per_decade > 0 ? "+" : ""}${fmt1(hd.trend_per_decade)}</div><div class="l">Trend je Jahrzehnt</div>
          <div class="d">95 %: ${fmt1(hd.trend_ci_per_decade[0])} bis ${fmt1(hd.trend_ci_per_decade[1])}</div></div>
      </div><p class="note">${esc(hd.method)}. Quelle: ${esc(hd.source)}.</p>`;
  } else if (h.hot_days_error) {
    html += `<p class="error">DWD-Daten nicht verfügbar: ${esc(h.hot_days_error)}</p>`;
  }
  html += `<div class="row" style="margin-top:12px"><button class="btn secondary" type="button" id="heat-to-cra">In die Risikobewertung übernehmen</button>
    ${st.wms.pet ? `<button class="btn secondary" type="button" id="heat-layer">Wärmebelastung in der Karte</button>` : ""}</div>`;
  $("#heat-result").innerHTML = html;
  if (hd) Charts.bars($("#hot-chart"), { years: hd.years, values: hd.values, trend: hd.trend_line, yLabel: "Tage", highlightFrom: 1991, title: "Heiße Tage pro Jahr" });
  $("#heat-to-cra").onclick = () => switchTab("cra");
  const hl = $("#heat-layer");
  if (hl) hl.onclick = () => { const cb = document.querySelector('[data-layer=pet]'); if (cb && !cb.checked) cb.click(); toast("Ebene „Wärmebelastung PET“ eingeschaltet"); };
}

// ------------------------------------------------------------------ CLIMAAX risk assessment

const prioCalc = (r) => Math.max(1, Math.min(4, Math.round(Math.max(r.severity_now, r.severity_future) * 0.5 + r.urgency * 0.5 - (r.capacity - 1) * 0.5)));

async function renderCRA() {
  if (!R.scales) R.scales = await api("/v3/cra/scales");
  const S = R.scales;
  const done = { scoping: !!(st.parcel || st.units.length || R.rain || R.heat), exploration: true, analysis: !!(R.rain || R.heat), key_risks: false, monitoring: false };
  $("#cra-steps").innerHTML = S.steps.map((s) => `<li class="${done[s.id] ? "done" : s.id === "key_risks" ? "now" : ""}"><div><b>${esc(s.title)}</b>${esc(s.text)}</div></li>`).join("");
  if (!R.rain && !R.heat) {
    $("#cra-table").innerHTML = `<div class="card"><p class="note" style="margin:0">Führen Sie zuerst die Analysen <strong>Starkregen</strong> und/oder <strong>Hitze</strong> aus – daraus wird die Bewertung vorgeschlagen.</p></div>`;
    $("#cra-matrix").innerHTML = "";
    return;
  }
  const rain = R.rain ? { rps: R.rain.rps, threshold_m: R.rain.threshold_m,
    buildings: R.rain.buildings.map((b) => ({ depth: b.depth_by_scenario[R.rain.scenarios[0]] })),
    buildings_future: R.rain.scenarios.includes("+2 K") ? R.rain.buildings.map((b) => ({ depth: b.depth_by_scenario["+2 K"] })) : null } : null;
  const sug = await api("/v3/cra/suggest", { method: "POST", body: JSON.stringify({ rain, heat: R.heat }) });
  const kept = R.cra ? Object.fromEntries(R.cra.map((x) => [x.hazard, x])) : {};
  R.cra = sug.rows.map((x) => (kept[x.hazard] && kept[x.hazard].edited ? { ...kept[x.hazard], basis: x.basis } : x));
  drawCRA(sug.note);
}

function drawCRA(note) {
  const S = R.scales;
  const sel = (row, k, labels) => `<select data-h="${esc(row.hazard)}" data-k="${k}" aria-label="${k}">${[1, 2, 3, 4].map((v) =>
    `<option value="${v}" ${row[k] === v ? "selected" : ""}>${v} · ${esc(labels[v])}</option>`).join("")}</select>`;
  R.cra.forEach((r) => (r.priority = prioCalc(r)));
  $("#cra-table").innerHTML = `<h3 class="sec-title">Bewertung der Schlüsselrisiken</h3>
    <div class="scrollx"><table class="data kra"><tr><th>Gefahr</th><th>Schwere heute</th><th>Schwere künftig</th><th>Dringlichkeit</th><th>Resilienz</th><th>Priorität</th></tr>
    ${R.cra.map((r) => `<tr><td><strong>${esc(r.hazard)}</strong><br><small>${esc(r.basis)}</small></td>
      <td>${sel(r, "severity_now", S.severity)}</td><td>${sel(r, "severity_future", S.severity)}</td><td>${sel(r, "urgency", S.urgency)}</td>
      <td>${sel(r, "capacity", S.capacity)}</td><td><span class="prio prio-${r.priority}">${esc(S.priority[r.priority])}</span></td></tr>`).join("")}</table></div>
    <p class="note">${esc(note || "")} Skalen: ${esc(S.source)}.</p>`;
  $("#cra-table").querySelectorAll("select").forEach((s) => s.addEventListener("change", () => {
    const row = R.cra.find((x) => x.hazard === s.dataset.h);
    row[s.dataset.k] = Number(s.value);
    row.edited = true;
    drawCRA(note);
  }));
  // matrix: future severity (columns) x urgency (rows)
  const shade = (sev, urg) => ["#e9f1e6", "#fbefd4", "#f7c9a3", "#f09a8c"][Math.min(3, Math.max(0, Math.round((sev + urg) / 2) - 1))];
  let cells = "";
  for (let u = 4; u >= 1; u--) {
    cells += `<div class="ax">${u}</div>`;
    for (let s = 1; s <= 4; s++) {
      const dots = R.cra.filter((r) => r.severity_future === s && r.urgency === u).map((r) => `<span class="dot">${esc(r.hazard)}</span>`).join("");
      cells += `<div class="cell" style="background:${shade(s, u)}">${dots}</div>`;
    }
  }
  $("#cra-matrix").innerHTML = `<h3 class="sec-title">Risikomatrix</h3><div class="matrix">${cells}<div></div>${[1, 2, 3, 4].map((s) => `<div class="ax">${s}</div>`).join("")}</div>
    <p class="note">Waagerecht: Schwere künftig (1 begrenzt … 4 kritisch) · senkrecht: Dringlichkeit.</p>
    <h3 class="sec-title">Monitoring – Vorschläge</h3><ul class="assumptions">
      ${R.rain ? "<li>Wassertiefen an den gefährdeten Gebäuden nach Starkregenereignissen dokumentieren (Fotos, Pegelmarken).</li><li>Funktion der Mulden prüfen: Einstau nach Regen, Entleerung binnen 24 h, Pflege der Oberfläche.</li>" : ""}
      ${R.heat ? "<li>Heiße Tage und Tropennächte jährlich aus den DWD-Daten fortschreiben.</li><li>Wärmebelastung (PET) nach Begrünung oder Entsiegelung mit der nächsten Klimaanalyse vergleichen.</li>" : ""}
    </ul>`;
}

// ------------------------------------------------------------------ engines, theme, export, persistence

async function renderEngines() {
  try { R.health = await api("/v3/health"); } catch (e) { R.health = null; }
  const w = R.health && R.health.climada_worker;
  const pill = $("#engine-pill");
  pill.textContent = w ? `CLIMADA ${w.climada}` : "Rechenkern: GECAA-Fallback";
  pill.classList.toggle("climada", !!w);
  pill.title = w ? "Schäden werden mit CLIMADA (ETH Zürich, GPL-3.0) im separaten Dienst berechnet" : "CLIMADA-Dienst nicht gestartet – eingebauter Rechenkern mit gleicher Methode";
  $("#engines").innerHTML = `<h2 style="font-size:16px">Rechenkerne und Methoden</h2>
    <div class="prov"><strong>CLIMADA</strong> ${w ? '<span class="pill ok">aktiv</span>' : '<span class="pill">nicht gestartet</span>'}
      <div>Risikomodell der ETH Zürich (GPL-3.0): Hazard, Exposures, ImpactCalc, CostBenefit. Läuft als separater Dienst
      (<code>services/climada_worker</code>), GECAA tauscht nur JSON aus. ${w ? `Version ${esc(w.climada)}.` : "Start: <code>services/climada_worker/install.sh</code> und <code>run.sh</code>."}</div></div>
    <div class="prov"><strong>CLIMAAX</strong> <span class="pill ok">eingebunden</span>
      <div>EU-Projekt, Handbuch und Workflows (Apache-2.0 oder CC BY 4.0): JRC-Schadensfunktionen und Höchstschäden (FLOODS),
      <em>magnitude_frequency_shift</em> (HEAVY_RAINFALL), Schritte und Skalen der Klimarisikobewertung (Handbook).</div></div>
    <div class="prov"><strong>GECAA-Hydrologie</strong> <span class="pill ok">eingebaut</span>
      <div>Priority-Flood (Barnes et al. 2014), D8-Fließwege, Senkenkaskade mit exakter Volumenbilanz, Fließtiefe nach Manning-Strickler.</div></div>`;
}

function initTheme() {
  const t = load().theme;
  if (t) document.documentElement.dataset.theme = t;
  $("#theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    save({ theme: document.documentElement.dataset.theme });
  });
}

function exportProject() {
  const project = {
    gecaa: "0.3", saved: new Date().toISOString(), provider: st.provider.id, view: { center: st.map.getCenter(), zoom: st.map.getZoom() },
    parcel: st.parcel, units: st.units.map((u) => ({ id: u.id, name: u.name, geometry: u.layer.toGeoJSON().geometry, roofs: [...u.roofs], inputs: u.inputs })),
    check: st.result || null, rain: R.rain, heat: R.heat, risk_assessment: R.cra,
  };
  download(`gecaa-projekt-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(project, null, 2), "application/json");
  toast("Projekt gespeichert");
}

document.addEventListener("gecaa:ready", () => {
  initTheme();
  renderEngines();
  const s = load();
  if (s.view && st.provider.id !== "synthetic") st.map.setView([s.view.lat, s.view.lng], s.view.zoom);
  if (s.rainSettings) { $("#rain-box").value = String(s.rainSettings.box); $("#rain-duration").value = String(s.rainSettings.duration); }
  st.map.on("moveend", () => { const c = st.map.getCenter(); save({ view: { lat: c.lat, lng: c.lng, zoom: st.map.getZoom() } }); });
  $("#rain-run").addEventListener("click", runRain);
  $("#heat-run").addEventListener("click", runHeat);
  $("#rain-box").addEventListener("change", showBox);
  $("#export-btn").addEventListener("click", exportProject);
  $("#layers-toggle").addEventListener("click", (e) => {
    const open = $("#layers").classList.toggle("open");
    e.currentTarget.setAttribute("aria-expanded", String(open));
  });
  if (s.tab && s.tab !== "check") switchTab(s.tab);
});

document.addEventListener("gecaa:tab", (e) => {
  const t = e.detail;
  save({ tab: t });
  setRainVisible(t === "rain");
  if (t === "rain") showBox();
  if (t === "cra") renderCRA().catch((err) => toast(err.message, true));
  if (t === "sources") renderEngines();
});

document.addEventListener("gecaa:provider", () => {
  Object.values(R.ov).forEach((l) => st.map.removeLayer(l));
  if (R.bLayer) st.map.removeLayer(R.bLayer);
  R.rain = null; R.heat = null; R.ov = {}; R.bLayer = null;
  $("#rain-result").hidden = true; $("#heat-result").hidden = true;
  $("#map-toolbar").hidden = true; $("#map-legend").hidden = true;
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && st.map && st.map.pm.globalDrawModeEnabled()) { st.map.pm.disableDraw(); $("#draw-btn").classList.remove("active"); }
});
