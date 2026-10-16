# SPDX-License-Identifier: Apache-2.0
"""Live open geodata for North Rhine-Westphalia, queried on demand for any place in NRW.

Services (all open, no key):
- ALKIS vereinfacht (WFS 2.0, GML 3.2)      parcels and building footprints       dl-de/zero-2-0
- GD NRW Bodenkarte BK50 (WMS GetFeatureInfo) kf (cm/d) and groundwater level class  dl-de/by-2-0
- LANUK Wasserschutzgebiete (WMS GetFeatureInfo) protection zone at the location    free except commercial use
- BKG Starkregengefahrenhinweise (WMS)       display only                          dl-de/by-2-0

Nothing here guesses a value: when a service has no answer (for example the soil map in sealed
city centres) the input stays missing and the check stays OPEN. Values derived from a class
(groundwater level class -> depth) are marked "abgeleitet" in the note.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape

from ..k3.model import InputValue, Quality
from .base import DataSource, MapLayer, to_utm, to_wgs

ALKIS_WFS = "https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht"
BK50_WMS = "https://www.wms.nrw.de/gd/bk050"
WSG_WMS = "https://www.wms.nrw.de/umwelt/wasser/wsg"
STARKREGEN_WMS = "https://sgx.geodatenzentrum.de/wms_starkregen"
DOP_WMS = "https://www.wms.nrw.de/geobasis/wms_nw_dop"
ALKIS_WMS = "https://www.wms.nrw.de/geobasis/wms_nw_alkis"

NRW_BOUNDS = (5.86, 50.32, 9.47, 52.54)          # WGS84 west, south, east, north
CRS_URN = "urn:ogc:def:crs:EPSG::25832"
UA = {"User-Agent": "GECAA/0.2 (open-source climate adaptation decision support)"}
CACHE_SECONDS = 7 * 24 * 3600

# BK50 groundwater level class -> mean fluctuation range below ground (m), GD NRW legend GW.pdf
GW_CLASSES = {1: (0.0, 0.4), 2: (0.4, 0.8), 3: (0.8, 1.3), 4: (1.3, 2.0)}
WSG_LAYERS = {"4": ("Trinkwasser", True), "2": ("Heilquellen", True), "3": ("Trinkwasser", False), "1": ("Heilquellen", False)}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _num(text) -> float | None:
    if text is None:
        return None
    t = str(text).strip().replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


# ----------------------------------------------------------------- GML 3.2 (just enough for ALKIS polygons)

def _ring(el) -> list[tuple[float, float]]:
    pos_list = next((e for e in el.iter() if _local(e.tag) == "posList"), None)
    if pos_list is not None and pos_list.text:
        dim = int(pos_list.get("srsDimension", "2"))
        v = [float(x) for x in pos_list.text.split()]
        pts = [(v[i], v[i + 1]) for i in range(0, len(v) - dim + 1, dim)]
    else:
        pts = [tuple(float(x) for x in e.text.split()[:2]) for e in el.iter() if _local(e.tag) == "pos" and e.text]
    if pts and pts[0][0] > 1_000_000 > pts[0][1]:  # northing first -> swap to easting, northing
        pts = [(y, x) for x, y in pts]
    return pts


def _polygons(geom_el) -> list[Polygon]:
    polys = []
    for el in geom_el.iter():
        if _local(el.tag) not in ("Polygon", "PolygonPatch"):
            continue
        ext, holes = None, []
        for child in el:
            name = _local(child.tag)
            if name == "exterior":
                ext = _ring(child)
            elif name == "interior":
                holes.append(_ring(child))
        if ext and len(ext) >= 4:
            p = Polygon(ext, [h for h in holes if len(h) >= 4])
            polys.append(p if p.is_valid else p.buffer(0))
    return polys


def parse_gml_features(xml_text: str, type_name: str) -> list[dict]:
    """Features of one type as {'properties': {...}, 'geometry': shapely (EPSG:25832)}."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    if _local(root.tag) == "ExceptionReport":
        msg = " ".join(e.text or "" for e in root.iter() if _local(e.tag) == "ExceptionText")
        raise RuntimeError(f"WFS-Fehler: {msg.strip()[:200]}")
    out = []
    for feat in root.iter():
        if _local(feat.tag) != type_name:
            continue
        props, geom = {}, None
        for child in feat:
            name = _local(child.tag)
            if len(child):
                polys = _polygons(child)
                if polys:
                    geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
            elif child.text and child.text.strip():
                props[name] = child.text.strip()
        if geom is not None and not geom.is_empty:
            out.append({"properties": props, "geometry": geom})
    return out


