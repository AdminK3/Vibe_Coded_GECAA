// SPDX-License-Identifier: Apache-2.0
// GECAA v2: K3 logic, evaluation units (system location + catchment), live geodata.
"use strict";

const LOGIC_ID = "k3_dwa_a_138_1_mre";
const RESULT_DE = { PASSED: "möglich", OPEN: "potenziell möglich (offen)", BLOCKED: "nicht möglich", PARTLY: "teilweise versickerbar" };
const SYM = { PASSED: "✓", OPEN: "?", BLOCKED: "✕", PARTLY: "◐" };
const COLOUR = { PASSED: "#2b7a3d", OPEN: "#b07400", BLOCKED: "#a5312a", PARTLY: "#6b6b00" };
const UNIT_INPUTS = ["TA", "KF", "GWF"];      // asked per location (KF/GWF only to override map values)
const SITE_INPUTS = ["ALT", "KT"];            // answered once for the site
const BUILDING_MIN_ZOOM = 17;

const st = {
  logic: null, providers: [], provider: null, provisions: {}, map: null, wms: {}, demoOverlays: {},
  buildings: null, bIndex: {}, parcelLayer: null, parcel: null, units: [], active: null, seq: 0,
  result: null, cy: null, parcelMode: false,
};

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const m2 = (v) => (v == null ? "–" : `${Math.round(v).toLocaleString("de-DE")} m²`);
const sym = (r, big) => `<span class="sym ${r}${big ? " big" : ""}" aria-label="${esc(RESULT_DE[r])}">${SYM[r]}</span>`;
const DEMO_DE = { "Soil permeability (kf)": "Durchlässigkeit kf (Demo)", "Depth to groundwater": "Flurabstand Grundwasser (Demo)", "Terrain slope": "Geländeneigung (Demo)",
  "Too slow": "zu gering", "Suitable": "geeignet", "Too fast": "zu hoch", "Too shallow": "zu gering", "Borderline": "grenzwertig", "Sufficient": "ausreichend",
  "Flat": "eben", "Moderate": "mäßig", "Steep": "steil" };
