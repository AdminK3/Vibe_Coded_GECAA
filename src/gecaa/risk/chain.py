# SPDX-License-Identifier: Apache-2.0
"""Heavy-rain risk chain for one site: terrain -> rain -> runoff -> ponding -> buildings -> damage -> measure.

Every result carries the list of assumptions it rests on (id, German text, source, kind), so the screening
character stays visible: this is a sink and flow-path analysis, not a hydrodynamic flood simulation.
"""

from __future__ import annotations

import math
import time
import uuid
from collections import OrderedDict
from dataclasses import replace as _dc_replace
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from ..geodata.base import to_utm, to_wgs
from .kostra import DURATIONS, RPS as KOSTRA_RPS
from . import damage, engine_client, measures as mlib, raster_png, scenario, sewer
from .hydrology import d8_receivers, ponding, priority_flood

BUFFER_M = 100.0             # extra terrain around the box so nearby water reaches the site
BUILDING_RAISE_M = 0.5       # buildings as obstacles (DGM has no buildings); above this, water enters doors/gateways
RING_M = 2.0                 # water depth at a building = maximum within 2 m around its footprint
DEMO_RAIN = {1: 20.0, 2: 24.0, 3: 27.0, 5: 30.0, 10: 35.0, 20: 39.0, 30: 42.0, 50: 46.0, 100: 51.0}

# Runoff coefficients during heavy rain (share of rain that runs off). Oriented on DIN 1986-100, Tab. 9
# (Spitzenabflussbeiwerte); green areas set higher than for design rain because soils saturate in heavy rain.
RUNOFF = [
    (("gebäude",), 1.0, "Dachflächen"),
    (("straße", "weg", "platz", "bahn", "flugverkehr", "verkehr", "parkplatz"), 0.9, "Verkehrsflächen"),
    (("industrie", "gewerbe", "handel", "gemischt", "wohnbau", "öffentlich", "sondernutzung", "versorgung",
      "entsorgung", "halde", "tagebau", "bergbau"), 0.6, "bebaute Grundstücke (teilversiegelt)"),
    (("gewässer", "fluss", "see", "hafen", "graben", "kanal"), 1.0, "Wasserflächen"),
    (("wald", "gehölz", "heide", "moor", "sumpf", "unland"), 0.15, "Wald und Gehölz"),
    (("landwirtschaft", "acker", "grünland", "garten", "grünanlage", "sport", "freizeit", "erholung",
      "friedhof", "park"), 0.3, "Grünflächen und Gärten"),
]
DEFAULT_RUNOFF = 0.5

_RUNS: "OrderedDict[str, dict]" = OrderedDict()
MAX_RUNS = 12
MAX_CELLS_SIDE = 600         # at most 600 x 600 cells: coarser resolution for large areas (runtime)


class MeasureIn(BaseModel):
    id: str = Field(max_length=40)
    name: str = Field("Mulde", max_length=80)
    geometry: dict
    roof_ids: list[str] = Field(default_factory=list, max_length=200)
    depth_m: float = Field(0.3, gt=0, le=1.0)          # usable storage depth of the trough (Einstau)
    kind: Literal["mulde", "gruendach", "entsiegelung", "baeume"] = "mulde"
    variant: Literal["extensiv", "intensiv"] = "extensiv"   # green roof only
    trees: int | None = Field(None, ge=1, le=2000)          # trees only: count (canopy = count × 50 m²)
    pit_m3_per_tree: float = Field(0.0, ge=0, le=50)        # trees only: tree-pit storage per tree

    @field_validator("geometry")
    @classmethod
    def _poly(cls, g: dict) -> dict:
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            raise ValueError("Maßnahme muss eine Fläche sein")
        a = to_utm(g).area
        if not 0.5 <= a <= 20000:
            raise ValueError("Maßnahmenfläche muss zwischen 0,5 und 20.000 m² liegen")
        return g


