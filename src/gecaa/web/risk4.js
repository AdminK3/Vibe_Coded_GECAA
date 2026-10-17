// SPDX-License-Identifier: Apache-2.0
// GECAA v4 UI: more measures (green roof, unsealing, trees), sewer and 2D options, uncertainty, PDF, 3D,
// heatwaves (xclim), land surface temperature (Landsat), development plans. Uses v2.js/risk.js globals.
"use strict";

const M4 = { list: [], drawing: null, seq: 0, lst: null, swmm: null };
const KIND_DE = { mulde: "Mulde", gruendach: "Gründach", entsiegelung: "Entsiegelung", baeume: "Bäume" };
const KIND_COL = { gruendach: "#2e7d32", entsiegelung: "#8d6e63", baeume: "#1b5e20" };

function pointInPoly(pt, ring) {           // ray casting, [lng, lat]
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

window.onDrawCreate = (layer) => {
  if (!M4.drawing) return false;
  const kind = M4.drawing;
  M4.drawing = null;
  const g = layer.toGeoJSON().geometry;
  const m = { id: `M${++M4.seq}`, kind, name: `${KIND_DE[kind]} ${M4.seq}`, layer, geometry: g, variant: "extensiv", trees: 0, pit: 0, roofs: [] };
  if (kind === "gruendach") {
    m.roofs = Object.entries(st.bIndex).filter(([, b]) => {
      const c = b.layer.getBounds().getCenter();
      return pointInPoly([c.lng, c.lat], g.coordinates[0]);
    }).map(([id]) => id);
    if (!m.roofs.length) toast("Keine Dächer in der Fläche – zoomen Sie nah heran, damit Gebäude geladen sind.", true);
  }
  layer.setStyle({ color: KIND_COL[kind], fillColor: KIND_COL[kind], fillOpacity: 0.25, dashArray: "4 3" });
  layer.bindTooltip(() => m.name);
  M4.list.push(m);
  renderMeasures();
  return true;
};

function renderMeasures() {
  $("#measure-list").innerHTML = M4.list.map((m) => `<div class="mrow" data-m="${m.id}"><span class="k">${esc(m.name)}</span>
    ${m.kind === "gruendach" ? `<select data-f="variant"><option value="extensiv" ${m.variant === "extensiv" ? "selected" : ""}>extensiv</option><option value="intensiv" ${m.variant === "intensiv" ? "selected" : ""}>intensiv</option></select> ${m.roofs.length} Dächer` : ""}
    ${m.kind === "baeume" ? `<label class="inline">Anzahl <input type="number" min="0" max="500" data-f="trees" value="${m.trees}"></label>
      <label class="inline">Baumrigole m³/Baum <input type="number" min="0" max="20" step="0.5" data-f="pit" value="${m.pit}"></label>` : ""}
    ${m.kind === "entsiegelung" ? `${Math.round(L.GeometryUtil ? 0 : geodesicArea(m.geometry)).toLocaleString("de-DE")} m²` : ""}
    <button class="icon-btn" type="button" data-del="${m.id}" aria-label="Maßnahme entfernen" style="margin-left:auto">×</button></div>`).join("");
  $("#measure-list").querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", () => {
    const m = M4.list.find((x) => x.id === el.closest("[data-m]").dataset.m);
    m[el.dataset.f] = el.type === "number" ? Number(el.value) : el.value;
  }));
  $("#measure-list").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
    const i = M4.list.findIndex((x) => x.id === b.dataset.del);
    st.map.removeLayer(M4.list[i].layer);
    M4.list.splice(i, 1);
    renderMeasures();
  }));
}

window.extendRainBody = (body) => {
  body.engine = $("#rain-engine").value;
  if (body.engine === "2d") { body.scenarios = ["heute"]; toast("2D-Simulation: nur heutiges Klima, ca. 1 Minute"); }
  body.sewer_mode = $("#rain-sewer-mode").value;
  body.sewer_design_rp = Number($("#rain-sewer-rp").value);
  if (body.sewer_mode !== "pauschal") body.sewer_mm = 0;
  body.basement_share = Math.min(1, Math.max(0, Number($("#rain-basement").value) || 0));
  if (M4.swmm) body.swmm_inp_b64 = M4.swmm;
  body.measures = (body.measures || []).map((m) => ({ ...m, kind: "mulde" })).concat(M4.list.map((m) => ({
    id: m.id, name: m.name, kind: m.kind, geometry: m.geometry, roof_ids: m.roofs, variant: m.variant,
    trees: m.kind === "baeume" && m.trees ? m.trees : null, pit_m3_per_tree: m.pit || 0 })));
};

