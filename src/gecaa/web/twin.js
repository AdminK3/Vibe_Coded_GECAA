// SPDX-License-Identifier: Apache-2.0
// GECAA digital twin (CesiumJS, Apache-2.0): DGM terrain, LoD2 buildings coloured by flood depth, water depth draped
// on the terrain, scenario / measure comparison, project timeline, open street-level photos (Panoramax).
"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const Q = new URLSearchParams(location.search);
const RUN = Q.get("run"), PROJECT = Q.get("project");
const CLASSES = [[0.05, "#c6e6f7", "5–10 cm"], [0.10, "#8cc8ee", "10–30 cm"], [0.30, "#4292d6", "30–50 cm"], [0.50, "#1a5eb0", "50–100 cm"], [1.0, "#0e3480", "> 1 m"]];
const KIND = { mulde: ["Versickerungsmulde", "#2e9e6a"], gruendach: ["Gründach", "#5a9e2e"], entsiegelung: ["Entsiegelung", "#b07a2e"], baeume: ["Bäume", "#1f7a3a"] };
const S = { sc: null, rp: null, variant: "base", alpha: 0.85, layers: {}, prims: [], inst: {}, scene: null, date: null, sel: null };
let viewer;

function depthColor(d, thr) {
  if (d == null || d < Math.max(0.05, thr || 0)) return Cesium.Color.fromCssColorString("#e9ecef");
  let c = CLASSES[0][1];
  for (const [lo, col] of CLASSES) if (d >= lo) c = col;
  return Cesium.Color.fromCssColorString(c);
}
const fmtD = (d) => (d == null ? "–" : `${Math.round(d * 100)} cm`);
const eur = (v) => (v == null ? "OFFEN" : `${Math.round(v).toLocaleString("de-DE")} €`);

function terrainProvider(t) {
  const H = new Float32Array(Uint8Array.from(atob(t.heights), (c) => c.charCodeAt(0)).buffer);
  const [w, s, e, n] = t.rect, N = 32;
  const sample = (lon, lat) => {
    if (lon < w || lon > e || lat < s || lat > n) return t.min;
    const fx = ((lon - w) / (e - w)) * (t.w - 1), fy = ((n - lat) / (n - s)) * (t.h - 1);
    const x0 = Math.floor(fx), y0 = Math.floor(fy), x1 = Math.min(x0 + 1, t.w - 1), y1 = Math.min(y0 + 1, t.h - 1), ax = fx - x0, ay = fy - y0;
    const v = (x, y) => H[y * t.w + x];
    return (v(x0, y0) * (1 - ax) + v(x1, y0) * ax) * (1 - ay) + (v(x0, y1) * (1 - ax) + v(x1, y1) * ax) * ay;
  };
  const scheme = new Cesium.GeographicTilingScheme();
  return new Cesium.CustomHeightmapTerrainProvider({
    width: N, height: N, tilingScheme: scheme,
    callback(x, y, level) {
      const r = scheme.tileXYToRectangle(x, y, level), out = new Float32Array(N * N);
      const W = Cesium.Math.toDegrees(r.west), E = Cesium.Math.toDegrees(r.east), No = Cesium.Math.toDegrees(r.north), So = Cesium.Math.toDegrees(r.south);
      if (E < w || W > e || No < s || So > n) { out.fill(t.min); return out; }
      for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) out[j * N + i] = sample(W + ((E - W) * i) / (N - 1), No - ((No - So) * j) / (N - 1));
      return out;
    },
  });
}

function buildBuildings(list) {
  const inst = [], extr = [];
  const flat = (ring) => ring.flatMap((p) => p);
  for (const b of list) {
    S.inst[b.id] = [];
    if (b.surfaces) {
      b.surfaces.forEach((f, i) => {
        const id = `${b.id}|${i}|${f.t}`;
        S.inst[b.id].push(id);
        inst.push(new Cesium.GeometryInstance({
          id, geometry: new Cesium.CoplanarPolygonGeometry({
            polygonHierarchy: new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArrayHeights(flat(f.ring))),
            vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT }),
          attributes: { color: Cesium.ColorGeometryInstanceAttribute.fromColor(Cesium.Color.LIGHTGRAY) } }));
      });
    } else if (b.footprint && b.ground != null) {
      const id = `${b.id}|0|x`;
      S.inst[b.id].push(id);
      extr.push(new Cesium.GeometryInstance({
        id, geometry: new Cesium.PolygonGeometry({
          polygonHierarchy: new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(b.footprint.flat())),
          height: b.ground, extrudedHeight: b.ground + (b.height || 0.5), vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT }),
        attributes: { color: Cesium.ColorGeometryInstanceAttribute.fromColor(Cesium.Color.LIGHTGRAY) } }));
    }
  }
  for (const set of [inst, extr]) {
    if (!set.length) continue;
    const p = new Cesium.Primitive({ geometryInstances: set, appearance: new Cesium.PerInstanceColorAppearance({ translucent: false, closed: false }),
      asynchronous: true, releaseGeometryInstances: false });
    viewer.scene.primitives.add(p);
    S.prims.push(p);
  }
}