class RainRequest(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    box_m: float = Field(400, ge=100, le=800)
    res_m: float = Field(2.0, ge=1.0, le=5.0)
    duration_min: int = Field(60, description=f"KOSTRA-Dauerstufe in Minuten: {DURATIONS}")
    rps: list[int] = Field(default_factory=lambda: [10, 30, 100], min_length=1, max_length=5)
    scenarios: list[str] = Field(default_factory=lambda: ["heute", "+1 K", "+2 K"], min_length=1, max_length=3)
    sewer_mm: float = Field(0.0, ge=0, le=50)          # sewer_mode "pauschal": rain the sewer takes away (mm)
    sewer_mode: Literal["none", "pauschal", "bemessung"] | None = None   # None: bemessung if KOSTRA, else none
    sewer_design_rp: Literal[1, 2, 3, 5, 10] = 3       # design return period of the sewer (DWA-A 118 / EN 752)
    swmm_inp_b64: str | None = Field(None, max_length=2_800_000)   # SWMM .inp, base64, max 2 MB
    engine: Literal["screening", "2d"] = "screening"
    basement_share: float = Field(0.0, ge=0, le=1)     # share of footprint with a basement
    threshold_m: float = Field(0.10, ge=0, le=0.5)     # water below this height does not enter (door sills)
    rain_override_mm: dict[int, float] | None = None
    measures: list[MeasureIn] = Field(default_factory=list, max_length=20)
    cost_eur: float | None = Field(None, gt=0, le=1e9)
    prefer_worker: bool = True

    @field_validator("duration_min")
    @classmethod
    def _dur(cls, v):
        if v not in DURATIONS:
            raise ValueError(f"Dauerstufe muss eine KOSTRA-Stufe sein: {DURATIONS}")
        return v

    @field_validator("rain_override_mm")
    @classmethod
    def _rain(cls, v):
        if v is None:
            return v
        if len(v) > len(KOSTRA_RPS) or any(k not in KOSTRA_RPS for k in v):
            raise ValueError(f"Regenhöhen nur für Wiederkehrintervalle {KOSTRA_RPS}")
        if any(not (math.isfinite(x) and 0 < x <= 300) for x in v.values()):
            raise ValueError("Regenhöhen müssen zwischen 0 und 300 mm liegen")
        return v

    @field_validator("rps")
    @classmethod
    def _rps(cls, v):
        if any(r not in (1, 2, 3, 5, 10, 20, 30, 50, 100) for r in v):
            raise ValueError("Wiederkehrintervalle: 1, 2, 3, 5, 10, 20, 30, 50, 100 Jahre")
        return sorted(set(v))

    @field_validator("scenarios")
    @classmethod
    def _sc(cls, v):
        if any(s not in scenario.SCENARIOS for s in v):
            raise ValueError(f"Szenarien: {', '.join(scenario.SCENARIOS)}")
        return v


def _assumptions(req: RainRequest, rain_source: str, landuse_known: bool, edge_share: float,
                 ctx: dict | None = None) -> list[dict]:
    ctx = ctx or {}
    a = [
        {"id": "A1", "art": "Vereinfachung", "quelle": "GECAA",
         "text": "Senken- und Fließweganalyse: Wasser füllt Geländesenken am Ende eines Blockregens (Volumenbilanz); "
                 "auf Fließwegen gilt die Fließtiefe des Spitzenabflusses (rationale Methode, Manning-Strickler, "
                 "Fließbreite = Rasterweite). Keine 2D-Strömungssimulation, keine Versickerung während des Ereignisses."},
        {"id": "A12", "art": "Vereinfachung", "quelle": "CLIMADA-Konvention",
         "text": f"Erwarteter Jahresschaden als Untergrenze: Ereignisse zwischen den Wiederkehrintervallen "
                 f"{', '.join(map(str, req.rps))} Jahre erhalten den Schaden des häufigeren Ereignisses; "
                 f"Ereignisse seltener als {min(req.rps)}-jährlich zählen nicht."},
        {"id": "A11", "art": "Annahme", "quelle": "Hydraulik-Lehrbuchwerte",
         "text": "Rauheit nach Manning n: versiegelt 0,016 · teilversiegelt 0,025 · Grün 0,05 s/m^(1/3)."},
        {"id": "A2", "art": "Annahme", "quelle": "GECAA",
         "text": f"Gebäude als Hindernis um {BUILDING_RAISE_M:g} m erhöht (das Geländemodell DGM1 enthält keine Gebäude); "
                 "höheres Wasser dringt durch Türen und Tore ein oder fließt durch Durchfahrten ab – Innenhöfe laufen "
                 f"daher nicht unbegrenzt voll. Überdachungen und sonstige Bauwerke sind kein Hindernis. "
                 f"Wassertiefe am Gebäude = größte Tiefe im {RING_M:g}-m-Streifen um den Grundriss."},
        _sewer_assumption(req, ctx),
        {"id": "A4", "art": "Normwert" if landuse_known else "Annahme", "quelle": "DIN 1986-100, Tab. 9 (angelehnt)",
         "text": "Abflussbeiwerte: Dach 1,0 · Verkehr 0,9 · teilversiegelte Grundstücke 0,6 · Grün 0,3 · Wald 0,15"
                 + ("" if landuse_known else f" – Nutzung unbekannt, einheitlich {DEFAULT_RUNOFF:g} außerhalb von Gebäuden")},
        {"id": "A5", "art": "Messwert" if "KOSTRA" in rain_source else "Annahme", "quelle": rain_source,
         "text": f"Bemessungsregen Dauerstufe {req.duration_min} min, als Blockregen ohne zeitlichen Verlauf."},
        {"id": "A6", "art": "Annahme", "quelle": scenario.SOURCE_CC,
         "text": "Klimaszenarien +1 K/+2 K als Sensitivität (Regen × 1,07 bzw. × 1,14), keine regionale Projektion."},
        {"id": "A7", "art": "Normwert", "quelle": damage.SOURCE_JRC,
         "text": "Schaden = Schadensfunktion(Wassertiefe) × Grundfläche × Höchstschaden (Gebäude + Inhalt, "
                 f"EU-Werte 2010 × {damage.CPI_FRAC:.3f} Preisanpassung, CLIMAAX). Nur ein Geschoss – Größenordnung."},
        {"id": "A10", "art": "Annahme", "quelle": "Eingabe",
         "text": f"Schwellenhöhe {req.threshold_m * 100:g} cm: Wasser bis zu dieser Höhe dringt nicht ins Gebäude ein "
                 "(Türschwellen, Sockel); die Schadensfunktion wird auf die Tiefe darüber angewendet."},
        *[mlib.ASSUMPTIONS[k] for k in mlib.KINDS if any(m.kind == k for m in req.measures)],
        {"id": "A9", "art": "Vereinfachung", "quelle": "GECAA",
         "text": f"Ausschnitt {req.box_m:g} m + {BUFFER_M:g} m Rand; Wasser von außerhalb fließt nicht zu."
                 + (f" Achtung: an {edge_share:.0%} des Randes steigt das Gelände nach außen an – "
                    "das Einzugsgebiet reicht vermutlich über den Ausschnitt hinaus." if edge_share > 0.3 else "")},
    ]
    if req.basement_share > 0:
        a.append({"id": "A16", "art": "Annahme", "quelle": f"Eingabe; {damage.SOURCE_JRC}",
                  "text": f"Keller unter {req.basement_share:.0%} der Grundfläche: dort Schaden nach derselben "
                          "JRC-Funktion bei Wassertiefe + 2,5 m (Kellergeschoss), sobald Wasser die Schwelle "
                          "übersteigt. Erdgeschossschaden unverändert (CLIMAAX)."})
    if ctx.get("engine_2d"):
        e = ctx["engine_2d"]
        a.append({"id": "A17", "art": "Vereinfachung", "quelle": f"{e.get('engine')} {e.get('version', '')}".strip(),
                  "text": "2D-Hydraulik: Flachwassergleichungen auf dem Raster (Gelände + Gebäude als Hindernis), "
                          "Blockregen mit Abflussbeiwert je Zelle, Manning-Rauheit wie A11; Ereignistiefe = "
                          "größte Wassertiefe der Simulation statt Senken-/Fließweganalyse (A1)."})
    if ctx.get("swmm"):
        a.append({"id": "A18", "art": "Modell", "quelle": "EPA SWMM (pyswmm), Eingabe-.inp",
                  "text": f"Überstau aus dem Kanalmodell: {ctx['swmm']['nodes']} Schächte im Raster mit zusammen "
                          f"{ctx['swmm']['m3']:.0f} m³ als zusätzliche Oberflächenquelle in ihrer Rasterzelle. "
                          "Koordinaten der .inp werden als EPSG:25832 (ETRS89/UTM 32N) angenommen."})
    return a


def _sewer_assumption(req: RainRequest, ctx: dict) -> dict:
    mode = ctx.get("sewer_mode", "none")
    if mode == "pauschal":
        return {"id": "A3", "art": "Annahme", "quelle": "Eingabe",
                "text": f"Kanalnetz nimmt pauschal {req.sewer_mm:g} mm des Regens auf (alle Flächen)."}
    if mode == "bemessung":
        return {"id": "A3", "art": "Normwert (angelehnt)", "quelle": "DWA-A 118 / DIN EN 752; KOSTRA-DWD",
                "text": f"Kanalnetz bemessen auf den {req.sewer_design_rp}-jährlichen Regen gleicher Dauer "
                        f"({ctx.get('design_mm', 0):g} mm, typische Bemessungshäufigkeit für Wohngebiete): auf "
                        "versiegelten Flächen (Abflussbeiwert ≥ 0,6) wirkt nur der Regen darüber; "
                        "Grünflächen unverändert. Klimaszenarien ändern die Kanalkapazität nicht."}
    return {"id": "A3", "art": "Vereinfachung", "quelle": "Eingabe",
            "text": "Kanalnetz nicht berücksichtigt (ungünstiger Fall)."}


def _resample(a: np.ndarray, src_res: float, res: float) -> tuple[np.ndarray, float]:
    k = max(1, math.ceil(res / src_res - 1e-9))
    if k == 1:
        return a, src_res
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    b = a[:h, :w].reshape(h // k, k, w // k, k)
    return np.nanmean(b, axis=(1, 3)), src_res * k


def _edge_inflow_share(dem: np.ndarray) -> float:
    """Share of edge cells where the terrain rises towards the outside (water would come from outside)."""
    inner = [(dem[0, 1:-1], dem[1, 1:-1]), (dem[-1, 1:-1], dem[-2, 1:-1]),
             (dem[1:-1, 0], dem[1:-1, 1]), (dem[1:-1, -1], dem[1:-1, -2])]
    rising = sum(int((e > i + 0.05).sum()) for e, i in inner)
    total = sum(len(e) for e, _ in inner)
    return rising / max(total, 1)


def run(provider, req: RainRequest, kostra=None) -> dict:
    if req.engine == "2d" and len(req.scenarios) * len(req.rps) > 3:
        raise ValueError("2D-Simulation: höchstens 3 Ereignisse je Lauf (z. B. ein Klimaszenario mit 3 Jährlichkeiten)")
    if req.cost_eur and not req.measures:
        raise ValueError("Kosten angegeben, aber keine Maßnahme eingezeichnet")
    t0 = time.time()
    steps = []

    def step(name):
        steps.append({"step": name, "s": round(time.time() - t0, 2)})

    cx, cy = to_utm({"type": "Point", "coordinates": [req.lon, req.lat]}).coords[0]
    half = req.box_m / 2 + BUFFER_M
    bbox = (cx - half, cy - half, cx + half, cy + half)
    inner = box(cx - req.box_m / 2, cy - req.box_m / 2, cx + req.box_m / 2, cy + req.box_m / 2)

    # 1 terrain
    dem_raw, x_left, y_top, src_res = provider.dem(bbox)
    res_wanted = max(req.res_m, (2 * half) / MAX_CELLS_SIDE)
    dem, res = _resample(dem_raw, src_res, res_wanted)
    if np.isnan(dem).all():
        raise ValueError("Geländemodell ohne Werte")
    if np.isnan(dem).any():           # data gaps: nearest valid height (no artificial hills or pits)
        from scipy import ndimage as _nd
        idx = _nd.distance_transform_edt(np.isnan(dem), return_distances=False, return_indices=True)
        dem = dem[tuple(idx)]
    if max(dem.shape) > MAX_CELLS_SIDE:
        raise ValueError("Analysegebiet zu groß für die Rasterweite")
    tr = from_origin(x_left, y_top, res, res)
    shape_rc = dem.shape
    step("Gelände (DGM)")

    # 2 buildings and land use
    blds = provider.buildings_utm(bbox)
    landuse = provider.landuse(bbox)
    coeff = np.full(shape_rc, DEFAULT_RUNOFF if not landuse else 0.3)
    if landuse:
        for words, c, _label in reversed(RUNOFF[1:]):
            geoms = [lu["geometry"] for lu in landuse
                     if any(w in (lu["nutzart"] + " " + lu["bez"]).lower() for w in words)]
            if geoms:
                m = rasterize(((g, 1) for g in geoms), out_shape=shape_rc, transform=tr, fill=0, dtype="uint8")
                coeff[m == 1] = c
    blds = [b for b in blds if not b.get("structure")]          # canopies etc.: no walls, no contents
    bld_geoms = [b["geometry"] for b in blds]
    bld_mask = (rasterize(((g, 1) for g in bld_geoms), out_shape=shape_rc, transform=tr, fill=0, dtype="uint8") == 1
                if bld_geoms else np.zeros(shape_rc, bool))
    coeff[bld_mask] = 1.0
    manning = np.where(coeff >= 0.9, 0.016, np.where(coeff >= 0.6, 0.025, 0.05))
    model = dem + np.where(bld_mask, BUILDING_RAISE_M, 0.0)
    step("Gebäude und Nutzung (ALKIS)")

    # 3 rain
    if req.rain_override_mm:
        rain = {int(k): float(v) for k, v in req.rain_override_mm.items()}
        rain_source = "Eingabe (eigene Regenhöhen)"
        kostra_info = None
    elif kostra is not None:
        kostra_info = kostra.depths(req.lon, req.lat, req.duration_min)
        rain = kostra_info["mm"]
        rain_source = f"{kostra_info['source']}, Rasterfeld {kostra_info['cell']}"
    else:
        rain, kostra_info = dict(DEMO_RAIN), None
        rain_source = "Demowerte (erfunden, nur für die Vorführung)"
    missing = [r for r in req.rps if r not in rain]
    if missing:
        raise ValueError(f"Keine Regenhöhe für Wiederkehrintervall {missing}")
    step("Bemessungsregen (KOSTRA)")

    # 4 hydrology: filled surface and flow directions once, then every event
    filled = priority_flood(model)
    recv = d8_receivers(filled, res)
    step("Senken und Fließwege")

    # measures: reduced runoff coefficient on assigned roofs and the trough itself, limited by storage
    bld_by_id = {str(b["id"]): b for b in blds}
    measure_masks = []
    for m in req.measures:
        g = to_utm(m.geometry)
        area = rasterize([(g, 1)], out_shape=shape_rc, transform=tr, fill=0, dtype="uint8") == 1
        roofs = [bld_by_id[r]["geometry"] for r in m.roof_ids if r in bld_by_id]
        roof = (rasterize(((r, 1) for r in roofs), out_shape=shape_rc, transform=tr, fill=0, dtype="uint8") == 1
                if roofs else np.zeros(shape_rc, bool))
        if m.kind == "gruendach":
            mask = roof if roofs else area & bld_mask
        elif m.kind == "mulde":
            mask = area | roof
        else:
            mask = area
        measure_masks.append((m, mask, mlib.storage_m3(m.kind, g.area, m.depth_m, m.trees, m.pit_m3_per_tree)))

    # sewer capacity
    sewer_mode = req.sewer_mode or ("bemessung" if kostra_info is not None else "none")
    design_mm = 0.0
    if sewer_mode == "bemessung":
        if req.sewer_design_rp not in rain:
            raise ValueError(f"Keine Regenhöhe für das Bemessungsintervall T={req.sewer_design_rp} a")
        design_mm = float(rain[req.sewer_design_rp])
    sewer_ctx = {"sewer_mode": sewer_mode, "design_mm": round(design_mm, 1)}

    # optional SWMM node flooding as extra surface sources (coordinates assumed EPSG:25832)
    extra_m3 = np.zeros(shape_rc)
    if req.swmm_inp_b64:
        import os as _os
        path = sewer.decode_inp(req.swmm_inp_b64)
        try:
            nodes = sewer.swmm_node_flooding(path, [(0.0, rain[req.rps[-1]] / (req.duration_min / 60.0)),
                                                    (req.duration_min * 60.0, 0.0)])
        except ImportError as exc:
            raise ValueError("SWMM-Datei angegeben, aber pyswmm ist nicht installiert (Extra 'sewer')") from exc
        finally:
            _os.unlink(path)
        n_in = 0
        for n in nodes:
            c_, r_ = int((n["x"] - x_left) // res), int((y_top - n["y"]) // res)
            if 0 <= r_ < shape_rc[0] and 0 <= c_ < shape_rc[1] and n["flood_m3"] > 0:
                extra_m3[r_, c_] += n["flood_m3"]
                n_in += 1
        sewer_ctx["swmm"] = {"nodes": n_in, "m3": float(extra_m3.sum())}

    def with_sources(c: np.ndarray, mm: float) -> np.ndarray:
        if not extra_m3.any() or mm <= 0:
            return c
        return c + extra_m3 / ((mm / 1000.0) * res * res)

    def coeff_with_measures(rain_mm: float) -> tuple[np.ndarray, list[float]]:
        c = coeff.copy()
        retained = []
        for m, mask, storage in measure_masks:
            before = float(c[mask].sum())
            mlib.apply_static(m.kind, m.variant, c, mask)
            if storage > 0:
                vol = float((rain_mm / 1000.0) * c[mask].sum() * res * res)
                keep = 0.0 if vol <= storage or vol == 0 else 1 - storage / vol
                c[mask] = c[mask] * keep
            retained.append((before - float(c[mask].sum())) * rain_mm / 1000.0 * res * res)
        return c, retained

    engine_2d = None
    if req.engine == "2d":
        try:
            from . import hydraulics_client
        except ImportError as exc:
            raise ValueError("2D-Hydraulik nicht verfügbar (hydraulics_client fehlt)") from exc

    def surface(mm_eff, c, dur):
        nonlocal engine_2d
        r = ponding(model, res, mm_eff, c, filled=filled, recv=recv, duration_s=dur, manning_n=manning)
        if req.engine == "2d":
            intensity = mm_eff / (dur / 3600.0) if dur else 0.0
            out = hydraulics_client.simulate(model, res, [intensity], c, manning, bld_mask, engine="auto",
                                             duration_s=dur, drain_s=1800.0)
            engine_2d = {"engine": out.get("engine"), "version": out.get("version")}
            r = _dc_replace(r, depth=np.asarray(out["depth_max"], dtype=float))
        return r

    events, retained_by_ev = {}, {}
    for sc in req.scenarios:
        f = scenario.factor(scenario.SCENARIOS[sc])
        for rp in req.rps:
            gross = rain[rp] * f
            mm = max(0.0, gross - req.sewer_mm) if sewer_mode == "pauschal" else gross
            c_base = sewer.effective_coeff(coeff, mm, design_mm) if sewer_mode == "bemessung" else coeff
            eff = (max(0.0, mm - design_mm) if sewer_mode == "bemessung" else mm)
            dur = req.duration_min * 60.0
            res_base = surface(mm, with_sources(c_base, mm), dur)
            ev = {"scenario": sc, "rp": rp, "rain_mm": round(gross, 1), "effective_mm": round(eff, 1),
                  "base": res_base}
            if measure_masks:
                c_m, ret = coeff_with_measures(mm)
                if sewer_mode == "bemessung":
                    c_m = sewer.effective_coeff(c_m, mm, design_mm)
                ev["measure"] = surface(mm, with_sources(c_m, mm), dur)
                retained_by_ev[(sc, rp)] = ret
            events[(sc, rp)] = ev
    sewer_ctx["engine_2d"] = engine_2d
    step("Überflutung je Ereignis")

    # 5 exposure: buildings inside the box, depth in a ring around each footprint
    curves = damage.load_curves()
    if hasattr(provider, "building_heights"):         # LoD2 heights (or ALKIS storeys x 3 m, marked)
        try:
            hmap = provider.building_heights(bbox) or {}
        except Exception:
            hmap = {}
        for b in blds:
            h = hmap.get(str(b["id"]))
            if h and h.get("height_m"):
                b["height_m"], b["height_method"] = h["height_m"], h.get("method")
    buildings, ring_idx = [], []
    for b in blds:
        g = b["geometry"]
        if not g.intersects(inner) or g.area < 10:
            continue
        ring = g.buffer(RING_M).difference(g)
        m = rasterize([(ring, 1)], out_shape=shape_rc, transform=tr, fill=0, dtype="uint8", all_touched=True)
        idx = np.flatnonzero(m.ravel())
        if not idx.size:
            continue
        cls = damage.classify(b["function"])
        c = g.centroid
        lon, lat = to_wgs(c)["coordinates"]
        buildings.append(damage.Building(id=str(b["id"]), label=b["function"] + (f", {b['address']}" if b.get("address") else ""),
                                         cls=cls, footprint_m2=round(g.area, 1),
                                         x=c.x, y=c.y, value_eur=damage.building_value(cls, g.area),
                                         geometry=to_wgs(g)))
        buildings[-1].lonlat = (lon, lat)
        h, st = b.get("height_m"), b.get("storeys")
        if st is None and h:
            st = max(1, round(float(h) / 3.0))
        buildings[-1].storeys = int(st) if st else None
        buildings[-1].height_m = float(h) if h else None
        ring_idx.append(idx)
    step("Gebäude als Exposition")

    def depth_rows(sc, variant):
        rows = []
        for idx in ring_idx:
            rows.append([float(events[(sc, rp)][variant].depth.ravel()[idx].max()) for rp in req.rps])
        return rows

    def damage_rows(rows):
        return [[max(0.0, d - req.threshold_m) for d in row] for row in rows]
    # 6 impact per scenario (CLIMADA worker or fallback)
    impacts = {}
    for sc in req.scenarios:
        payload = {"rps": req.rps, "exposures": [{"id": b.id, "lon": b.lonlat[0], "lat": b.lonlat[1],
                                                   "value": b.value_eur, "cls": b.cls} for b in buildings],
                   "impfs": {cls: {"depth": curves[cls][0].tolist(), "mdr": curves[cls][1].tolist()} for cls in curves},
                   "depth": damage_rows(depth_rows(sc, "base")),
                   "measure_depth": damage_rows(depth_rows(sc, "measure")) if measure_masks else None,
                   "cost_eur": req.cost_eur, "years": 30, "discount_rate": 0.02}
        impacts[sc] = engine_client.impact(payload, curves, buildings, prefer_worker=req.prefer_worker)
        if sc == req.scenarios[0]:
            base_payload = payload
        impacts[sc]["depth"] = depth_rows(sc, "base")
        impacts[sc]["measure_depth"] = depth_rows(sc, "measure") if measure_masks else None
        if req.basement_share > 0:
            _add_basement(impacts[sc], buildings, curves, req, payload)
    step("Schadensberechnung")

    persons = _persons(provider, bbox, buildings)

    # 7 layers (PNG) and result
    # map overlays: warp from UTM to Web Mercator so they line up exactly with the Leaflet map
    run_id = uuid.uuid4().hex[:12]
    warp, bounds = raster_png.mercator_warper(tr, shape_rc)
    layers = {"hillshade": raster_png.hillshade_png(warp(raster_png.hillshade(dem, res)), shaded=True)}
    for (sc, rp), ev in events.items():
        key = f"{sc}|{rp}"
        layers[f"depth|{key}|base"] = raster_png.depth_png(warp(ev["base"].depth))
        if "measure" in ev:
            layers[f"depth|{key}|measure"] = raster_png.depth_png(warp(ev["measure"].depth))
    top = events[(req.scenarios[0], req.rps[-1])]["base"]
    layers["flow"] = raster_png.flow_png(warp(top.flow_acc_m3))
    for (sc, rp), ev in events.items():               # raw grids for GeoTIFF export (QGIS)
        for variant in ("base", "measure"):
            if variant in ev:
                layers[f"grid|{sc}|{rp}|{variant}"] = {"array": ev[variant].depth.astype("float32"), "transform": tr}
    layers["payload"] = base_payload                  # for the uncertainty analysis of this run
    _RUNS[run_id] = layers
    while len(_RUNS) > MAX_RUNS:
        _RUNS.popitem(last=False)

    base_sc = req.scenarios[0]
    imp = impacts[base_sc]
    rp_keys = list(map(str, req.rps))
    b_out = []
    for i, b in enumerate(buildings):
        by_sc = {sc: dict(zip(rp_keys, [round(x, 2) for x in impacts[sc]["depth"][i]])) for sc in req.scenarios}
        by_sc_m = ({sc: dict(zip(rp_keys, [round(x, 2) for x in impacts[sc]["measure_depth"][i]]))
                    for sc in req.scenarios} if measure_masks else None)
        b_out.append({"id": b.id, "label": b.label, "cls": b.cls, "cls_de": damage.CLASS_DE[b.cls],
                      "footprint_m2": b.footprint_m2, "value_eur": round(b.value_eur, -2),
                      "storeys": b.storeys, "height_m": b.height_m, "persons_est": persons.get(b.id),
                      "depth": by_sc[base_sc], "depth_measure": by_sc_m[base_sc] if by_sc_m else None,
                      "depth_by_scenario": by_sc, "depth_measure_by_scenario": by_sc_m,
                      "eai_eur": round(imp["eai"].get(b.id, 0.0), 0),
                      "eai_by_scenario": {sc: round(impacts[sc]["eai"].get(b.id, 0.0), 0) for sc in req.scenarios},
                      "geometry": b.geometry})
    b_out.sort(key=lambda b: -b["eai_eur"])

    def ev_summary(ev, variant):
        r = ev[variant]
        inner_mask = rasterize([(inner, 1)], out_shape=shape_rc, transform=tr, fill=0, dtype="uint8") == 1
        wet = (r.depth >= 0.10) & inner_mask
        return {"max_depth_m": round(float(r.depth[inner_mask].max()), 2),
                "area_over_10cm_m2": round(float(wet.sum() * res * res)), "runoff_m3": round(r.runoff_m3),
                "stored_m3": round(sum(d.stored_m3 for d in r.depressions)),
                "left_grid_m3": round(r.spilled_off_grid_m3)}

    ev_out = []
    for (sc, rp), ev in events.items():
        e = {"scenario": sc, "rp": rp, "rain_mm": ev["rain_mm"], "effective_mm": ev["effective_mm"],
             "base": ev_summary(ev, "base")}
        if "measure" in ev:
            e["measure"] = ev_summary(ev, "measure")
        ev_out.append(e)

    edge_share = _edge_inflow_share(dem)
    shift = None
    if all(r in rain for r in (10, 30, 100)) or len(rain) >= 3:
        shift = [scenario.return_period_shift(rain, scenario.SCENARIOS[s], max(r for r in rain if r <= 100))
                 for s in req.scenarios if scenario.SCENARIOS[s] > 0]

    comparison = []
    if hasattr(provider, "bkg_depth") and provider.id == "nrw" and 100 in req.rps:
        ev100 = events[(base_sc, 100)]["base"].depth.ravel()
        pos = {b.id: k for k, b in enumerate(buildings)}
        for b in b_out[:8]:
            idx = ring_idx[pos[b["id"]]]
            cell = int(idx[np.argmax(ev100[idx])])
            r_, c_ = divmod(cell, shape_rc[1])
            px, py = x_left + (c_ + 0.5) * res, y_top - (r_ + 0.5) * res
            try:
                v = provider.bkg_depth(px, py)
            except Exception:  # comparison is optional
                break
            comparison.append({"id": b["id"], "label": b["label"], "gecaa_rp100": b["depth"].get("100"),
                               "bkg_agw": v.get("agw"), "bkg_extrem": v.get("extrem")})

    step("Ergebnis")
    return {
        "run_id": run_id, "bounds": bounds, "res_m": res, "grid": list(shape_rc), "center": [req.lon, req.lat],
        "box_wgs": to_wgs(inner), "rain": {"mm": rain, "source": rain_source, "duration_min": req.duration_min,
                                          "kostra": kostra_info},
        "rps": req.rps, "scenarios": req.scenarios, "events": ev_out, "threshold_m": req.threshold_m,
        "impact": {sc: {k: v for k, v in impacts[sc].items() if k not in ("depth", "measure_depth", "eai")}
                   for sc in req.scenarios},
        "buildings": b_out, "measures": [
            {"id": m.id, "name": m.name, "kind": m.kind, "variant": m.variant if m.kind == "gruendach" else None,
             "storage_m3": round(s, 1), "roofs": len(m.roof_ids),
             "retained_m3": round(retained_by_ev[(base_sc, req.rps[-1])][i], 1),
             "retained_m3_by_rp": {str(rp): round(retained_by_ev[(base_sc, rp)][i], 1) for rp in req.rps},
             "heat_note": mlib.HEAT_NOTES[m.kind]} for i, (m, _mask, s) in enumerate(measure_masks)],
        "sewer": {"mode": sewer_mode, "design_rp": req.sewer_design_rp if sewer_mode == "bemessung" else None,
                  "design_mm": sewer_ctx["design_mm"] if sewer_mode == "bemessung" else None,
                  "swmm": sewer_ctx.get("swmm")},
        "hydraulic_engine": engine_2d or {"engine": "screening", "version": "GECAA"},
        "basement_share": req.basement_share,
        "rp_shift": shift, "bkg_comparison": comparison, "assumptions": _assumptions(
            req, rain_source, bool(landuse), edge_share, sewer_ctx),
        "legend": raster_png.DEPTH_LEGEND, "steps": steps, "seconds": round(time.time() - t0, 2),
        "layers": sorted(layers),
    }


BASEMENT_EXTRA_M = 2.5


def _basement_eai(curves, buildings, rows, req) -> tuple[dict, list[float]]:
    freq = damage.band_frequencies(req.rps)          # req.rps is sorted
    eai, per_ev = {}, [0.0] * len(req.rps)
    for b, row in zip(buildings, rows):
        d = np.array([x + BASEMENT_EXTRA_M if x > 0 else 0.0 for x in row])
        dmg = damage.mdr(curves, b.cls, d) * (d > 0) * b.value_eur * req.basement_share
        eai[b.id] = float((dmg * freq).sum())
        per_ev = [p + float(v) for p, v in zip(per_ev, dmg)]
    return eai, per_ev


def _add_basement(imp: dict, buildings, curves, req, payload) -> None:
    """Basement damage: same JRC curve at depth + 2.5 m for basement_share of the footprint (assumption A16)."""
    eai, per_ev = _basement_eai(curves, buildings, payload["depth"], req)
    imp["eai"] = {k: v + eai.get(k, 0.0) for k, v in imp["eai"].items()}
    imp["basement_aai"] = sum(eai.values())
    imp["aai_agg"] = imp["aai_agg"] + imp["basement_aai"]
    for fc in imp.get("freq_curve", []):
        if fc["rp"] in req.rps:
            fc["eur"] += per_ev[req.rps.index(fc["rp"])]
    if imp.get("measure") and payload.get("measure_depth") is not None:
        m_eai, _ = _basement_eai(curves, buildings, payload["measure_depth"], req)
        ms = imp["measure"]
        ms["aai_agg"] += sum(m_eai.values())
        old = ms.get("benefit_annual") or 0.0
        ms["benefit_annual"] = imp["aai_agg"] - ms["aai_agg"]
        if old and ms.get("benefit_npv") is not None:
            ms["benefit_npv"] *= ms["benefit_annual"] / old
            if ms.get("bcr") is not None:
                ms["bcr"] *= ms["benefit_annual"] / old


def layer(run_id: str, name: str) -> bytes | None:
    v = _RUNS.get(run_id, {}).get(name)
    return v if isinstance(v, bytes) else None


def _persons(provider, bbox, buildings) -> dict:
    """Zensus 2022 residents per 100 m cell, shared out to residential buildings in the cell by floor area."""
    try:
        from .population import population
        cells = population(provider, bbox)
    except Exception:
        cells = None
    if not cells:
        return {}
    by_cell: dict = {}
    for b in buildings:
        if b.cls != "residential":
            continue
        key = (int(b.x // 100) * 100, int(b.y // 100) * 100)
        by_cell.setdefault(key, []).append(b)
    out = {}
    for c in cells:
        key = (int(c["x"] // 100) * 100, int(c["y"] // 100) * 100)
        bl = by_cell.get(key)
        if not bl or not c.get("residents"):
            continue
        floor = [b.footprint_m2 * (b.storeys or 1) for b in bl]
        tot = sum(floor) or 1.0
        for b, f in zip(bl, floor):
            out[b.id] = round(c["residents"] * f / tot, 1)
    return out