window.afterRain = () => {
  const r = R.rain, imp = r.impact[r.scenarios[0]];
  const persons = r.buildings.filter((b) => b.persons_est && bDepth(b, r.scenarios[0], r.rps[r.rps.length - 1]) > r.threshold_m)
    .reduce((a, b) => a + b.persons_est, 0);
  const hasPersons = r.buildings.some((b) => b.persons_est != null);
  const he = r.hydraulic_engine || {};
  const sew = r.sewer || {};
  let html = `<h3 class="sec-title">Rechenweg</h3><p class="note">Hydraulik: <strong>${esc(he.engine || "Screening")}</strong>${he.version ? ` ${esc(he.version)}` : ""} ·
    Kanalnetz: ${sew.mode === "bemessung" ? `bemessen auf ${sew.design_rp}-jährlichen Regen (${fmt1(sew.design_mm)} mm)` : sew.mode === "pauschal" ? "pauschal" : "nicht berücksichtigt"}${sew.swmm ? " · SWMM-Überstau eingerechnet" : ""}
    ${r.basement_share ? ` · Kelleranteil ${Math.round(r.basement_share * 100)} %` : ""}</p>`;
  if (hasPersons) html += `<p class="note">Bewohner in betroffenen Wohngebäuden (Zensus 2022, nach Geschossfläche verteilt): <strong>≈ ${Math.round(persons)}</strong></p>`;
  if (r.measures.length) {
    html += `<h3 class="sec-title">Wirkung je Maßnahme (${r.rps[r.rps.length - 1]}-jährlich)</h3><table class="data"><tr><th>Maßnahme</th><th>Art</th><th>Rückhalt</th><th>Hitze</th></tr>
      ${r.measures.map((m) => `<tr><td>${esc(m.name)}</td><td>${esc(KIND_DE[m.kind] || m.kind)}${m.variant && m.kind === "gruendach" ? ` (${esc(m.variant)})` : ""}</td>
        <td>${fmt1(m.retained_m3)} m³</td><td><small>${esc(m.heat_note || "")}</small></td></tr>`).join("")}</table>`;
  }
  html += `<div id="unc-box"><button class="btn secondary" type="button" id="unc-btn">Unsicherheit berechnen (CLIMADA unsequa)</button></div>
    <div class="row" style="margin-top:10px"><button class="btn primary" type="button" id="pdf-btn" style="width:auto">PDF-Bericht</button>
    <a class="btn secondary" href="/zwilling?run=${encodeURIComponent(r.run_id)}" target="_blank" rel="noopener">Digitaler Zwilling</a>
    <a class="btn secondary" href="/3d?run=${encodeURIComponent(r.run_id)}" target="_blank" rel="noopener">3D-Ansicht</a>
    <button class="btn primary" type="button" id="proj-btn" style="width:auto">Als Projekt planen</button>
    <a class="btn secondary" href="/v3/risk/${encodeURIComponent(r.run_id)}/geotiff?name=${encodeURIComponent(`depth|${r.scenarios[0]}|${r.rps[r.rps.length - 1]}|base`)}">GeoTIFF (QGIS)</a></div>`;
  const box = document.createElement("div");
  box.innerHTML = html;
  $("#rain-result").appendChild(box);
  $("#unc-btn").onclick = runUncertainty;
  $("#pdf-btn").onclick = downloadPdf;
  $("#proj-btn").onclick = createProject;
};

async function createProject() {
  const c = siteCenter();
  const label = st.parcel ? `Flurstück ${st.parcel.properties.label || ""}`.trim() : "Standort";
  const name = prompt("Name des Projekts", `Klimaanpassung ${label}`);
  if (!name) return;
  const site = { name: label, address: st.parcel && st.parcel.properties.address, lon: c.lon, lat: c.lat };
  const measures = (R.rain.measures || []).map((m) => {
    const d = M4.list.find((x) => x.id === m.id);
    return { id: m.id, name: m.name, kind: m.kind, variant: m.variant || null, geometry: d ? d.geometry : (m.geometry || null),
      trees: d && d.trees ? d.trees : null };
  });
  try {
    const p = await api("/v5/projects", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.slice(0, 160), site, run_id: R.rain.run_id, measures }) });
    window.open(`/projekte#${encodeURIComponent(p.id)}`, "_blank", "noopener");
  } catch (err) { toast(`Projekt konnte nicht angelegt werden: ${err.message}`, true); }
}