function buildingDepth(b) {
  const src = S.variant === "base" || !b.depth_m ? b.depth : b.depth_m;
  return ((src || {})[S.sc] || {})[String(S.rp)];
}
function applyColors() {
  if (!S.prims.every((p) => p.ready)) { viewer.scene.postRender.addEventListener(function once() { viewer.scene.postRender.removeEventListener(once); setTimeout(applyColors, 50); }); return; }
  for (const b of S.scene.buildings) {
    const base = depthColor(buildingDepth(b), S.scene.threshold_m);
    for (const id of S.inst[b.id]) {
      const roof = id.endsWith("|roof"), sel = S.sel === b.id;
      const c = sel ? Cesium.Color.fromCssColorString("#ffcf33") : roof ? Cesium.Color.lerp(base, Cesium.Color.WHITE, 0.25, new Cesium.Color()) : base;
      for (const p of S.prims) {
        const a = p.getGeometryInstanceAttributes(id);
        if (a) { a.color = Cesium.ColorGeometryInstanceAttribute.toValue(c); break; }
      }
    }
  }
}

async function water() {
  for (const l of Object.values(S.layers)) viewer.imageryLayers.remove(l, true);
  S.layers = {};
  const [[s, w], [n, e]] = S.scene.bounds, rect = Cesium.Rectangle.fromDegrees(w, s, e, n);
  const add = async (variant, dir) => {
    const url = `/v3/risk/${encodeURIComponent(RUN)}/layer?name=${encodeURIComponent(`depth|${S.sc}|${S.rp}|${variant}`)}`;
    const prov = await Cesium.SingleTileImageryProvider.fromUrl(url, { rectangle: rect });
    const l = viewer.imageryLayers.addImageryProvider(prov);
    l.alpha = S.alpha;
    if (dir) l.splitDirection = dir;
    S.layers[variant + (dir || "")] = l;
  };
  if (S.variant === "split") {
    await add("base", Cesium.SplitDirection.LEFT);
    await add("measure", Cesium.SplitDirection.RIGHT);
  } else await add(S.variant);
  $("#split").hidden = S.variant !== "split";
}

