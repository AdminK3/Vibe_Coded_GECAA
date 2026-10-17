"use strict";
const esc3 = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// SPDX-License-Identifier: Apache-2.0
// GECAA 3D view (MapLibre GL JS, BSD-3): basemap.de raster, buildings as fill-extrusion coloured by depth class,
// depth PNG as image source. Reads a run from GET /v3/risk/{run_id}/result (runs made via POST /v3/risk/rain3d).
(function () {
  const $ = (s) => document.querySelector(s);
  const CLASSES = [[0.05, "#d9d9d9", "< 5 cm"], [0.10, "#c6e6f7", "5–10 cm"], [0.30, "#8cc8ee", "10–30 cm"],
    [0.50, "#4292d6", "30–50 cm"], [1.00, "#1a5eb0", "50–100 cm"], [Infinity, "#0e3480", "> 1 m"]];
  const color = (d) => (CLASSES.find((c) => (d || 0) < c[0]) || CLASSES[CLASSES.length - 1])[1];
  let result = null;

  const map = new maplibregl.Map({
    container: "map", center: [7.0, 51.25], zoom: 15, pitch: 60, bearing: -20, maxPitch: 85,
    style: {
      version: 8,
      sources: { basemap: { type: "raster", tileSize: 256, attribution: "© basemap.de / BKG",
        tiles: ["https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_farbe/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png"] } },
      layers: [{ id: "basemap", type: "raster", source: "basemap" }],
    },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

  const status = (t) => { $("#status").textContent = t; };
  $("#legend").innerHTML = "<b>Wassertiefe am Gebäude</b><br>" +
    CLASSES.map((c) => `<span><i style="background:${c[1]}"></i>${c[2]}</span>`).join("<br>");

  function height(b) {
    const p = b.properties || b;
    if (p.height_m > 0) return p.height_m;
    if (p.storeys > 0) return p.storeys * 3;
    return 6;
  }

  function layerKey() { return $("#layer").value; }
  function parseKey(k) { const [, sc, rp, v] = k.split("|"); return { sc, rp, v }; }

  function buildingsGeojson() {
    const { sc, rp, v } = parseKey(layerKey());
    return { type: "FeatureCollection", features: (result.buildings || []).filter((b) => b.geometry).map((b) => {
      const bySc = (v === "measure" ? b.depth_measure_by_scenario : b.depth_by_scenario) || {};
      const d = ((bySc[sc] || b.depth || {})[rp]) || 0;
      return { type: "Feature", geometry: b.geometry,
        properties: { id: b.id, label: b.label || b.id, depth: d, color: color(d), h: height(b), eai: b.eai_eur } };
    }) };
  }

  function draw() {
    if (!result) return;
    const [[s, w], [n, e]] = result.bounds;
    const url = `/v3/risk/${result.run_id}/layer?name=${encodeURIComponent(layerKey())}`;
    const coords = [[w, n], [e, n], [e, s], [w, s]];
    if (map.getSource("depth")) map.getSource("depth").updateImage({ url, coordinates: coords });
    else {
      map.addSource("depth", { type: "image", url, coordinates: coords });
      map.addLayer({ id: "depth", type: "raster", source: "depth", paint: { "raster-opacity": 0.8 } });
    }
    map.setLayoutProperty("depth", "visibility", $("#showdepth").checked ? "visible" : "none");
    const gj = buildingsGeojson();
    if (map.getSource("bld")) map.getSource("bld").setData(gj);
    else {
      map.addSource("bld", { type: "geojson", data: gj });
      map.addLayer({ id: "bld", type: "fill-extrusion", source: "bld", paint: {
        "fill-extrusion-color": ["get", "color"], "fill-extrusion-height": ["get", "h"],
        "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.9 } });
      map.on("click", "bld", (ev) => {
        const p = ev.features[0].properties;
        new maplibregl.Popup().setLngLat(ev.lngLat).setHTML(
          `<b>${esc3(p.label)}</b><br>Wassertiefe: ${Number(p.depth).toLocaleString("de-DE")} m<br>` +
          `Jahresschaden (Untergrenze/Größenordnung): ${Number(p.eai || 0).toLocaleString("de-DE")} €`).addTo(map);
      });
    }
  }

  function show(res) {
    result = res;
    const sel = $("#layer");
    sel.innerHTML = (res.layers || []).filter((l) => l.startsWith("depth|")).map((l) => {
      const { sc, rp, v } = parseKey(l);
      return `<option value="${l}">${sc}, ${rp}-jährlich${v === "measure" ? ", mit Maßnahme" : ""}</option>`;
    }).join("");
    const top = `depth|${res.scenarios[0]}|${Math.max(...res.rps)}|base`;
    if ([...sel.options].some((o) => o.value === top)) sel.value = top;
    const [[s, w], [n, e]] = res.bounds;
    map.fitBounds([[w, s], [e, n]], { pitch: +$("#pitch").value, bearing: +$("#bearing").value, padding: 40 });
    draw();
    status(`Lauf ${res.run_id}: ${(res.buildings || []).length} Gebäude.`);
    try { localStorage.setItem("gecaa.3d.run", res.run_id); } catch (e) { /* storage blocked */ }
    history.replaceState(null, "", `?run=${res.run_id}`);
  }

  async function load(id) {
    status("Lade …");
    const r = await fetch(`/v3/risk/${encodeURIComponent(id)}/result`);
    if (!r.ok) { status((await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`); return; }
    show(await r.json());
  }

  async function compute() {
    const c = map.getCenter();
    status("Berechne Starkregen (kann bis zu einer Minute dauern) …");
    const q = new URLSearchParams(location.search).get("provider");
    const r = await fetch(`/v3/risk/rain3d${q ? `?provider=${encodeURIComponent(q)}` : ""}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lon: c.lng, lat: c.lat }) });
    if (!r.ok) { status((await r.json().catch(() => ({}))).detail || `Fehler ${r.status}`); return; }
    show(await r.json());
  }

  $("#load").onclick = () => $("#run").value && load($("#run").value.trim());
  $("#new").onclick = compute;
  $("#layer").onchange = draw;
  $("#showdepth").onchange = draw;
  $("#pitch").oninput = (e) => map.setPitch(+e.target.value);
  $("#bearing").oninput = (e) => map.setBearing(+e.target.value);
  map.on("pitchend", () => { $("#pitch").value = Math.round(map.getPitch()); });
  map.on("rotateend", () => { $("#bearing").value = Math.round(map.getBearing()); });

  map.on("load", () => {
    const p = new URLSearchParams(location.search);
    let id = p.get("run");
    if (!id) { try { id = localStorage.getItem("gecaa.3d.run"); } catch (e) { id = null; } }
    const lon = parseFloat(p.get("lon")), lat = parseFloat(p.get("lat"));
    if (isFinite(lon) && isFinite(lat)) map.jumpTo({ center: [lon, lat] });
    if (id) { $("#run").value = id; load(id); } else status("Lauf-ID eingeben oder neu berechnen.");
  });
})();