# ----------------------------------------------------------------- WMS GetFeatureInfo (ESRI text/xml)

def parse_feature_info(xml_text: str) -> list[dict]:
    """Attribute sets from a GetFeatureInfo answer: ESRI <FIELDS a="..."/> or <Field><FieldName/><FieldValue/>."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    feats = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "FIELDS" and el.attrib:
            feats.append({_local(k): v for k, v in el.attrib.items()})
        elif name in ("FeatureInfo", "Feature") and any(_local(c.tag) == "Field" for c in el):
            d = {}
            for f in el:
                if _local(f.tag) == "Field":
                    k = next((c.text for c in f if _local(c.tag) == "FieldName"), None)
                    v = next((c.text for c in f if _local(c.tag) == "FieldValue"), None)
                    if k:
                        d[k] = v
            feats.append(d)
    return feats


class NRWProvider:
    id = "nrw"
    label = "Offene Geodaten NRW (live)"

    def __init__(self, cache_dir: Path | None = None, timeout: float = 20.0):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        self._mem: dict[str, tuple[float, str]] = {}
        self.sources = [
            DataSource("alkis", "ALKIS vereinfacht: Flurstücke und Gebäude", "Geobasis NRW", "dl-de/zero-2-0", ALKIS_WFS),
            DataSource("bk50", "Bodenkarte BK50: Durchlässigkeit kf, Grundwasserstufe", "Geologischer Dienst NRW", "dl-de/by-2-0",
                       BK50_WMS, "Maßstab 1:50.000 – für einzelne Standorte nur Orientierung (Datengüte mittel); "
                                 "in versiegelten Innenstädten oft ohne Angabe"),
            DataSource("wsg", "Wasserschutzgebiete NRW", "LANUK NRW", "frei, gewerbliche Nutzung genehmigungspflichtig", WSG_WMS),
            DataSource("starkregen", "Starkregengefahrenhinweise (nur Anzeige)", "BKG", "dl-de/by-2-0", STARKREGEN_WMS),
            DataSource("nominatim", "Adresssuche", "OpenStreetMap Nominatim", "ODbL", "https://nominatim.openstreetmap.org"),
        ]
        self.map_layers = [
            MapLayer("dop", "Luftbild (DOP)", DOP_WMS, "nw_dop_rgb", "© Geobasis NRW (dl-de/zero-2-0)", opacity=1.0, min_zoom=14),
            MapLayer("alkis", "Liegenschaftskarte (ALKIS)", ALKIS_WMS, "adv_alkis_flurstuecke,adv_alkis_gebaeude",
                     "© Geobasis NRW (dl-de/zero-2-0)", opacity=0.9, min_zoom=16),
            MapLayer("bk50", "Versickerungseignung (BK50)", BK50_WMS, "Versickerungseignung",
                     "© Geologischer Dienst NRW (dl-de/by-2-0)", opacity=0.6, min_zoom=10),
            MapLayer("wsg", "Wasserschutzgebiete", WSG_WMS, "4,3,2,1", "© LANUK NRW", opacity=0.55, min_zoom=9),
            MapLayer("starkregen", "Starkregen: Fließgeschwindigkeit (außergewöhnlich)", STARKREGEN_WMS, "nw_geschw_agw",
                     "© BKG (dl-de/by-2-0)", opacity=0.7, min_zoom=12),
        ]

    # ------------------------------------------------------------- plumbing

    @property
    def bounds(self):
        return NRW_BOUNDS

    def covers(self, lon, lat):
        w, s, e, n = NRW_BOUNDS
        return w <= lon <= e and s <= lat <= n

    def _get(self, url: str, params: dict) -> str:
        key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
        now = time.time()
        if key in self._mem and now - self._mem[key][0] < CACHE_SECONDS:
            return self._mem[key][1]
        path = self.cache_dir / f"{key}.xml" if self.cache_dir else None
        if path and path.exists() and now - path.stat().st_mtime < CACHE_SECONDS:
            text = path.read_text(encoding="utf-8")
        else:
            r = httpx.get(url, params=params, headers=UA, timeout=self.timeout)
            r.raise_for_status()
            text = r.text
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        if len(self._mem) > 500:
            self._mem.clear()
        self._mem[key] = (now, text)
        return text

    def _wfs(self, type_name: str, bbox_utm: tuple[float, float, float, float], count: int = 1000) -> list[dict]:
        x0, y0, x1, y1 = (round(v, 2) for v in bbox_utm)
        text = self._get(ALKIS_WFS, {
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": f"ave:{type_name}",
            "COUNT": str(count), "SRSNAME": CRS_URN, "BBOX": f"{x0},{y0},{x1},{y1},{CRS_URN}"})
        return parse_gml_features(text, type_name)

    def _feature_info(self, url: str, layer: str, x: float, y: float) -> list[dict]:
        half = 1.0   # 2 m box, 3 x 3 pixels, centre pixel
        text = self._get(url, {
            "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
            "STYLES": "", "CRS": "EPSG:25832", "BBOX": f"{x - half:.2f},{y - half:.2f},{x + half:.2f},{y + half:.2f}",
            "WIDTH": "3", "HEIGHT": "3", "I": "1", "J": "1", "INFO_FORMAT": "text/xml", "FEATURE_COUNT": "5"})
        return parse_feature_info(text)

    @staticmethod
    def _point(location: dict) -> Point:
        g = to_utm(location)
        return g.representative_point()

    # ------------------------------------------------------------- parcels and buildings

    def parcel_at(self, lon, lat):
        if not self.covers(lon, lat):
            return None
        pt = to_utm({"type": "Point", "coordinates": [lon, lat]})
        feats = self._wfs("Flurstueck", (pt.x - 1, pt.y - 1, pt.x + 1, pt.y + 1), count=20)
        hit = next((f for f in feats if f["geometry"].covers(pt)), feats[0] if feats else None)
        if not hit:
            return None
        p = hit["properties"]
        nr = p.get("flstnrzae", "?") + (f"/{p['flstnrnen']}" if p.get("flstnrnen") else "")
        label = f"{p.get('gemarkung', '')}, Flur {int(p['flur']) if p.get('flur', '').isdigit() else p.get('flur', '?')}, Nr. {nr}"
        return {"type": "Feature", "geometry": to_wgs(hit["geometry"]),
                "properties": {"id": p.get("flstkennz", "").rstrip("_"), "label": label.strip(", "),
                               "address": p.get("lagebeztxt"), "area_m2": _num(p.get("flaeche")),
                               "use": p.get("tntxt"), "source": "ALKIS NRW"}}

    def buildings(self, bbox):
        w, s, e, n = bbox
        a, b = to_utm({"type": "Point", "coordinates": [w, s]}), to_utm({"type": "Point", "coordinates": [e, n]})
        feats = self._wfs("GebaeudeBauwerk", (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
        out = []
        for i, f in enumerate(feats):
            p = f["properties"]
            out.append({"type": "Feature", "geometry": to_wgs(f["geometry"]),
                        "properties": {"id": p.get("oid") or f"geb-{i}", "function": p.get("gebnutzbez") or p.get("funktion") or "Gebäude",
                                       "storeys": p.get("anzahlgs"), "source": "ALKIS NRW"}})
        return {"type": "FeatureCollection", "features": out}

    # ------------------------------------------------------------- soil map BK50

    def _bk50(self, location: dict) -> dict | None:
        pt = self._point(location)
        feats = self._feature_info(BK50_WMS, "Versickerungseignung", pt.x, pt.y)
        f = next((x for x in feats if any(str(v).strip() not in ("", "0", "Null") for k, v in x.items() if k.upper() in ("KF", "TYP_TEXT", "GW_CODE"))), None)
        return f

    def kf(self, location):
        f = self._bk50(location)
        src = "Bodenkarte BK50 (GD NRW), kf im 2-m-Raum"
        if not f or not str(f.get("TYP_TEXT", "")).strip():
            return InputValue(abbr="KF", value=None, unit="m/s", source=src, level="unit",
                              note="Die Bodenkarte hat hier keine Angabe (meist bebaute oder versiegelte Fläche). "
                                   "Ein Versickerungsversuch oder Baugrundgutachten liefert den Wert.")
        kf_cmd = _num(f.get("KF"))
        if kf_cmd is None:
            return InputValue(abbr="KF", value=None, unit="m/s", source=src, level="unit", note="kein kf-Wert in der BK50")
        note = f"BK50: {kf_cmd:g} cm/d".replace(".", ",")
        if kf_cmd <= 0:
            kf_cmd, note = 1.0, note + " (Klasse sehr gering, < 1 cm/d; Klassenobergrenze verwendet, abgeleitet)"
        kf_ms = kf_cmd / 100 / 86400
        soil = " · ".join(x for x in (f.get("TYP_TEXT"), f.get("ART_TEXT")) if x)
        sic = f.get("SIC")
        note += f"; Boden: {soil}" if soil else ""
        note += f"; Versickerungseignung {sic}" if sic else ""
        note += ". Maßstab 1:50.000 – Wert vor Ort prüfen."
        return InputValue(abbr="KF", value=float(f"{kf_ms:.2g}"), unit="m/s",
                          distribution=("log10normal", math.log10(kf_ms), 0.5),
                          source=src, level="unit", quality=Quality.mittel, note=note)

    def groundwater_depth(self, location):
        f = self._bk50(location)
        src = "Bodenkarte BK50 (GD NRW), Grundwasserstufe"
        if not f or not str(f.get("TYP_TEXT", "")).strip():
            return InputValue(abbr="GWF", value=None, unit="m", source=src, level="unit",
                              note="Die Bodenkarte hat hier keine Angabe. Grundwasserstände bei der Unteren Wasserbehörde "
                                   "oder aus Messstellen (HYGRIS) erfragen.")
        code = str(f.get("GW_CODE", "")).strip().upper()
        stufe = _num(f.get("GW_STUFE"))
        m = re.search(r"(\d)\s*$", code)
        cls = int(m.group(1)) if m else (int(stufe) if stufe is not None else None)
        trend = {"A": "abgesenkt", "H": "angehoben", "O": "stark schwankend", "U": "stark schwankend", "S": "stark schwankend"}
        flag = next((t for k, t in trend.items() if code.startswith("G" + k) or code.startswith("GW" + k)), None)
        if cls in GW_CLASSES:
            lo, hi = GW_CLASSES[cls]
            note = (f"BK50 Grundwasserstufe {code or cls}: mittlerer Schwankungsbereich {lo:g}–{hi:g} m unter Gelände. "
                    f"Als Flurabstand zum MHGW wird die Oberkante {lo:g} m verwendet (abgeleitet, auf der sicheren Seite).")
            if flag:
                note += f" Grundwasser {flag}."
            return InputValue(abbr="GWF", value=lo, unit="m", distribution=("uniform", lo, hi), source=src, level="unit",
                              quality=Quality.mittel, note=note)
        no_gw = str(f.get("GW_TIEFE", "")).strip() in ("99", "99.0")
        if cls == 0 or (cls is not None and cls >= 5) or (cls is None and no_gw):
            what = "kein Grundwasser im 2-m-Raum" if cls in (0, None) else f"Grundwasserstufe {code or cls}, tiefer als Stufe 4 (bis 2,0 m)"
            note = (f"BK50: {what}. Verwendet wird 2 m (Untergrenze, abgeleitet); "
                    "tatsächlicher Flurabstand größer, aber nicht beziffert.")
            if flag:
                note += f" Grundwasser {flag}."
            return InputValue(abbr="GWF", value=2.0, unit="m", source=src, level="unit", quality=Quality.mittel, note=note)
        return InputValue(abbr="GWF", value=None, unit="m", source=src, level="unit",
                          note=f"Grundwasserstufe nicht auswertbar ({code or 'leer'}).")

    # ------------------------------------------------------------- water protection zones

    def protection_zone(self, location):
        pt = self._point(location)
        src = "Wasserschutzgebiete NRW (LANUK)"
        hits = []
        for layer, (kind, designated) in WSG_LAYERS.items():
            for f in self._feature_info(WSG_WMS, layer, pt.x, pt.y):
                zone = str(f.get("zone_roemisch") or f.get("ZONE_ROEMISCH") or "").replace(" ", "").upper()
                if not zone and f.get("zone_"):
                    zone = {"1": "I", "2": "II", "3": "III"}.get(str(f["zone_"]).strip(), "")
                if zone:
                    hits.append((designated, kind, zone, f.get("name") or f.get("NAME") or ""))
        if not hits:
            return InputValue(abbr="WSG", value="kein", source=src, level="unit", quality=Quality.hoch,
                              note="Kein festgesetztes oder geplantes Schutzgebiet am Standort (Abfrage am Standortmittelpunkt).")
        order = {"I": 0, "II": 1, "IIIA": 2, "IIIB": 3, "III": 4}
        fixed = sorted((h for h in hits if h[0]), key=lambda h: order.get(h[2], 5))
        if fixed:
            _, kind, zone, name = fixed[0]
            value = zone if zone in order else "III"
            note = f"{kind}schutzgebiet {name}, Zone {zone} (festgesetzt)."
            if value != zone:
                note += " Zonenbezeichnung ohne direkte Entsprechung – wie Zone III behandelt (Einzelfall, offen)."
            return InputValue(abbr="WSG", value=value, source=src, level="unit", quality=Quality.hoch, note=note.strip())
        _, kind, zone, name = sorted(hits, key=lambda h: order.get(h[2], 5))[0]
        return InputValue(abbr="WSG", value="geplant", source=src, level="unit", quality=Quality.hoch,
                          note=f"Geplantes {kind}schutzgebiet {name}, Zone {zone}: Verfahren läuft, Einstufung durch die Behörde.")
