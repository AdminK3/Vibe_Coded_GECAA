// SPDX-License-Identifier: Apache-2.0
// GECAA web app: pick or draw a parcel, evaluate a measure, inspect the reasoning and the sources.
"use strict";

const BLUEPRINT_DEFAULT = "mulden_rigolen_system_nrw";
const GOOD_STATES = new Set(["suitable", "sufficient", "flat", "none", "none_known", "adequate", "permissible", "feasible"]);
const BAD_STATES = new Set(["too_low", "insufficient", "steep", "zone_i_ii", "suspected", "inadequate", "unsuitable", "not_permissible", "not_feasible"]);
const CERTAINTY_TEXT = {
  clear: "The evidence points clearly in one direction.",
  leaning: "The evidence leans one way, but some inputs are uncertain.",
  open: "The result is open: the available data does not decide it. See what would settle it.",
};

const state = {
  dataset: null, layers: [], blueprints: [], blueprint: null, provisions: {},
  parcel: null, lastEval: null, map: null, overlays: {}, drawn: null, parcelsLayer: null,
  highlight: null, cy: null, samples: {},
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (p) => `${Math.round(p * 100)} %`;
const tone = (s) => (GOOD_STATES.has(s) ? "good" : BAD_STATES.has(s) ? "bad" : "warn");
const TONE_COLOUR = { good: "#2b7a3d", warn: "#b07400", bad: "#a5312a" };

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = isJson ? body.detail : body;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

// ---------------------------------------------------------------- start-up

async function init() {
  try {
    const [dataset, layers, blueprints, provisions] = await Promise.all([
      api("/v1/dataset"), api("/v1/layers"), api("/v1/blueprints"), api("/v1/provisions"),
    ]);
    state.dataset = dataset;
    state.layers = layers;
    state.blueprints = blueprints;
    provisions.forEach((p) => (state.provisions[p.id] = p));
    state.blueprint = await api(`/v1/blueprints/${BLUEPRINT_DEFAULT}`);
  } catch (err) {
    document.body.insertAdjacentHTML("afterbegin", `<p class="error">Could not load GECAA: ${esc(err.message)}</p>`);
    return;
  }
  renderBadges();
  renderMeasures();
  initMap();
  await initLayers();
  renderKnowledge();
  initTabs();
  initForm();
}

function renderBadges() {
  const b = [];
  if (state.dataset.synthetic) b.push(`<span class="badge" title="The site data is invented demo data">Synthetic demo data</span>`);
  if (state.blueprint.review_status === "draft") b.push(`<span class="badge" title="Rules not yet reviewed by a regulatory expert">Draft rules · screening only</span>`);
  b.push(`<span class="badge info">Decision support: the authority decides</span>`);
  $("#badges").innerHTML = b.join("");
}

function renderMeasures() {
  $("#measure").innerHTML = state.blueprints
    .map((bp) => `<option value="${esc(bp.id)}">${esc(bp.title)}</option>`).join("");
}

// ---------------------------------------------------------------- map

function initMap() {
  const [w, s, e, n] = state.dataset.bounds;
  const map = L.map("map", { zoomControl: true });
  state.map = map;
  // Official German basemap from the Federal Agency for Cartography and Geodesy (BKG), CC BY 4.0.
  const basemap = (style) => L.tileLayer(
    `https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_${style}/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png`,
    { maxZoom: 20, maxNativeZoom: 18, attribution: '&copy; <a href="https://basemap.de" target="_blank" rel="noopener">basemap.de</a> / BKG (CC BY 4.0)' });
  const colour = basemap("farbe").addTo(map);
  L.control.layers({ "basemap.de colour": colour, "basemap.de grey": basemap("grau") }, null, { position: "bottomleft" }).addTo(map);
  const extent = L.latLngBounds([s, w], [n, e]);
  L.rectangle(extent, { color: "#0a5c8f", weight: 2, dashArray: "6 6", fill: false, interactive: false }).addTo(map);
  map.fitBounds(extent.pad(0.15));

  map.pm.setGlobalOptions({ snappable: true, allowSelfIntersection: false });
  map.on("pm:create", (ev) => {
    if (state.drawn) map.removeLayer(state.drawn);
    state.drawn = ev.layer;
    ev.layer.setStyle(parcelStyle("selected"));
    ev.layer.on("pm:edit", () => selectParcel({ id: "drawn", name: "Drawn parcel", geometry: ev.layer.toGeoJSON().geometry, properties: {} }, ev.layer));
    ev.layer.pm.enable({ allowSelfIntersection: false });
    $("#draw-btn").classList.remove("active");
    $("#draw-btn").textContent = "Draw a new parcel";
    selectParcel({ id: "drawn", name: "Drawn parcel", geometry: ev.layer.toGeoJSON().geometry, properties: {} }, ev.layer);
  });
  $("#draw-btn").addEventListener("click", () => {
    const active = map.pm.globalDrawModeEnabled();
    if (active) { map.pm.disableDraw(); $("#draw-btn").classList.remove("active"); return; }
    map.pm.enableDraw("Polygon", { finishOn: "dblclick" });
    $("#draw-btn").classList.add("active");
  });
}

function parcelStyle(kind, verdict) {
  if (kind === "selected") {
    const c = verdict ? TONE_COLOUR[tone(verdict)] : "#0a5c8f";
    return { color: c, weight: 3, fillColor: c, fillOpacity: verdict ? 0.35 : 0.15 };
  }
  return { color: "#334", weight: 1.5, dashArray: "4 3", fillColor: "#fff", fillOpacity: 0.15 };
}

async function initLayers() {
  const panel = [`<h3>Map layers</h3>`];
  for (const layer of state.layers) {
    const on = layer.id === "zones" || layer.id === "parcels";
    if (layer.type === "raster") {
      state.overlays[layer.id] = L.imageOverlay(layer.url, [[layer.bounds[1], layer.bounds[0]], [layer.bounds[3], layer.bounds[2]]], { opacity: 0.85 });
    } else {
      const gj = await api(layer.url);
      if (layer.id === "zones") {
        state.overlays.zones = L.geoJSON(gj, {
          style: (f) => {
            const z = String(f.properties.zone || "").toUpperCase();
            const c = z.startsWith("III") ? "#b07400" : "#a5312a";
            return { color: c, weight: 1.5, fillColor: c, fillOpacity: z.startsWith("III") ? 0.12 : 0.25 };
          },
          onEachFeature: (f, l) => l.bindTooltip(esc(f.properties.name || `Zone ${f.properties.zone}`)),
          interactive: true,
        });
      } else if (layer.id === "parcels") {
        state.overlays.parcels = L.geoJSON(gj, {
          style: () => parcelStyle("sample"),
          onEachFeature: (f, l) => {
            state.samples[f.properties.id] = { feature: f, layer: l };
            l.bindTooltip(esc(f.properties.name), { sticky: true });
            l.on("click", () => chooseSample(f.properties.id));
          },
        });
        $("#sample-select").insertAdjacentHTML("beforeend", gj.features
          .map((f) => `<option value="${esc(f.properties.id)}">${esc(f.properties.name)}</option>`).join(""));
      }
    }
    if (on) state.overlays[layer.id].addTo(state.map);
    const legend = layer.legend.map(([label, colour]) => `<div><span class="swatch" style="background:${colour}"></span>${esc(label)}</div>`).join("");
    panel.push(`<label><input type="checkbox" data-layer="${esc(layer.id)}" ${on ? "checked" : ""}> ${esc(layer.label)}</label>${legend ? `<div class="legend">${legend}</div>` : ""}`);
  }
  panel.push(`<div class="src">Dashed frame: area covered by the site data (${esc(state.dataset.name)}).</div>`);
  $("#layers").innerHTML = panel.join("");
  $("#layers").addEventListener("change", (ev) => {
    const id = ev.target.dataset.layer;
    if (!id) return;
    if (ev.target.checked) state.overlays[id].addTo(state.map); else state.map.removeLayer(state.overlays[id]);
    if (state.highlight) state.highlight.bringToFront();
  });
  $("#sample-select").addEventListener("change", (ev) => ev.target.value && chooseSample(ev.target.value));
}

function chooseSample(id) {
  const s = state.samples[id];
  if (!s) return;
  $("#sample-select").value = id;
  const p = s.feature.properties;
  if (p.connected_area_m2 != null) $("#connected").value = p.connected_area_m2;
  if (p.available_area_m2 != null) $("#available").value = p.available_area_m2;
  const c = document.querySelector(`input[name=contamination][value="${p.contamination || "unknown"}"]`);
  if (c) c.checked = true;
  selectParcel({ id, name: p.name, geometry: s.feature.geometry, properties: p }, s.layer);
  state.map.fitBounds(s.layer.getBounds().pad(1.2));
}

function selectParcel(parcel, layer) {
  if (state.highlight && state.highlight !== layer && state.highlight !== state.drawn) state.highlight.setStyle(parcelStyle("sample"));
  state.highlight = layer;
  layer.setStyle(parcelStyle("selected"));
  layer.bringToFront();
  state.parcel = parcel;
  const area = Math.round(geodesicArea(parcel.geometry));
  $("#site-card").hidden = false;
  $("#site-card").innerHTML = `<strong>${esc(parcel.name)}</strong>${area.toLocaleString("de-DE")} m² · ${parcel.id === "drawn" ? "drag the corners to adjust" : "sample parcel"}`;
  $("#evaluate-btn").disabled = false;
  $("#result").hidden = true;
}

// Area of a WGS84 polygon in m² (spherical approximation, accurate enough for parcels).
function geodesicArea(geom) {
  const R = 6378137, rad = Math.PI / 180;
  const ringArea = (ring) => {
    let a = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const [x1, y1] = ring[i], [x2, y2] = ring[i + 1];
      a += (x2 - x1) * rad * (2 + Math.sin(y1 * rad) + Math.sin(y2 * rad));
    }
    return Math.abs(a * R * R / 2);
  };
  const polys = geom.type === "MultiPolygon" ? geom.coordinates : [geom.coordinates];
  return polys.reduce((sum, rings) => sum + ringArea(rings[0]) - rings.slice(1).reduce((h, r) => h + ringArea(r), 0), 0);
}