function legend() {
  $("#legend").innerHTML = CLASSES.map(([, c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join("")
    + `<span><i style="background:#e9ecef;border:1px solid #bbb"></i>trocken</span>`
    + [...new Set(S.scene.measures.map((m) => m.kind))].filter((k) => KIND[k]).map((k) => KIND[k]).map(([l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join("");
}

function measures() {
  for (const m of S.scene.measures) {
    if (!m.geometry || m.geometry.type !== "Polygon") continue;
    const col = Cesium.Color.fromCssColorString((KIND[m.kind] || ["", "#2e9e6a"])[1]);
    m.entity = viewer.entities.add({ name: m.name, polygon: { hierarchy: Cesium.Cartesian3.fromDegreesArray(m.geometry.coordinates[0].flat()),
      material: col.withAlpha(0.55), classificationType: Cesium.ClassificationType.TERRAIN } });
  }
}

// ------------------------------------------------------------------ timeline (project phases)
const day = 864e5;
function timeline() {
  const t = S.scene.timeline;
  if (!t) return;
  const ph = t.phases.filter((p) => p.start && p.end);
  if (!ph.length) { $("#tl").hidden = false; $("#tl-t").textContent = "Im Projekt sind noch keine Termine eingetragen – Zeitachse OFFEN."; $("#tl-r").disabled = true; return; }
  const t0 = Math.min(...ph.map((p) => Date.parse(p.start))), t1 = Math.max(...ph.map((p) => Date.parse(p.end)));
  const um = t.phases.find((p) => p.key === "umsetzung");
  const done = um && um.end ? Date.parse(um.end) : null;
  $("#tl").hidden = false;
  $("#tl-name").textContent = t.name || "";
  const r = $("#tl-r");
  r.max = Math.max(1, Math.round((t1 - t0) / day));
  const show = () => {
    const d = t0 + Number(r.value) * day, cur = ph.filter((p) => Date.parse(p.start) <= d && d <= Date.parse(p.end) + day).map((p) => p.label);
    const built = done != null && d >= done;
    $("#tl-t").innerHTML = `<strong>${new Date(d).toLocaleDateString("de-DE")}</strong> · ${esc(cur.join(", ") || "keine Phase")}<br>`
      + (done == null ? "Ende der Umsetzung OFFEN." : built ? "Maßnahmen umgesetzt – Wasser mit Maßnahmen." : `Maßnahmen ab ${new Date(done).toLocaleDateString("de-DE")} wirksam.`);
    for (const m of S.scene.measures) if (m.entity) m.entity.polygon.material = Cesium.Color.fromCssColorString((KIND[m.kind] || ["", "#2e9e6a"])[1]).withAlpha(built ? 0.6 : 0.18);
    if (S.scene.has_measure && S.variant !== "split") setVariant(built ? "measure" : "base", true);
  };
  r.oninput = show;
  let timer = null;
  $("#play").onclick = () => {
    if (timer) { clearInterval(timer); timer = null; $("#play").textContent = "▶"; return; }
    $("#play").textContent = "❚❚";
    if (Number(r.value) >= Number(r.max)) r.value = 0;
    timer = setInterval(() => { r.value = Math.min(Number(r.max), Number(r.value) + Math.max(1, Math.round(r.max / 80))); show(); if (Number(r.value) >= Number(r.max)) $("#play").click(); }, 120);
  };
  show();
}

function setVariant(v, quiet) {
  if (S.variant === v) return;
  S.variant = v;
  document.querySelectorAll("#variant button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v)));
  water(); applyColors();
  if (!quiet && S.sel) info(S.sel);
}

// ------------------------------------------------------------------ building info + street photos
async function info(bid) {
  const b = S.scene.buildings.find((x) => x.id === bid);
  if (!b) return;
  S.sel = bid; applyColors();
  const d0 = ((b.depth || {})[S.sc] || {})[String(S.rp)], d1 = b.depth_m ? ((b.depth_m || {})[S.sc] || {})[String(S.rp)] : null;
  $("#info").hidden = false;
  $("#info").innerHTML = `<h4>${esc(b.label || b.id)}</h4><table>
    <tr><td>Nutzung</td><td>${esc(b.cls || "–")}</td></tr>
    <tr><td>Wassertiefe ohne Maßnahmen</td><td>${fmtD(d0)}</td></tr>
    ${b.depth_m ? `<tr><td>… mit Maßnahmen</td><td>${fmtD(d1)}</td></tr>` : ""}
    <tr><td>Jahresschaden (Untergrenze)</td><td>${eur(b.eai)}</td></tr>
    ${b.persons != null ? `<tr><td>Bewohner (Zensus, abgeleitet)</td><td>≈ ${Math.round(b.persons)}</td></tr>` : ""}
    <tr><td>Höhe</td><td>${b.height ? `${b.height.toLocaleString("de-DE")} m` : "OFFEN"}</td></tr>
    <tr><td>Geometrie</td><td>${esc(b.source)}</td></tr></table>
    <div id="gsv"></div>
    <h3>Straßenbilder (offen)</h3><div id="street" class="note">Suche offene Straßenbilder …</div>`;
  const pts = (b.surfaces ? b.surfaces.flatMap((f) => f.ring) : b.footprint) || [];
  const lon = pts.reduce((a, p) => a + p[0], 0) / pts.length, lat = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  googleSV(lon, lat, bid);
  try {
    const r = await (await fetch(`/v6/street?lon=${lon}&lat=${lat}&radius=80`)).json();
    if (S.sel !== bid) return;
    if (!r.images || !r.images.length) { $("#street").innerHTML = r.error ? esc(r.error) : "Keine offenen Straßenbilder im Umkreis von 80 m. Eigene Bilder lassen sich bei Panoramax hochladen."; return; }
    $("#street").outerHTML = `<div class="strip">${r.images.slice(0, 6).map((im, i) => `<button type="button" data-i="${i}" title="${esc(im.date)}, ${im.distance_m} m${im.facing ? ", Blick zum Gebäude" : ""}">
      <img src="${esc(im.thumb)}" alt="Straßenbild ${esc(im.date)}" loading="lazy"><small>${im.distance_m} m${im.facing ? " ◎" : ""}</small></button>`).join("")}</div>
      <p class="note">◎ = Blick Richtung Gebäude · Quelle: ${esc(r.sources.join(", "))}</p>`;
    document.querySelectorAll(".strip button").forEach((btn) => btn.addEventListener("click", () => {
      const im = r.images[Number(btn.dataset.i)];
      $("#m-img").src = im.image || im.hd || im.thumb;
      $("#m-cap").innerHTML = `${esc(im.source)} · ${esc(im.date)} · © ${esc(im.author || "unbekannt")} · ${esc(im.license || "")} · <a href="${esc(im.viewer)}" target="_blank" rel="noopener noreferrer">im Viewer öffnen</a>`;
      $("#modal").hidden = false;
    }));
  } catch (e) { if (S.sel === bid) $("#street").textContent = "Straßenbilder nicht erreichbar."; }
}

async function googleSV(lon, lat, bid) {
  try {
    const g = await (await fetch(`/v6/streetview?lon=${lon}&lat=${lat}`)).json();
    if (!g.enabled || S.sel !== bid) return;
    $("#gsv").innerHTML = g.available
      ? `<h3>Google Street View <small>${esc(g.pano_date || "")} · ${g.distance_m} m</small></h3>
         <iframe class="gsv" src="${esc(g.embed_url)}" loading="lazy" allowfullscreen referrerpolicy="no-referrer-when-downgrade" title="Google Street View"></iframe>
         <p class="note">${esc(g.note)} <a href="${esc(g.maps_url)}" target="_blank" rel="noopener noreferrer">In Google Maps öffnen</a></p>`
      : `<p class="note">Google Street View: kein Panorama im Umkreis (${esc(g.status || "")}).</p>`;
  } catch (e) { /* optional source */ }
}

// ------------------------------------------------------------------ boot
async function boot() {
  if (!RUN) { $("#sub").textContent = "Kein Lauf angegeben – bitte aus dem Starkregen-Ergebnis öffnen."; return; }
  let sc;
  try {
    const r = await fetch(`/v6/twin/${encodeURIComponent(RUN)}/scene${PROJECT ? `?project=${encodeURIComponent(PROJECT)}` : ""}`);
    if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
    sc = await r.json();
  } catch (e) { $("#sub").textContent = `Fehler: ${e.message}`; return; }
  S.scene = sc; S.sc = sc.scenarios[0]; S.rp = sc.rps[sc.rps.length - 1];
  Cesium.Ion.defaultAccessToken = "";
  viewer = new Cesium.Viewer("cv", {
    baseLayer: new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
      url: "https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_farbe/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png",
      maximumLevel: 18, credit: "© basemap.de / BKG (CC BY 4.0)" })),
    terrainProvider: terrainProvider(sc.terrain), animation: false, timeline: false, geocoder: false, homeButton: false,
    sceneModePicker: false, baseLayerPicker: false, navigationHelpButton: false, fullscreenButton: false, infoBox: false,
    selectionIndicator: false, skyAtmosphere: false, requestRenderMode: false });
  viewer.scene.globe.depthTestAgainstTerrain = true;
  viewer.scene.globe.enableLighting = false;
  $("#sub").textContent = `${sc.buildings.length} Gebäude · ${sc.lod2_count} mit LoD2-Dächern`;
  $("#sc").innerHTML = sc.scenarios.map((x) => `<option>${esc(x)}</option>`).join("");
  $("#rp").innerHTML = sc.rps.map((x) => `<option value="${x}" ${x === S.rp ? "selected" : ""}>${x}-jährlich</option>`).join("");
  $("#sc").onchange = (e) => { S.sc = e.target.value; water(); applyColors(); if (S.sel) info(S.sel); };
  $("#rp").onchange = (e) => { S.rp = Number(e.target.value); water(); applyColors(); if (S.sel) info(S.sel); };
  document.querySelectorAll("#variant button").forEach((b) => {
    if (b.dataset.v !== "base" && !sc.has_measure) { b.disabled = true; b.title = "Keine Maßnahmen in diesem Lauf"; }
    b.addEventListener("click", () => setVariant(b.dataset.v));
  });
  $("#alpha").oninput = (e) => { S.alpha = Number(e.target.value); Object.values(S.layers).forEach((l) => { l.alpha = S.alpha; }); };
  $("#src").textContent = sc.sources.join(" · ");
  legend();
  buildBuildings(sc.buildings);
  measures();
  await water();
  applyColors();
  timeline();
  const [w, s, e, n] = sc.terrain.rect;
  viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees((w + e) / 2, s - (n - s) * 0.35, sc.terrain.max + Math.max(150, (n - s) * 111000 * 0.6)),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-30), roll: 0 } });
  const h = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  h.setInputAction((m) => { const p = viewer.scene.pick(m.position); if (p && typeof p.id === "string") info(p.id.split("|")[0]); }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
  // split slider
  viewer.scene.splitPosition = 0.5;
  let drag = false;
  $("#split i").addEventListener("pointerdown", () => { drag = true; });
  window.addEventListener("pointerup", () => { drag = false; });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const x = Math.min(0.95, Math.max(0.05, e.clientX / window.innerWidth));
    viewer.scene.splitPosition = x; $("#split").style.left = `${x * 100}%`;
  });
  $("#m-close").onclick = () => { $("#modal").hidden = true; };
  $("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });
}
boot();
