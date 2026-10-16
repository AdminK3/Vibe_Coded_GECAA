// SPDX-License-Identifier: Apache-2.0
// Small dependency-free SVG charts for GECAA (line/scatter with log x-axis, bars with trend line).
"use strict";

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs = {}, text) => {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    if (text != null) e.textContent = text;
    return e;
  };
  const niceMax = (v) => {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
  };

  function frame(w, h, title) {
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, class: "chart", role: "img", "aria-label": title || "Diagramm" });
    if (title) svg.appendChild(el("title", {}, title));
    return svg;
  }

  // series: [{name, color, dashed, points: [[x, y], ...]}]
  function line(target, { series, xLog = false, xTicks, xLabel = "", yLabel = "", yFmt = (v) => v, title, h = 210 }) {
    const w = 440, m = { l: 58, r: 12, t: 12, b: 38 };
    const svg = frame(w, h, title);
    const xs = series.flatMap((s) => s.points.map((p) => p[0]));
    const ys = series.flatMap((s) => s.points.map((p) => p[1]));
    const fx = xLog ? Math.log10 : (v) => v;
    const x0 = fx(Math.min(...xs)), x1 = fx(Math.max(...xs));
    const yMax = niceMax(Math.max(...ys, 0) * 1.05);
    const X = (v) => m.l + ((fx(v) - x0) / ((x1 - x0) || 1)) * (w - m.l - m.r);
    const Y = (v) => h - m.b - (v / yMax) * (h - m.t - m.b);
    for (let i = 0; i <= 4; i++) {
      const v = (yMax * i) / 4;
      svg.appendChild(el("line", { x1: m.l, x2: w - m.r, y1: Y(v), y2: Y(v), class: i ? "grid" : "axis" }));
      svg.appendChild(el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, yFmt(v)));
    }
    (xTicks || [...new Set(xs)]).forEach((t) => {
      svg.appendChild(el("text", { x: X(t), y: h - m.b + 16, "text-anchor": "middle" }, t));
    });
    svg.appendChild(el("text", { x: (m.l + w - m.r) / 2, y: h - 4, "text-anchor": "middle" }, xLabel));
    svg.appendChild(el("text", { x: 12, y: (h - m.b) / 2, transform: `rotate(-90 12 ${(h - m.b) / 2})`, "text-anchor": "middle" }, yLabel));
    series.forEach((s) => {
      const pts = s.points.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");
      svg.appendChild(el("polyline", { points: pts, fill: "none", stroke: s.color, "stroke-width": 2.4,
        "stroke-dasharray": s.dashed ? "6 4" : "none", "stroke-linejoin": "round" }));
      s.points.forEach(([x, y]) => {
        const c = el("circle", { cx: X(x), cy: Y(y), r: 3.6, fill: s.color });
        c.appendChild(el("title", {}, `${s.name}: ${x} → ${yFmt(y)}`));
        svg.appendChild(c);
      });
    });
    const legend = document.createElement("div");
    legend.className = "chart-legend";
    legend.innerHTML = series.map((s) => `<span class="${s.dashed ? "dash" : ""}" style="--c:${s.color}">${s.name}</span>`).join("");
    target.replaceChildren(svg, legend);
  }

  function bars(target, { years, values, trend, title, yLabel = "", h = 190, color = "#ec6b3a", highlightFrom }) {
    const w = 440, m = { l: 36, r: 10, t: 10, b: 30 };
    const svg = frame(w, h, title);
    const yMax = niceMax(Math.max(...values, 1) * 1.08);
    const n = values.length, bw = (w - m.l - m.r) / n;
    const Y = (v) => h - m.b - (v / yMax) * (h - m.t - m.b);
    const X = (i) => m.l + i * bw;
    for (let i = 0; i <= 4; i++) {
      const v = (yMax * i) / 4;
      svg.appendChild(el("line", { x1: m.l, x2: w - m.r, y1: Y(v), y2: Y(v), class: i ? "grid" : "axis" }));
      svg.appendChild(el("text", { x: m.l - 5, y: Y(v) + 4, "text-anchor": "end" }, Math.round(v)));
    }
    values.forEach((v, i) => {
      const r = el("rect", { x: X(i) + 0.6, y: Y(v), width: Math.max(bw - 1.2, 0.8), height: Y(0) - Y(v), rx: 1,
        fill: color, opacity: highlightFrom && years[i] < highlightFrom ? 0.45 : 0.9 });
      r.appendChild(el("title", {}, `${years[i]}: ${v}`));
      svg.appendChild(r);
      if (years[i] % 10 === 0) svg.appendChild(el("text", { x: X(i) + bw / 2, y: h - m.b + 15, "text-anchor": "middle" }, years[i]));
    });
    if (trend) {
      const [[ya, va], [yb, vb]] = trend;
      const xi = (yr) => X(years.indexOf(yr)) + bw / 2;
      svg.appendChild(el("line", { x1: xi(ya), y1: Y(va), x2: xi(yb), y2: Y(vb), stroke: "currentColor", "stroke-width": 2.2,
        "stroke-dasharray": "6 4", style: "color: var(--ink)" }));
    }
    svg.appendChild(el("text", { x: 4, y: 12 }, yLabel));
    target.replaceChildren(svg);
  }

  return { line, bars };
})();