// ---------------------------------------------------------------- evaluation

function initForm() {
  $("#site-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.parcel) return;
    const btn = $("#evaluate-btn");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Evaluating…`;
    $("#eval-error").hidden = true;
    try {
      const out = await api("/v1/evaluations", { method: "POST", body: JSON.stringify(requestBody()) });
      state.lastEval = out.evaluation;
      renderResult(out);
      state.highlight.setStyle(parcelStyle("selected", out.evaluation.verdict));
      updateGraph();
    } catch (err) {
      $("#eval-error").textContent = err.message.includes("outside")
        ? "This parcel lies outside the site data. Draw it inside the dashed frame on the map."
        : `Evaluation failed: ${err.message}`;
      $("#eval-error").hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Evaluate";
    }
  });
}

function requestBody() {
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    blueprint_id: $("#measure").value || BLUEPRINT_DEFAULT,
    parcel: { type: "Feature", properties: { id: state.parcel.id, name: state.parcel.name }, geometry: state.parcel.geometry },
    context: {
      connected_area_m2: num("#connected"),
      available_area_m2: num("#available"),
      contamination: document.querySelector("input[name=contamination]:checked").value,
    },
  };
}

function bars(dist, labels, highlight, colour) {
  return `<div class="bars">${Object.entries(dist).map(([s, p]) =>
    `<div class="bar"${s === highlight ? ' style="font-weight:600"' : ""}><span>${esc(cap(labels[s] || s))}</span><span class="track"><span class="fill" style="width:${(p * 100).toFixed(1)}%;background:${colour || TONE_COLOUR[tone(s)]}"></span></span><span class="pct">${pct(p)}</span></div>`).join("")}</div>`;
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function cites(basis) {
  const items = (basis || []).filter((b) => state.provisions[b]);
  if (!items.length) return "";
  return `<div class="cites">${items.map((b) => `<button class="cite" type="button" data-prov="${esc(b)}" title="${esc(state.provisions[b].title)}">${esc(state.provisions[b].citation)}</button>`).join("")}</div>`;
}

function renderResult({ evaluation: e, parcel }) {
  const outcome = e.findings.find((f) => !f.is_input && Object.keys(f.distribution).join() === Object.keys(e.distribution).join());
  const best = Object.keys(e.distribution)[0];
  const derived = e.findings.filter((f) => !f.is_input).reverse();
  const inputs = e.findings.filter((f) => f.is_input);
  const finding = (f) => `<div class="finding">
      <div class="head"><span>${esc(f.label)}</span><span class="state-pill ${tone(f.most_likely)}">${esc(cap(f.most_likely_label))} · ${pct(f.distribution[f.most_likely])}</span></div>
      <p>${esc(f.explanation)}</p>
      ${f.assumption ? `<p class="assume">Assumption: ${esc(f.assumption)}</p>` : ""}
      ${cites(f.basis)}
    </div>`;
  const checks = e.next_checks.map((c) => `<div class="check"><strong>${esc(c.label)}</strong><br>${esc(c.advice)}
      ${bars(Object.fromEntries(Object.entries(c.outcome_if).map(([k, d]) => [`if ${k}`, d[best]])), {}, null, TONE_COLOUR[tone(best)])}
      <small>Chance of "${esc(outcome.state_labels[best] || best)}" in each case.</small></div>`).join("");
  $("#result").innerHTML = `
    <div class="verdict ${tone(e.verdict)}">
      <div class="eyebrow">Result for ${esc(parcel.name)}</div>
      <div class="big">${esc(cap(e.verdict_label))} · ${pct(e.confidence)}</div>
      <div>${esc(CERTAINTY_TEXT[e.certainty])}</div>
      ${bars(e.distribution, outcome.state_labels, e.verdict)}
    </div>
    ${checks ? `<div class="block"><h3>What would settle it</h3>${checks}</div>` : ""}
    <div class="block"><h3>How the result was reached</h3>${derived.map(finding).join("")}</div>
    <div class="block"><h3>Site evidence</h3>${inputs.map(finding).join("")}</div>
    <div class="block"><h3>Not checked in this version</h3><ul class="gaps">${e.gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul></div>
    <div class="actions">
      <button class="btn secondary" type="button" id="report-btn">Open printable report</button>
      <button class="btn secondary" type="button" id="json-btn">Download data (JSON)</button>
      <button class="btn secondary" type="button" id="graph-btn">Show in network</button>
    </div>`;
  $("#result").hidden = false;
  $("#result").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  $("#report-btn").addEventListener("click", openReport);
  $("#json-btn").addEventListener("click", () => download(`gecaa-${parcel.id}.json`, JSON.stringify({ parcel, evaluation: e }, null, 2), "application/json"));
  $("#graph-btn").addEventListener("click", () => switchTab("reasoning"));
}

async function openReport() {
  const win = window.open("", "_blank");
  try {
    const res = await fetch("/v1/evaluations/report", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestBody()) });
    const html = await res.text();
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    if (win) win.location = url; else download("gecaa-report.html", html, "text/html");
  } catch (err) {
    if (win) win.close();
    alert(`Report failed: ${err.message}`);
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

// ---------------------------------------------------------------- reasoning graph

function initGraph() {
  if (state.cy || typeof cytoscape === "undefined") return;
  const bp = state.blueprint;
  const elements = [];
  bp.nodes.forEach((n) => elements.push({ data: { id: n.id, label: n.label, kind: n.parents.length ? (n.id === bp.outcome ? "outcome" : "rule") : "input" } }));
  bp.nodes.forEach((n) => n.parents.forEach((p) => elements.push({ data: { id: `${p}->${n.id}`, source: p, target: n.id } })));
  state.cy = cytoscape({
    container: $("#graph"),
    elements,
    layout: { name: "breadthfirst", directed: true, roots: bp.nodes.filter((n) => !n.parents.length).map((n) => n.id), spacingFactor: 0.95, padding: 10 },
    style: [
      { selector: "node", style: { label: "data(label)", "text-wrap": "wrap", "text-max-width": 128, "font-size": 11, "text-valign": "center", "text-halign": "center",
        shape: "round-rectangle", width: 140, height: 54, "background-color": "#ffffff", "border-width": 1.5, "border-color": "#8aa1af", color: "#1b2730" } },
      { selector: "node[kind = 'input']", style: { "background-color": "#eef5f9" } },
      { selector: "node[kind = 'outcome']", style: { "border-width": 3, "border-color": "#0a5c8f", "font-weight": "bold" } },
      { selector: "node.good", style: { "background-color": "#e2f2e5", "border-color": "#2b7a3d" } },
      { selector: "node.warn", style: { "background-color": "#fbefd4", "border-color": "#b07400" } },
      { selector: "node.bad", style: { "background-color": "#f8e1df", "border-color": "#a5312a" } },
      { selector: "node:selected", style: { "overlay-opacity": 0.12, "overlay-color": "#0a5c8f" } },
      { selector: "edge", style: { width: 1.5, "line-color": "#9fb0ba", "target-arrow-color": "#9fb0ba", "target-arrow-shape": "triangle", "curve-style": "bezier" } },
    ],
    wheelSensitivity: 0.2,
  });
  state.cy.on("tap", "node", (ev) => showNode(ev.target.id()));
  updateGraph();
}

function updateGraph() {
  if (!state.cy || !state.lastEval) return;
  state.lastEval.findings.forEach((f) => {
    const node = state.cy.getElementById(f.node_id);
    node.removeClass("good warn bad").addClass(tone(f.most_likely));
    node.data("label", `${f.label}\n${f.most_likely_label} · ${pct(f.distribution[f.most_likely])}`);
  });
}

function showNode(id) {
  const n = state.blueprint.nodes.find((x) => x.id === id);
  const label = (nodeId, s) => {
    const node = state.blueprint.nodes.find((x) => x.id === nodeId);
    return (node.state_labels && node.state_labels[s]) || s.replace(/_/g, " ");
  };
  const finding = state.lastEval && state.lastEval.findings.find((f) => f.node_id === id);
  let html = `<h3 style="margin:0 0 4px">${esc(n.label)}</h3>`;
  if (finding) html += bars(finding.distribution, finding.state_labels, finding.most_likely);
  if (!n.parents.length) {
    html += `<p>Input from <code>${esc(n.evidence_source)}</code>.</p>${n.assumption ? `<p class="assume">Assumption: ${esc(n.assumption)}</p>` : ""}${cites(n.basis)}`;
  } else {
    html += `<p>Rule table: the first row whose conditions match decides.</p><table><tr><th>When</th><th>Then</th></tr>${n.rules.map((r) => {
      const when = Object.entries(r.when).map(([p, v]) => `${esc(state.blueprint.nodes.find((x) => x.id === p).label)}: ${esc([].concat(v).map((s) => label(p, s)).join(" or "))}`).join("<br>") || "otherwise";
      const then = typeof r.then === "string" ? esc(label(id, r.then)) : Object.entries(r.then).map(([s, p]) => `${esc(label(id, s))} ${pct(p)}`).join("<br>") + " <em>(expert judgement)</em>";
      return `<tr><td>${when}</td><td><strong>${then}</strong><br>${esc(r.explanation)}${cites(r.basis)}</td></tr>`;
    }).join("")}</table>`;
  }
  $("#node-detail").innerHTML = html;
}

// ---------------------------------------------------------------- knowledge

const KIND_LABEL = { federal_law: "Federal law", state_law: "State law (NRW)", ordinance: "Ordinances", technical_standard: "Technical standards (licensed, cited only)", guidance: "Working assumptions" };

function renderKnowledge() {
  const groups = {};
  Object.values(state.provisions).forEach((p) => (groups[p.kind] = groups[p.kind] || []).push(p));
  $("#provisions").innerHTML = Object.keys(KIND_LABEL).filter((k) => groups[k]).map((k) => `
    <h3 style="font-size:14px;margin:18px 0 8px">${esc(KIND_LABEL[k])}</h3>
    ${groups[k].map((p) => `<div class="prov" id="prov-${esc(p.id)}">
      <strong>${esc(p.citation)}</strong>${p.review_status === "draft" ? '<span class="pill">draft</span>' : `<span class="pill ok">${esc(p.review_status.replace("_", " "))}</span>`}
      <div style="color:var(--muted)">${esc(p.title)}</div>
      <div style="margin-top:4px">${esc(p.summary)}</div>
      ${p.text ? `<blockquote>${esc(p.text)}</blockquote>` : ""}
      <div class="meta">${p.source_uri ? `<a href="${esc(p.source_uri)}" target="_blank" rel="noopener">${esc(p.source_uri)}</a>` : ""}${p.eli ? ` · ELI ${esc(p.eli)}` : ""}
        ${p.provenance && p.provenance.content_sha256 ? ` · fetched ${esc(new Date(p.provenance.retrieved_at).toLocaleString("de-DE"))} via ${esc(p.provenance.retrieval_method)}, sha256 ${esc(p.provenance.content_sha256.slice(0, 12))}` : " · text not yet fetched"}
        ${p.licence_note ? ` · ${esc(p.licence_note)}` : ""}</div>
    </div>`).join("")}`).join("");
}

async function refreshKnowledge() {
  const btn = $("#refresh-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> Asking NeuRIS…`;
  $("#refresh-result").innerHTML = "";
  try {
    const results = await api("/v1/knowledge/refresh", { method: "POST" });
    $("#refresh-result").innerHTML = results.map((r) => `<div class="refresh-row"><span class="s-${esc(r.status)}">${esc(r.status)}</span> · ${esc(r.citation)}<br><small>${esc(r.message)}</small></div>`).join("");
    (await api("/v1/provisions")).forEach((p) => (state.provisions[p.id] = p));
    renderKnowledge();
  } catch (err) {
    $("#refresh-result").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check federal law for updates";
  }
}

// ---------------------------------------------------------------- tabs and citations

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) => (p.hidden = p.id !== `pane-${name}`));
  document.querySelector(".layout").classList.toggle("wide", name === "reasoning");
  setTimeout(() => state.map && state.map.invalidateSize(), 50);
  if (name === "reasoning") {
    initGraph();
    if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 12); }
  }
}

function initTabs() {
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("#refresh-btn").addEventListener("click", refreshKnowledge);
  document.body.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".cite");
    if (!btn) return;
    switchTab("knowledge");
    const card = document.getElementById(`prov-${btn.dataset.prov}`);
    if (card) {
      card.scrollIntoView({ block: "center" });
      card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 1600);
    }
  });
}

init();