const de = (t) => { if (DEMO_DE[t]) return DEMO_DE[t]; const m = String(t).match(/^([^(]+?)\s*(\(.*\))?$/); return m && DEMO_DE[m[1]] ? `${DEMO_DE[m[1]]}${m[2] ? " " + m[2].replace(/ to /g, " bis ") : ""}` : t; };
const SUP = { "-": "⁻", 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹" };
function num(v) {
  if (typeof v !== "number") return v;
  if (v !== 0 && (Math.abs(v) < 0.01 || Math.abs(v) >= 1e5)) {
    const [m, e] = v.toExponential().split("e");
    const exp = String(Number(e)).replace(/./g, (c) => SUP[c]);
    return `${Number(m) === 1 ? "" : Number(m).toLocaleString("de-DE") + "·"}10${exp}`;
  }
  return v.toLocaleString("de-DE");
}
const dqClass = (q) => (q === "hoch" ? "hoch" : q === "mittel" ? "mittel" : q === "Annahme offen" ? "offen" : "");

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const json = (res.headers.get("content-type") || "").includes("json");
  const body = json ? await res.json() : await res.text();
  if (!res.ok) throw new Error(json ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : body);
  return body;
}

// ------------------------------------------------------------------ start

async function init() {
  try {
    const [logics, providers, provisions] = await Promise.all([api("/v2/logics"), api("/v2/providers"), api("/v1/provisions")]);
    st.logic = logics.find((l) => l.id === LOGIC_ID) || logics[0];
    st.providers = providers;
    provisions.forEach((p) => (st.provisions[p.id] = p));
  } catch (err) {
    document.body.insertAdjacentHTML("afterbegin", `<p class="error">GECAA konnte nicht geladen werden: ${esc(err.message)}</p>`);
    return;
  }
  const preferred = new URLSearchParams(location.search).get("daten");
  st.provider = st.providers.find((p) => p.id === preferred) || st.providers.find((p) => p.id === "nrw") || st.providers[0];
  $("#provider").innerHTML = st.providers.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");
  $("#provider").value = st.provider.id;
  renderBadges();
  initMap();
  renderSiteInputs();
  renderLogicTab();
  renderSources();
  initEvents();
  await switchProvider(st.provider.id);
  document.dispatchEvent(new CustomEvent("gecaa:ready"));
}

function renderBadges() {
  const b = [];
  if (st.logic.review_status !== "done") b.push(`<span class="badge" title="Die Prüflogik hat das unabhängige Audit noch nicht durchlaufen">Prüflogik: Entwurf</span>`);
  b.push(`<span class="badge info" id="provider-badge"></span>`);
  b.push(`<span class="badge info">Entscheidungshilfe – es entscheidet die Behörde</span>`);
  $("#badges").innerHTML = b.join("");
}

// ------------------------------------------------------------------ map

function initMap() {
  const map = L.map("map", { zoomControl: true });
  st.map = map;
  const basemap = (style) => L.tileLayer(
    `https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_${style}/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png`,
    { maxZoom: 21, maxNativeZoom: 18, attribution: '&copy; <a href="https://basemap.de" target="_blank" rel="noopener">basemap.de</a> / BKG (CC BY 4.0)' });
  const colour = basemap("farbe").addTo(map);
  L.control.layers({ "basemap.de farbig": colour, "basemap.de grau": basemap("grau") }, null, { position: "topleft" }).addTo(map);
  map.setView([51.43, 7.66], 8);   // NRW
  map.pm.setGlobalOptions({ snappable: true, allowSelfIntersection: false });
  map.on("pm:create", (ev) => {
    $("#draw-btn").classList.remove("active");
    addUnit(ev.layer);
  });
  map.on("moveend", () => loadBuildings());
  map.on("click", (ev) => { if (st.parcelMode) pickParcel(ev.latlng); });
  st.buildings = L.geoJSON(null, {
    style: () => buildingStyle(false),
    onEachFeature: (f, layer) => {
      const id = String(f.properties.id);
      st.bIndex[id] = { feature: f, layer, area: geodesicArea(f.geometry) };
      layer.bindTooltip(`${esc(f.properties.function || "Gebäude")} · ${m2(st.bIndex[id].area)}`, { sticky: true });
      layer.on("click", (e) => { L.DomEvent.stop(e); toggleRoof(id); });
    },
  }).addTo(map);
}

const buildingStyle = (selected) => selected
  ? { color: "#0a5c8f", weight: 2, fillColor: "#0a5c8f", fillOpacity: 0.45 }
  : { color: "#ffffff", weight: 1.2, fillColor: "#37474f", fillOpacity: 0.2 };
const unitStyle = (u, result) => {
  const c = result ? COLOUR[result] : (u === st.active ? "#0a5c8f" : "#2f6f8f");
  return { color: c, weight: u === st.active ? 3 : 2, dashArray: result ? null : "5 4", fillColor: c, fillOpacity: result ? 0.4 : 0.15 };
};

async function switchProvider(id) {
  st.provider = st.providers.find((p) => p.id === id);
  document.dispatchEvent(new CustomEvent("gecaa:provider", { detail: id }));
  $("#provider-badge").textContent = `Daten: ${st.provider.label}`;
  Object.values(st.wms).forEach((l) => st.map.removeLayer(l));
  Object.values(st.demoOverlays).forEach((l) => st.map.removeLayer(l));
  st.wms = {};
  st.demoOverlays = {};
  st.buildings.clearLayers();
  st.bIndex = {};
  const panel = [`<h3>Kartenebenen</h3>`];
  if (st.provider.id === "synthetic") {
    const layers = await api("/v1/layers");
    layers.filter((l) => l.type === "raster").forEach((l) => {
      st.demoOverlays[l.id] = L.imageOverlay(l.url, [[l.bounds[1], l.bounds[0]], [l.bounds[3], l.bounds[2]]], { opacity: 0.8 });
      panel.push(layerToggle(l.id, de(l.label), false, (l.legend || []).map(([t, c]) => [de(t), c])));
    });
    const zones = await api("/v1/layers/zones.geojson");
    st.demoOverlays.zones = L.geoJSON(zones, { style: (f) => ({ color: String(f.properties.zone).startsWith("III") ? "#b07400" : "#a5312a", weight: 1.5, fillOpacity: 0.12 }) });
    panel.push(layerToggle("zones", "Wasserschutzzonen (Demo)", true, []));
    st.demoOverlays.zones.addTo(st.map);
    const [w, s, e, n] = st.provider.bounds;
    st.map.fitBounds([[s, w], [n, e]], { padding: [20, 20] });
  } else {
    st.provider.map_layers.forEach((m) => {
      st.wms[m.id] = L.tileLayer.wms(m.url, { layers: m.layers, format: "image/png", transparent: true, opacity: m.opacity,
        attribution: m.attribution, minZoom: m.min_zoom, maxZoom: 21, version: "1.3.0" });
      panel.push(layerToggle(m.id, m.label, false, [], m.legend_url));
    });
  }
  panel.push(`<div class="src">Gebäude: Klick wählt das Dach als Einzugsgebiet des aktiven Standorts.</div>`);
  $("#layers").innerHTML = panel.join("");
  loadBuildings();
}

function layerToggle(id, label, on, legend, legendUrl) {
  const lg = (legend || []).map(([t, c]) => `<div><span class="swatch" style="background:${c}"></span>${esc(t)}</div>`).join("");
  return `<label><input type="checkbox" data-layer="${esc(id)}" ${on ? "checked" : ""}> ${esc(label)}</label>${lg ? `<div class="legend">${lg}</div>` : ""}${legendUrl ? `<div class="legend"><a href="${esc(legendUrl)}" target="_blank" rel="noopener">Legende</a></div>` : ""}`;
}

let buildingTimer = null;
function loadBuildings() {
  clearTimeout(buildingTimer);
  buildingTimer = setTimeout(async () => {
    const synthetic = st.provider.id === "synthetic";
    const zoomOk = st.map.getZoom() >= BUILDING_MIN_ZOOM;
    $("#zoom-hint").hidden = synthetic || zoomOk;
    if (!synthetic && !zoomOk) return;
    const b = synthetic ? st.provider.bounds : st.map.getBounds().toBBoxString().split(",").map(Number);
    try {
      const fc = await api(`/v2/buildings?provider=${st.provider.id}&bbox=${b.map((x) => x.toFixed(6)).join(",")}`);
      fc.features.filter((f) => !st.bIndex[String(f.properties.id)]).forEach((f) => st.buildings.addData(f));
      refreshRoofStyles();
    } catch (err) {
      $("#zoom-hint").hidden = false;
      $("#zoom-hint").textContent = `Gebäude konnten nicht geladen werden: ${err.message}`;
    }
  }, 350);
}

// ------------------------------------------------------------------ parcel and search

async function pickParcel(latlng) {
  st.parcelMode = false;
  $("#parcel-btn").classList.remove("active");
  $("#parcel-card").hidden = false;
  $("#parcel-card").innerHTML = `<span class="wait">Flurstück wird gesucht…</span>`;
  try {
    const f = await api(`/v2/parcel?provider=${st.provider.id}&lon=${latlng.lng}&lat=${latlng.lat}`);
    if (st.parcelLayer) st.map.removeLayer(st.parcelLayer);
    st.parcelLayer = L.geoJSON(f, { style: { color: "#c25e00", weight: 3, fill: false, dashArray: "8 5" }, interactive: false }).addTo(st.map);
    st.parcel = f;
    const p = f.properties;
    $("#parcel-card").innerHTML = `<strong>Flurstück ${esc(p.label || p.id)}</strong>${p.address ? `${esc(p.address)}<br>` : ""}${m2(p.area_m2 || geodesicArea(f.geometry))}${p.use ? ` · ${esc(String(p.use).split(";")[0])}` : ""}${p.source ? ` · ${esc(p.source)}` : ""}`;
    st.map.fitBounds(st.parcelLayer.getBounds(), { maxZoom: 19, padding: [30, 30] });
  } catch (err) {
    $("#parcel-card").innerHTML = `<span class="error">${esc(err.message)}</span>`;
  }
}

async function search() {
  const q = $("#search").value.trim();
  if (q.length < 3) return;
  $("#hits").hidden = false;
  $("#hits").innerHTML = `<li class="wait" style="padding:6px 10px">Suche…</li>`;
  try {
    const hits = await api(`/v2/geocode?q=${encodeURIComponent(q)}`);
    $("#hits").innerHTML = hits.length ? hits.map((h, i) => `<li><button type="button" data-i="${i}">${esc(h.label)}</button></li>`).join("") : `<li class="wait" style="padding:6px 10px">Nichts gefunden.</li>`;
    $("#hits").onclick = (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      const h = hits[Number(b.dataset.i)];
      st.map.setView([h.lat, h.lon], 18);
      $("#hits").hidden = true;
    };
  } catch (err) {
    $("#hits").innerHTML = `<li class="error">${esc(err.message)}</li>`;
  }
}

// ------------------------------------------------------------------ units (locations + catchment)

function addUnit(layer) {
  const u = { id: `S${++st.seq}`, name: `Standort ${st.seq}`, layer, roofs: new Set(), paved: "", inputs: {} };
  st.units.push(u);
  layer.pm.enable({ allowSelfIntersection: false });
  layer.on("click", (e) => { L.DomEvent.stop(e); setActive(u); });
  layer.on("pm:edit", () => renderUnits());
  setActive(u);
  layer.bindTooltip(() => esc(u.name), { permanent: false });
}

function setActive(u) {
  st.active = u;
  st.units.forEach((x) => x.layer.setStyle(unitStyle(x, x.result)));
  refreshRoofStyles();
  renderUnits();
}

function toggleRoof(id) {
  if (!st.active) {
    flash("Zeichnen Sie zuerst einen Anlagenstandort ein; danach ordnen Sie Dächer zu.");
    return;
  }
  const r = st.active.roofs;
  if (r.has(id)) r.delete(id);
  else { st.units.forEach((x) => x.roofs.delete(id)); r.add(id); }   // a roof drains into one location only
  refreshRoofStyles();
  renderUnits();
}

function refreshRoofStyles() {
  const sel = st.active ? st.active.roofs : new Set();
  Object.entries(st.bIndex).forEach(([id, b]) => b.layer.setStyle(buildingStyle(sel.has(id))));
}

function removeUnit(id) {
  const u = st.units.find((x) => x.id === id);
  if (!u) return;
  st.map.removeLayer(u.layer);
  st.units = st.units.filter((x) => x !== u);
  if (st.active === u) st.active = st.units[st.units.length - 1] || null;
  setActive(st.active);
}

function inputField(abbr, value, scope) {
  const spec = st.logic.inputs.find((i) => i.abbr === abbr);
  const id = `${scope}-${abbr}`;
  if (spec.kind === "choice") {
    return `<div class="field"><label for="${id}">${esc(spec.label)}</label><select id="${id}" data-abbr="${abbr}" data-scope="${scope}">
      <option value="">– nicht bekannt –</option>${Object.entries(spec.choices).map(([k, v]) => `<option value="${esc(k)}" ${value === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
      ${spec.help ? `<small>${esc(spec.help)}</small>` : ""}</div>`;
  }
  return `<div class="field"><label for="${id}">${esc(spec.label)}${spec.unit ? ` (${esc(spec.unit)})` : ""}</label>
    <input type="text" inputmode="decimal" id="${id}" data-abbr="${abbr}" data-scope="${scope}" value="${esc(value ?? "")}" placeholder="${abbr === "KF" ? "z. B. 2e-5 – sonst Bodenkarte" : abbr === "GWF" ? "sonst Bodenkarte" : ""}">
    ${spec.help ? `<small>${esc(spec.help)}</small>` : ""}</div>`;
}

function renderUnits() {
  $("#units").innerHTML = st.units.map((u) => {
    const roofArea = [...u.roofs].reduce((s, id) => s + (st.bIndex[id] ? st.bIndex[id].area : 0), 0);
    const total = roofArea + (Number(String(u.paved).replace(",", ".")) || 0);
    return `<div class="unit ${u === st.active ? "active" : ""}" data-unit="${u.id}">
      <div class="uhead">${u.result ? sym(u.result) : ""}<input type="text" value="${esc(u.name)}" data-name="${u.id}" aria-label="Name des Standorts">
        <button type="button" class="icon-btn" data-remove="${u.id}" title="Standort entfernen" aria-label="Standort entfernen">×</button></div>
      <div class="meta">Standort ${m2(geodesicArea(u.layer.toGeoJSON().geometry))} · Einzugsgebiet ${m2(total || null)} (${u.roofs.size} Dach${u.roofs.size === 1 ? "" : "flächen"}${u.paved ? " + befestigt" : ""})</div>
      ${u === st.active ? `<p class="hint" style="margin:0">Aktiv: Klicken Sie auf Dächer, um sie zuzuordnen.</p>` : `<button type="button" class="btn link" data-activate="${u.id}">Aktivieren</button>`}
      <div class="field"><label for="${u.id}-paved">Weitere befestigte Fläche im Einzugsgebiet (m²)</label><input type="text" inputmode="decimal" id="${u.id}-paved" data-paved="${u.id}" value="${esc(u.paved)}"></div>
      ${inputField("TA", u.inputs.TA, u.id)}
      <details><summary>Eigene Messwerte statt Kartenwerte</summary>${inputField("KF", u.inputs.KF, u.id)}${inputField("GWF", u.inputs.GWF, u.id)}</details>
    </div>`;
  }).join("");
  $("#assess-btn").disabled = st.units.length === 0;
}

function renderSiteInputs() {
  $("#site-inputs").innerHTML = SITE_INPUTS.map((a) => inputField(a, null, "site")).join("");
}

// ------------------------------------------------------------------ assessment

function request() {
  const site_inputs = {};
  document.querySelectorAll("[data-scope=site]").forEach((el) => { if (el.value !== "") site_inputs[el.dataset.abbr] = el.value; });
  return {
    logic_id: st.logic.id,
    site_name: st.parcel ? `Flurstück ${st.parcel.properties.label || st.parcel.properties.id}` : null,
    parcel: st.parcel ? st.parcel.geometry : null,
    site_inputs,
    units: st.units.map((u) => ({
      id: u.id, name: u.name, location: u.layer.toGeoJSON().geometry, inputs: Object.fromEntries(Object.entries(u.inputs).filter(([, v]) => v !== "" && v != null)),
      catchment: [...[...u.roofs].map((id) => ({ id, kind: "roof", area_m2: st.bIndex[id] ? Math.round(st.bIndex[id].area) : null, label: st.bIndex[id] ? st.bIndex[id].feature.properties.function : null })),
        ...(u.paved ? [{ id: `${u.id}-befestigt`, kind: "paved", area_m2: Number(String(u.paved).replace(",", ".")) || null }] : [])],
    })),
  };
}

async function assess(ev) {
  ev.preventDefault();
  const btn = $("#assess-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Daten werden abgerufen und geprüft…`;
  $("#assess-error").hidden = true;
  try {
    const res = await api(`/v2/assess?provider=${st.provider.id}`, { method: "POST", body: JSON.stringify(request()) });
    st.result = res;
    res.units.forEach((r) => {
      const u = st.units.find((x) => x.id === r.id);
      if (u) { u.result = r.result; u.layer.setStyle(unitStyle(u, r.result)); }
    });
    renderUnits();
    renderResult(res);
    updateGraphUnits();
  } catch (err) {
    $("#assess-error").textContent = `Prüfung fehlgeschlagen: ${err.message}`;
    $("#assess-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Prüfen";
  }
}

function renderResult(r) {
  const label = (abbr) => (st.logic.inputs.find((i) => i.abbr === abbr) || {}).label || abbr;
  const unitCard = (u, i) => `<div class="ucard">
      <div class="uh">${sym(u.result)} ${esc(u.name)} <span class="c-${u.result}" style="margin-left:auto;font-weight:600">${esc(RESULT_DE[u.result])}</span></div>
      <div class="checks">${u.checks.map((c) => `<div class="chk">${sym(c.result)}<div><div class="q">${esc(c.id)} ${esc(c.question)}${c.derived ? ` <span class="tagd">abgeleitet</span>` : ""}</div>
        <div>${esc(c.text)}</div>
        ${c.value_display != null ? `<div class="v">Ist: ${esc(c.value_display)}${c.threshold_display ? ` · Soll: ${esc(c.threshold_display)}` : ""}${c.p_passed != null && c.p_passed > 0 && c.p_passed < 1 ? ` · Wahrscheinlichkeit erfüllt ${Math.round(c.p_passed * 100)} % (aus der Unsicherheit der Kartendaten)` : ""}</div>` : ""}
        ${c.inputs_used.length ? `<div class="v">${c.inputs_used.filter((x) => x.value != null).map((x) => `${esc(label(x.abbr))}: <span class="dq ${dqClass(x.quality)}">${esc(x.quality)}</span> ${esc(x.source || "")}`).join("<br>")}</div>` : ""}
        ${c.basis.length ? `<div class="cites">${c.basis.filter((b) => st.provisions[b]).map((b) => `<button class="cite" type="button" data-prov="${esc(b)}">${esc(st.provisions[b].citation)}</button>`).join("")}</div>` : ""}
      </div></div>`).join("")}
      ${u.p_passed != null ? `<p class="v" style="font-size:12.5px;color:var(--muted)">Wahrscheinlichkeit, dass alle Prüfungen bei der Unsicherheit der Kartendaten erfüllt sind: ${Math.round(u.p_passed * 100)} %.</p>` : ""}</div></div>`;
  const counts = { PASSED: 0, OPEN: 0, BLOCKED: 0 };
  r.units.forEach((u) => counts[u.result]++);
  $("#result").innerHTML = `
    <div class="ampel bg-${r.result}">${sym(r.result, true)}<div><div class="big c-${r.result}">${esc(RESULT_DE[r.result].replace(/^./, (c) => c.toUpperCase()))}</div>
      <div style="font-size:13px">${counts.PASSED} möglich · ${counts.OPEN} offen · ${counts.BLOCKED} nicht möglich</div></div></div>
    ${r.open_points.length ? `<div class="block"><h3>Nächste Schritte</h3><ol class="steps-list">${r.open_points.map((p) => `<li><strong>${esc(p.check)}</strong> ${esc(p.text)}<br><small>Betrifft: ${esc(p.units.join(", "))}${p.responsible ? ` · Zuständig: ${esc(p.responsible)}` : ""}</small></li>`).join("")}</ol></div>` : ""}
    <div class="block"><h3>Standorte</h3>${r.units.map(unitCard).join("")}</div>
    <div class="block"><h3>Noch nicht codiert</h3><ul class="gaps">${r.not_coded.map((n) => `<li>${esc(n.topic)}</li>`).join("")}</ul></div>
    <p style="font-size:12.5px;color:var(--muted)">„Möglich“ heißt: alle codierten Prüfungen sind erfüllt. Kein Nachweis der Richtlinienkonformität und keine fachliche Empfehlung.</p>
    <div class="actions"><button class="btn secondary" type="button" id="report-btn">Bericht öffnen</button>
      <button class="btn secondary" type="button" id="json-btn">Daten (JSON)</button>
      <button class="btn secondary" type="button" id="graph-btn">In der Prüflogik zeigen</button></div>`;
  $("#result").hidden = false;
  $("#result").scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  $("#report-btn").onclick = openReport;
  $("#json-btn").onclick = () => download("gecaa-pruefung.json", JSON.stringify({ request: request(), result: r }, null, 2), "application/json");
  $("#graph-btn").onclick = () => switchTab("logic");
}

async function openReport() {
  const win = window.open("", "_blank");
  try {
    const res = await fetch(`/v2/report?provider=${st.provider.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request()) });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
    }
    const html = await res.text();
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    if (win) win.location = url; else download("gecaa-bericht.html", html, "text/html");
  } catch (err) {
    if (win) win.close();
    flash(`Bericht fehlgeschlagen: ${err.message}`);
  }
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function flash(msg) {
  $("#assess-error").textContent = msg;
  $("#assess-error").hidden = false;
  setTimeout(() => ($("#assess-error").hidden = true), 4000);
}

// ------------------------------------------------------------------ logic tab

function renderLogicTab() {
  const L_ = st.logic;
  $("#logic-head").innerHTML = `<h2 style="font-size:16px;margin:0">${esc(L_.title)}</h2>
    <p class="hint">${esc(L_.guideline)} · v${esc(L_.version)} · Status: ${L_.review_status === "done" ? "auditiert" : "Entwurf"}<br>Bewertungseinheit: ${esc(L_.evaluation_unit)}</p>`;
  const cite = (basis) => (basis || []).filter((b) => st.provisions[b]).map((b) => esc(st.provisions[b].citation)).join(", ");
  $("#logic-tables").innerHTML = `
    <h3 style="font-size:14px">Parameter</h3><table class="logic"><tr><th>ID</th><th>Bedeutung</th><th>Wert</th><th>Quelle</th></tr>
    ${L_.parameters.map((p) => `<tr><td>${esc(p.id)}</td><td>${esc(p.label)}${p.derived ? ` <span class="tagd">abgeleitet</span>` : ""}</td><td>${esc(num(p.value))} ${esc(p.unit && p.unit !== "-" ? p.unit : "")}</td><td>${cite(p.basis)}</td></tr>`).join("")}</table>
    <h3 style="font-size:14px">Berechnungen</h3><table class="logic"><tr><th>ID</th><th>Bedeutung</th></tr>
    ${L_.calculations.map((c) => `<tr><td>${esc(c.id)}</td><td>${esc(c.label)}${c.derived ? ` <span class="tagd">abgeleitet</span>` : ""}</td></tr>`).join("")}</table>
    <h3 style="font-size:14px">Prüfungen</h3><table class="logic"><tr><th>ID</th><th>Frage</th><th>Eingaben</th><th>Quelle</th></tr>
    ${L_.checks.map((c) => `<tr><td>${esc(c.id)}</td><td>${esc(c.question)}${c.derived ? ` <span class="tagd">abgeleitet</span>` : ""}</td><td>${esc(c.inputs.join(", "))}</td><td>${cite(c.basis)}</td></tr>`).join("")}</table>
    <h3 style="font-size:14px">Nicht codiert</h3><ul class="gaps">${L_.not_coded.map((n) => `<li><strong>${esc(n.topic)}</strong> – ${esc(n.reason)}</li>`).join("")}</ul>`;
}

function initGraph() {
  if (st.cy || typeof cytoscape === "undefined") return;
  const L_ = st.logic, els = [], ids = new Set();
  const node = (id, label, kind) => { if (!ids.has(id)) { ids.add(id); els.push({ data: { id, label, kind } }); } };
  const edge = (a, b) => els.push({ data: { id: `${a}->${b}`, source: a, target: b } });
  L_.inputs.forEach((i) => node(i.abbr, `${i.abbr}\n${i.label}`, "input"));
  L_.calculations.forEach((c) => { node(c.id, `${c.id}\n${c.label}`, "calc"); [c.a, c.b].forEach((x) => { if (ids.has(x)) edge(x, c.id); }); });
  node("RESULT", "Ergebnis des Standorts", "result");
  L_.checks.forEach((c) => {
    node(c.id, `${c.id}\n${c.question}`, "check");
    const srcs = new Set([...c.inputs.filter((i) => !L_.calculations.some((k) => k.id === c.operand && [k.a, k.b].includes(i))), c.operand]);
    srcs.forEach((s) => { if (ids.has(s)) edge(s, c.id); });
    if (c.low && ids.has(c.low) && c.low.startsWith("CA")) edge(c.low, c.id);
    edge(c.id, "RESULT");
  });
  st.cy = cytoscape({
    container: $("#graph"), elements: els, wheelSensitivity: 0.2,
    layout: { name: "breadthfirst", directed: true, roots: L_.inputs.map((i) => i.abbr), spacingFactor: 1.15, padding: 8 },
    style: [
      { selector: "node", style: { label: "data(label)", "text-wrap": "wrap", "text-max-width": 120, "font-size": 9.5, "text-valign": "center", "text-halign": "center",
        shape: "round-rectangle", width: 132, height: 58, "background-color": "#fff", "border-width": 1.5, "border-color": "#8aa1af", color: "#1b2730" } },
      { selector: "node[kind='input']", style: { "background-color": "#eef5f9" } },
      { selector: "node[kind='calc']", style: { "background-color": "#f4f1fa", shape: "ellipse" } },
      { selector: "node[kind='result']", style: { "border-width": 3, "border-color": "#0a5c8f", "font-weight": "bold" } },
      { selector: "node.PASSED", style: { "background-color": "#e2f2e5", "border-color": "#2b7a3d" } },
      { selector: "node.OPEN", style: { "background-color": "#fbefd4", "border-color": "#b07400" } },
      { selector: "node.BLOCKED", style: { "background-color": "#f8e1df", "border-color": "#a5312a" } },
      { selector: "node.missing", style: { "border-style": "dashed", "border-color": "#a5312a" } },
      { selector: "edge", style: { width: 1.2, "line-color": "#9fb0ba", "target-arrow-color": "#9fb0ba", "target-arrow-shape": "triangle", "curve-style": "bezier" } },
    ],
  });
  st.cy.on("tap", "node", (ev) => showNode(ev.target.id()));
  colourGraph();
}

function updateGraphUnits() {
  $("#graph-unit").innerHTML = st.result.units.map((u) => `<option value="${esc(u.id)}">${esc(u.name)} – ${esc(RESULT_DE[u.result])}</option>`).join("");
  colourGraph();
}

function colourGraph() {
  if (!st.cy || !st.result) return;
  const u = st.result.units.find((x) => x.id === $("#graph-unit").value) || st.result.units[0];
  st.cy.nodes().removeClass("PASSED OPEN BLOCKED missing");
  u.checks.forEach((c) => st.cy.getElementById(c.id).addClass(c.result));
  u.inputs.forEach((i) => { if (i.value == null) st.cy.getElementById(i.abbr).addClass("missing"); });
  st.cy.getElementById("RESULT").addClass(u.result);
}

function showNode(id) {
  const L_ = st.logic;
  const chk = L_.checks.find((c) => c.id === id), inp = L_.inputs.find((i) => i.abbr === id), calc = L_.calculations.find((c) => c.id === id);
  const u = st.result && (st.result.units.find((x) => x.id === $("#graph-unit").value) || st.result.units[0]);
  let h = "";
  if (chk) {
    const r = u && u.checks.find((c) => c.id === id);
    h = `<h3>${esc(chk.id)} ${esc(chk.question)}</h3>${r ? `<p>${sym(r.result)} ${esc(r.text)}${r.value_display != null ? `<br><small>Ist: ${esc(r.value_display)}${r.threshold_display ? ` · Soll: ${esc(r.threshold_display)}` : ""}</small>` : ""}</p>` : ""}
      <p><small>Regel: ${esc(chk.rule)} · Thema: ${esc(chk.theme)}${chk.responsible ? ` · Zuständig: ${esc(chk.responsible)}` : ""}</small></p>`;
  } else if (inp) {
    const v = u && u.inputs.find((i) => i.abbr === id);
    h = `<h3>${esc(inp.abbr)} · ${esc(inp.label)}</h3>${v ? `<p>${v.value == null ? "<strong>fehlt</strong> – daraus folgt OFFEN" : `Wert: ${esc(v.value)} ${esc(v.unit || "")} · <span class="dq ${dqClass(v.quality)}">${esc(v.quality)}</span><br><small>${esc(v.source || "")}${v.note ? ` · ${esc(v.note)}` : ""}</small>`}</p>` : ""}<p><small>${esc(inp.help || "")}</small></p>`;
  } else if (calc) {
    h = `<h3>${esc(calc.id)} · ${esc(calc.label)}</h3>`;
  }
  $("#node-detail").innerHTML = h;
}

// ------------------------------------------------------------------ sources tab

function renderSources() {
  const p = st.provider || st.providers[0];
  $("#data-sources").innerHTML = `<h2 style="font-size:16px">Geodaten</h2>${st.providers.map((pr) => `<div class="prov"><strong>${esc(pr.label)}</strong>
    <ul class="gaps">${pr.sources.map((s) => `<li>${esc(s.label)} – ${esc(s.provider)}, Lizenz ${esc(s.licence)}${s.note ? ` · ${esc(s.note)}` : ""}</li>`).join("")}</ul></div>`).join("")}`;
  $("#provisions").innerHTML = `<h2 style="font-size:16px">Rechtsgrundlagen und Regelwerke</h2>` + Object.values(st.provisions).map((pv) => `<div class="prov" id="prov-${esc(pv.id)}">
    <strong>${esc(pv.citation)}</strong>${pv.review_status === "draft" ? '<span class="pill">Entwurf</span>' : '<span class="pill ok">geprüft</span>'}
    <div style="margin-top:4px">${esc(pv.summary_de || pv.summary)}</div>
    ${pv.text ? `<blockquote>${esc(pv.text)}</blockquote>` : ""}
    <div class="meta">${pv.source_uri ? `<a href="${esc(pv.source_uri)}" target="_blank" rel="noopener">${esc(pv.source_uri)}</a>` : ""}${pv.provenance && pv.provenance.content_sha256 ? ` · abgerufen ${esc(new Date(pv.provenance.retrieved_at).toLocaleString("de-DE"))}, sha256 ${esc(pv.provenance.content_sha256.slice(0, 12))}` : ""}</div></div>`).join("");
}

async function refreshKnowledge() {
  const btn = $("#refresh-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> NeuRIS wird abgefragt…`;
  const STATUS = { new: "neu", changed: "geändert", unchanged: "unverändert", failed: "fehlgeschlagen", skipped: "übersprungen" };
  try {
    const res = await api("/v1/knowledge/refresh", { method: "POST" });
    $("#refresh-result").innerHTML = res.map((r) => `<div class="refresh-row"><span class="s-${esc(r.status)}">${esc(STATUS[r.status] || r.status)}</span> · ${esc(r.citation)}<br><small>${esc(r.message)}</small></div>`).join("");
    (await api("/v1/provisions")).forEach((p) => (st.provisions[p.id] = p));
    renderSources();
  } catch (err) {
    $("#refresh-result").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Bundesrecht auf Änderungen prüfen";
  }
}

// ------------------------------------------------------------------ events and helpers

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) => (p.hidden = p.id !== `pane-${name}`));
  document.querySelector(".layout").classList.toggle("wide", name === "logic");
  document.querySelector(".panel").scrollTop = 0;
  setTimeout(() => st.map.invalidateSize(), 50);
  if (name === "logic") { initGraph(); if (st.cy) { st.cy.resize(); st.cy.fit(undefined, 8); } }
  document.dispatchEvent(new CustomEvent("gecaa:tab", { detail: name }));
}

function initEvents() {
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#search-btn").addEventListener("click", search);
  $("#search").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });
  $("#provider").addEventListener("change", (e) => switchProvider(e.target.value));
  $("#parcel-btn").addEventListener("click", () => {
    st.parcelMode = !st.parcelMode;
    $("#parcel-btn").classList.toggle("active", st.parcelMode);
  });
  $("#draw-btn").addEventListener("click", () => {
    if (st.map.pm.globalDrawModeEnabled()) { st.map.pm.disableDraw(); $("#draw-btn").classList.remove("active"); return; }
    st.map.pm.enableDraw("Polygon", { finishOn: "dblclick" });
    $("#draw-btn").classList.add("active");
  });
  $("#site-form").addEventListener("submit", assess);
  $("#units").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-remove]"); if (rm) return removeUnit(rm.dataset.remove);
    const act = e.target.closest("[data-activate]"); if (act) return setActive(st.units.find((u) => u.id === act.dataset.activate));
  });
  $("#units").addEventListener("change", (e) => {
    const t = e.target;
    const u = st.units.find((x) => x.id === (t.dataset.name || t.dataset.paved || t.dataset.scope));
    if (!u) return;
    if (t.dataset.name) u.name = t.value || u.name;
    else if (t.dataset.paved) u.paved = t.value;
    else if (t.dataset.abbr) u.inputs[t.dataset.abbr] = t.value;
    renderUnits();
  });
  $("#layers").addEventListener("change", (e) => {
    const id = e.target.dataset.layer;
    const layer = st.wms[id] || st.demoOverlays[id];
    if (!layer) return;
    e.target.checked ? layer.addTo(st.map) : st.map.removeLayer(layer);
  });
  $("#graph-unit").addEventListener("change", colourGraph);
  $("#refresh-btn").addEventListener("click", refreshKnowledge);
  document.body.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".cite");
    if (!btn) return;
    switchTab("sources");
    const card = document.getElementById(`prov-${btn.dataset.prov}`);
    if (card) { card.scrollIntoView({ block: "center" }); card.classList.add("flash"); setTimeout(() => card.classList.remove("flash"), 1600); }
  });
}

function geodesicArea(geom) {
  const R = 6378137, rad = Math.PI / 180;
  const ring = (r) => { let a = 0; for (let i = 0; i < r.length - 1; i++) { const [x1, y1] = r[i], [x2, y2] = r[i + 1]; a += (x2 - x1) * rad * (2 + Math.sin(y1 * rad) + Math.sin(y2 * rad)); } return Math.abs(a * R * R / 2); };
  const polys = geom.type === "MultiPolygon" ? geom.coordinates : [geom.coordinates];
  return polys.reduce((s, rings) => s + ring(rings[0]) - rings.slice(1).reduce((h, r) => h + ring(r), 0), 0);
}

init();