async function runUncertainty() {
  const b = $("#unc-btn");
  b.disabled = true;
  b.textContent = "Rechne Stichproben …";
  try {
    const u = await api(`/v3/risk/${R.rain.run_id}/uncertainty`);
    if (!u.aai_agg) { $("#unc-box").innerHTML = `<p class="note">${esc(u.note || "keine Angabe")}</p>`; return; }
    const { p5, p50, p95 } = u.aai_agg, max = p95 * 1.1 || 1;
    const s1 = u.sensitivity_S1 || {};
    const names = { curve_scale: "Schadensfunktion", exposure_scale: "Gebäudewerte", hazard_scale: "Wassertiefen" };
    $("#unc-box").innerHTML = `<h3 class="sec-title">Unsicherheit des erwarteten Jahresschadens</h3>
      <div class="range-bar"><span style="left:${(p5 / max) * 100}%;width:${((p95 - p5) / max) * 100}%"></span><i style="left:${(p50 / max) * 100}%"></i></div>
      <p class="note">90-%-Bereich <strong>${fmtEUR(p5)} – ${fmtEUR(p95)}</strong> pro Jahr, Median ${fmtEUR(p50)}.
      Rechenkern: ${esc(u.engine || "")}. Einfluss (Sobol S1): ${Object.entries(s1).map(([k, v]) => `${esc(names[k] || k)} ${fmt1(v * 100)} %`).join(" · ") || "–"}.</p>`;
  } catch (err) {
    b.disabled = false;
    b.textContent = "Unsicherheit berechnen";
    toast(err.message, true);
  }
}

async function downloadPdf() {
  const c = siteCenter();
  const site = { name: st.parcel ? `Flurstück ${st.parcel.properties.label || ""}` : "Standort", address: st.parcel && st.parcel.properties.address, lon: c.lon, lat: c.lat };
  try {
    const res = await fetch("/v3/report/pdf", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rain: R.rain, heat: R.heat, cra: R.cra, check: st.result || null, site }) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `gecaa-klimarisiko-${new Date().toISOString().slice(0, 10)}.pdf`;
    a.click();
  } catch (err) { toast(`PDF fehlgeschlagen: ${err.message}`, true); }
}

// ------------------------------------------------------------------ heat extras

async function runWaves() {
  const c = siteCenter(), out = $("#heat-extra");
  out.innerHTML = `<div class="skeleton"></div><p class="note">Tageswerte der nächsten DWD-Station werden geladen …</p>`;
  try {
    const w = await api(`/v3/heat/waves?lon=${c.lon}&lat=${c.lat}`);
    const yrs = w.years.filter((y) => y.heatwaves != null);
    out.innerHTML = `<h3 class="sec-title">Hitzewellen an der Station ${esc(w.station.name || w.station.id)} (${fmt1(w.station.distance_km)} km)</h3>
      <div id="hw-chart"></div><div id="tn-chart"></div>
      <div class="kpis"><div class="kpi"><div class="v">${fmt1(w.mean_last30.heatwaves)}</div><div class="l">Hitzewellen/Jahr (30 J.)</div></div>
      <div class="kpi"><div class="v">${fmt1(w.mean_last30.heatwave_max_days)}</div><div class="l">längste Hitzewelle (Tage)</div></div>
      <div class="kpi"><div class="v">${fmt1(w.mean_last30.tropical_nights)}</div><div class="l">Tropennächte/Jahr</div></div></div>
      <p class="note">${esc(w.method)}. Quelle: ${esc(w.source)}.</p>`;
    Charts.bars($("#hw-chart"), { years: yrs.map((y) => y.year), values: yrs.map((y) => y.heatwaves), yLabel: "Hitzewellen", color: "#d0612e", title: "Hitzewellen pro Jahr" });
    const tn = w.years.filter((y) => y.tropical_nights != null);
    Charts.bars($("#tn-chart"), { years: tn.map((y) => y.year), values: tn.map((y) => y.tropical_nights), yLabel: "Tropennächte", color: "#7b3fa0", title: "Tropennächte pro Jahr" });
  } catch (err) { out.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
}

async function runLst() {
  const c = siteCenter(), out = $("#heat-extra");
  out.innerHTML = `<div class="skeleton"></div><p class="note">Landsat-Sommerszenen werden gesucht und gemittelt …</p>`;
  try {
    const l = await api(`/v3/heat/lst?lon=${c.lon}&lat=${c.lat}&box_m=2000`);
    if (M4.lst) st.map.removeLayer(M4.lst);
    M4.lst = L.imageOverlay(`/v3/heat/lst/${l.id}.png`, l.bounds, { opacity: 0.75 }).addTo(st.map);
    st.map.fitBounds(l.bounds);
    out.innerHTML = `<h3 class="sec-title">Oberflächentemperatur im Sommer (Landsat, Median)</h3>
      <div class="kpis"><div class="kpi"><div class="v">${fmt1(l.stats.median)} °C</div><div class="l">Median im Gebiet</div></div>
      <div class="kpi"><div class="v">${fmt1(l.stats.p90)} °C</div><div class="l">heißeste 10 %</div></div>
      <div class="kpi"><div class="v">${fmt1(l.stats.max)} °C</div><div class="l">Maximum</div></div></div>
      <p class="note">Oberflächen-, nicht Lufttemperatur. ${l.scene_ids.length} Szenen. Klassen nach CLIMAAX-Hitzeworkflow. Quelle: ${esc(l.source)}.
      <button class="btn link" type="button" id="lst-off">Ebene ausblenden</button></p>`;
    $("#lst-off").onclick = () => { st.map.removeLayer(M4.lst); M4.lst = null; };
  } catch (err) { out.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
}

async function showStreet() {
  const c = siteCenter(), box = $("#street");
  box.innerHTML = `<p class="note">Suche offene Straßenbilder …</p>`;
  api(`/v6/streetview?lon=${c.lon}&lat=${c.lat}`).then((g) => {
    if (!g.enabled) return;
    const el = document.createElement("div");
    el.innerHTML = g.available ? `<iframe class="gsv" src="${esc(g.embed_url)}" loading="lazy" allowfullscreen referrerpolicy="no-referrer-when-downgrade" title="Google Street View"></iframe>
      <p class="note">${esc(g.note)} <a href="${esc(g.maps_url)}" target="_blank" rel="noopener noreferrer">In Google Maps öffnen</a></p>`
      : `<p class="note">Google Street View: kein Panorama im Umkreis.</p>`;
    box.before(el);
  }).catch(() => {});
  try {
    const r = await api(`/v6/street?lon=${c.lon}&lat=${c.lat}&radius=120`);
    if (!r.images.length) { box.innerHTML = `<p class="note">${esc(r.error || "Keine offenen Straßenbilder im Umkreis von 120 m.")} Eigene Fotos lassen sich bei Panoramax hochladen.</p>`; return; }
    box.innerHTML = `<div class="street-strip">${r.images.slice(0, 6).map((im) => `<a href="${esc(im.viewer)}" target="_blank" rel="noopener noreferrer" title="${esc(im.date)} · © ${esc(im.author || "")} · ${esc(im.license || "")}">
      <img src="${esc(im.thumb)}" alt="Straßenbild ${esc(im.date)}" loading="lazy"><small>${im.distance_m} m</small></a>`).join("")}</div>
      <p class="note">Quelle: ${esc(r.sources.join(", "))}. Klick öffnet den Panoramax-Viewer.</p>`;
  } catch (err) { box.innerHTML = `<p class="note">Straßenbilder nicht erreichbar: ${esc(err.message)}</p>`; }
}

async function showPlans() {
  const c = siteCenter(), out = $("#plans");
  out.innerHTML = `<div class="skeleton"></div>`;
  try {
    const p = await api(`/v3/plans?provider=${st.provider.id}&lon=${c.lon}&lat=${c.lat}&box_m=400`);
    out.innerHTML = p.plans.length ? `<table class="data"><tr><th>Plan</th><th>Status</th><th>seit</th></tr>${p.plans.map((x) =>
      `<tr><td>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name || x.number || "Plan")}</a>` : esc(x.name || "Plan")}<br><small>${esc(x.type || "")} · ${esc(x.municipality || "")}</small></td>
       <td>${esc(x.status || "")}</td><td>${esc(x.valid_from || "")}</td></tr>`).join("")}</table><p class="note">${esc(p.source || "")}</p>`
      : `<p class="note">${esc(p.note || "Keine Bebauungspläne im Umkreis gemeldet (die Abdeckung hängt von der Kommune ab).")}</p>`;
  } catch (err) { out.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
}

document.addEventListener("gecaa:ready", () => {
  document.querySelectorAll("[data-measure]").forEach((b) => b.addEventListener("click", () => {
    M4.drawing = b.dataset.measure;
    st.map.pm.enableDraw("Polygon", { finishOn: "dblclick" });
    toast(`${KIND_DE[M4.drawing]}: Fläche in der Karte einzeichnen (Doppelklick beendet)${M4.drawing === "gruendach" ? " – alle Dächer darin werden begrünt" : ""}`);
  }));
  $("#heat-waves-btn").addEventListener("click", runWaves);
  $("#heat-lst-btn").addEventListener("click", runLst);
  $("#street-btn") && ($("#street-btn").onclick = showStreet);
$("#plans-btn").addEventListener("click", showPlans);
  $("#rain-swmm").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) { M4.swmm = null; return; }
    if (f.size > 2e6) { toast("SWMM-Datei größer als 2 MB", true); e.target.value = ""; return; }
    const buf = new Uint8Array(await f.arrayBuffer());
    let s = ""; buf.forEach((x) => (s += String.fromCharCode(x)));
    M4.swmm = btoa(s);
  });
});
