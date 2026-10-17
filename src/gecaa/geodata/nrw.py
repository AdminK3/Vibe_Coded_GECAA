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
    except ET.ParseError as exc:   # HTML error page or broken answer: not the same as "nothing here"
        raise RuntimeError("Antwort des Kartendienstes nicht lesbar") from exc
    if _local(root.tag) in ("ServiceExceptionReport", "ExceptionReport"):
        msg = " ".join((e.text or "").strip() for e in root.iter())
        raise RuntimeError(f"Kartendienst meldet einen Fehler: {msg.strip()[:200]}")
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


def _building_label(p: dict) -> str:
    """ALKIS: 'funktion' is the specific use (e.g. 'Allgemein bildende Schule'); 'gebnutzbez' only a group."""
    name, fkt = p.get("name"), p.get("funktion") or p.get("gebnutzbez") or "Gebäude"
    return f"{name} ({fkt})" if name else fkt.split(";")[0]


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
            MapLayer("bkg_tiefe", "BKG Hinweiskarte: Wassertiefe (100-jährlich)", STARKREGEN_WMS, "nw_tiefe_agw",
                     "© BKG (dl-de/by-2-0)", opacity=0.7, min_zoom=12),
            MapLayer("pet", "Klimaanalyse: Wärmebelastung PET (typischer Sommertag)",
                     "https://www.wms.nrw.de/umwelt/klimaanpassung_klimaanalyse", "54", "© LANUK NRW (dl-de/zero-2-0)",
                     opacity=0.6, min_zoom=10),
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
            if "ExceptionReport" in text[:500] or text.lstrip()[:1] != "<" or "<html" in text[:300].lower():
                return text                      # never cache errors
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                self._prune_cache()
        if len(self._mem) > 500:
            self._mem.clear()
        self._mem[key] = (now, text)
        return text

    def _prune_cache(self, keep: int = 5000):
        files = sorted(self.cache_dir.glob("*.xml"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep] if len(files) > keep else []:
            old.unlink(missing_ok=True)

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
        hit = next((f for f in feats if f["geometry"].covers(pt)), None)
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
                        "properties": {"id": p.get("oid") or f"geb-{i}", "function": _building_label(p),
                                       "storeys": p.get("anzahlgs"), "address": p.get("lagebeztxt"), "source": "ALKIS NRW"}})
        return {"type": "FeatureCollection", "features": out}

    # ------------------------------------------------------------- soil map BK50

    def _bk50(self, location: dict) -> dict | None:
        pt = self._point(location)
        feats = self._feature_info(BK50_WMS, "Versickerungseignung", pt.x, pt.y)
        f = next((x for x in feats if str(x.get("TYP_TEXT", "")).strip()), None)
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
        if kf_cmd <= 0:
            return InputValue(abbr="KF", value=None, unit="m/s", source=src, level="unit",
                              note="BK50 gibt 0 cm/d an (sehr gering oder keine Angabe). Ein Versickerungsversuch liefert den Wert.")
        note = f"BK50: {kf_cmd:g} cm/d".replace(".", ",")
        kf_ms = kf_cmd / 100 / 86400
        soil = " · ".join(x for x in (f.get("TYP_TEXT"), f.get("ART_TEXT")) if x)
        sic = f.get("SIC")
        note += f"; Boden: {soil}" if soil else ""
        note += f"; Versickerungseignung {sic}" if sic else ""
        note += ". Maßstab 1:50.000 – Wert vor Ort prüfen."
        return InputValue(abbr="KF", value=float(f"{kf_ms:.4g}"), unit="m/s",
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
            # no distribution: the class gives a range of mean levels, not an uncertainty of the checked value
            return InputValue(abbr="GWF", value=lo, unit="m", source=src, level="unit",
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

    # ------------------------------------------------------------- v3: terrain, land use, climate analysis

    DGM_WCS = "https://www.wcs.nrw.de/geobasis/wcs_nw_dgm"
    KLIMA_WMS = "https://www.wms.nrw.de/umwelt/klimaanpassung_klimaanalyse"

    def dem(self, bbox_utm: tuple[float, float, float, float]):
        """DGM1 (1 m terrain without buildings) for the box, as (array, x_left, y_top, res=1.0)."""
        import numpy as np
        from rasterio.io import MemoryFile

        x0, y0, x1, y1 = (int(round(v)) for v in bbox_utm)
        if (x1 - x0) * (y1 - y0) > 1_500_000:
            raise ValueError("Geländeausschnitt zu groß")
        key = hashlib.sha1(f"dgm{x0},{y0},{x1},{y1}".encode()).hexdigest()
        path = self.cache_dir / f"{key}.tif" if self.cache_dir else None
        if path and path.exists():
            content = path.read_bytes()
        else:
            r = httpx.get(self.DGM_WCS, headers=UA, timeout=60, params=[
                ("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"), ("COVERAGEID", "nw_dgm"),
                ("FORMAT", "image/tiff"), ("SUBSET", f"x({x0},{x1})"), ("SUBSET", f"y({y0},{y1})")])
            r.raise_for_status()
            if not r.content.startswith((b"II*", b"MM\x00*")):
                raise RuntimeError("Geländemodell-Dienst lieferte kein GeoTIFF")
            content = r.content
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        with MemoryFile(content) as mf, mf.open() as ds:
            a = ds.read(1).astype("float64")
            if ds.nodata is not None:
                a[a == ds.nodata] = np.nan
            return a, ds.bounds.left, ds.bounds.top, float(ds.res[0])

    def landuse(self, bbox_utm) -> list[dict]:
        """ALKIS actual land use (Tatsächliche Nutzung): [{'nutzart', 'bez', 'geometry' (shapely, EPSG:25832)}]."""
        feats = self._wfs("Nutzung", bbox_utm, count=5000)
        return [{"nutzart": f["properties"].get("nutzart", ""), "bez": f["properties"].get("bez", ""),
                 "geometry": f["geometry"]} for f in feats]

    def buildings_utm(self, bbox_utm) -> list[dict]:
        feats = self._wfs("GebaeudeBauwerk", bbox_utm, count=5000)
        return [{"id": f["properties"].get("oid") or f"geb-{i}", "function": _building_label(f["properties"]),
                 "storeys": f["properties"].get("anzahlgs"), "address": f["properties"].get("lagebeztxt"),
                 "structure": str(f["properties"].get("gebnutzbez", "")).startswith("Sonstiges Bauwerk"),
                 "geometry": f["geometry"]} for i, f in enumerate(feats)]

    def climate_analysis(self, x: float, y: float) -> dict:
        """LANUK Klimaanalyse NRW at a point: PET (day) and air temperature (night), typical and extreme day."""
        layers = {"pet_typisch": "54", "pet_extrem": "52", "nacht_typisch": "38", "nacht_extrem": "29"}
        out = {}
        for key, layer in layers.items():
            feats = self._feature_info(self.KLIMA_WMS, layer, x, y)
            v = next((f.get("Classify.PixelValue") for f in feats if f.get("Classify.PixelValue")), None)
            out[key] = _num(v)
        feats = self._feature_info(self.KLIMA_WMS, "18", x, y)
        if feats:
            f = feats[0]
            out["versiegelung_pct"] = _num(f.get("vsg_men"))
        return out

    def bkg_depth(self, x: float, y: float) -> dict:
        """BKG Hinweiskarte Starkregengefahren NRW: maximum water depth (m) at a point, 100-year and extreme event.

        GetFeatureInfo as JSON (verified): properties.Tiefe in centimetres, -9999 = no water."""
        out = {}
        for key, layer in (("agw", "nw_tiefe_agw"), ("extrem", "nw_tiefe_extrem")):
            text = self._get(STARKREGEN_WMS, {
                "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
                "STYLES": "", "CRS": "EPSG:25832", "BBOX": f"{x - 1:.2f},{y - 1:.2f},{x + 1:.2f},{y + 1:.2f}",
                "WIDTH": "3", "HEIGHT": "3", "I": "1", "J": "1", "INFO_FORMAT": "application/json"})
            try:
                feats = json.loads(text).get("features", [])
            except ValueError:
                feats = []
            v = next((f["properties"].get("Tiefe") for f in feats if "Tiefe" in f.get("properties", {})), None)
            out[key] = None if v is None else (0.0 if v <= -9000 else round(float(v) / 100.0, 2))
        return out

    # ------------------------------------------------------------- v4: LoD2 heights, development plans

    LOD2_API = "https://ogc-api.nrw.de/3dg/v1/collections/building/items"
    BPLAN_API = "https://ogc-api.nrw.de/inspire-lu-bplan/v1/collections/spatialplan/items"

    @staticmethod
    def _bbox_crs84(bbox_utm) -> str:
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
        x0, y0, x1, y1 = bbox_utm
        (a, b), (c, d) = t.transform(x0, y0), t.transform(x1, y1)
        return f"{a:.6f},{b:.6f},{c:.6f},{d:.6f}"

    @staticmethod
    def parse_lod2_cityjson(doc: dict) -> dict[str, float]:
        """CityJSON (1.1) -> {ALKIS oid: max measuredHeight (m)} over Building and its BuildingParts.

        Verified on a live sample (Münster, 2026-10): BuildingPart attributes carry `measuredHeight`;
        the ALKIS id ("DENW..AL...") sits in attributes.externalReferences[].name (live check 2026-10-17).
        No storey attribute."""
        objs = doc.get("CityObjects", {}) or {}
        out: dict[str, float] = {}
        for oid, o in objs.items():
            if o.get("type") != "Building":
                continue
            attrs = o.get("attributes", {}) or {}
            ext = [r.get("name") for r in (attrs.get("externalReferences") or []) if isinstance(r, dict)]
            key = next((e for e in ext if e and e.startswith("DENW")), None) or o.get("name") or attrs.get("name") or oid
            hs = [attrs.get("measuredHeight")]
            for cid in o.get("children", []) or []:
                hs.append((objs.get(cid, {}).get("attributes") or {}).get("measuredHeight"))
            hs = [float(h) for h in hs if isinstance(h, (int, float)) and h > 0]
            if hs:
                out[str(key)] = round(max(hs), 2)
        return out

    @staticmethod
    def parse_lod2_surfaces(doc: dict, max_buildings: int = 1500) -> dict[str, list[dict]]:
        """CityJSON -> {ALKIS oid: [{'t': 'roof'|'wall', 'ring': [[lon, lat, h], ...]}, ...]}.

        Verified live (Mettmann, 2026-10-17): referenceSystem CRS84h, integer vertices with transform
        scale/translate, LoD2 `Solid` geometries with semantics (RoofSurface, WallSurface, GroundSurface,
        ClosureSurface); BuildingParts point to their Building via `parents`. Ground and closure faces are
        dropped; only exterior rings are kept."""
        objs = doc.get("CityObjects", {}) or {}
        tf = doc.get("transform") or {"scale": [1, 1, 1], "translate": [0, 0, 0]}
        sc, tr = tf["scale"], tf["translate"]
        verts = doc.get("vertices", []) or []

        def key_of(oid):
            o = objs.get(oid, {})
            if o.get("parents"):
                return key_of(o["parents"][0])
            attrs = o.get("attributes", {}) or {}
            ext = [r.get("name") for r in (attrs.get("externalReferences") or []) if isinstance(r, dict)]
            return str(next((e for e in ext if e and e.startswith("DENW")), None) or oid)

        kinds = {"RoofSurface": "roof", "WallSurface": "wall"}
        out: dict[str, list[dict]] = {}
        for oid, o in objs.items():
            for g in o.get("geometry", []) or []:
                b, sem = g.get("boundaries") or [], g.get("semantics") or {}
                surfs = sem.get("surfaces") or []
                if g.get("type") == "Solid":
                    faces = [(f, (sem.get("values") or [[None] * len(b[0])])[0][i]) for i, f in enumerate(b[0] if b else [])]
                elif g.get("type") in ("MultiSurface", "CompositeSurface"):
                    vals = sem.get("values") or [None] * len(b)
                    faces = list(zip(b, vals))
                else:
                    continue
                for face, si in faces:
                    t = kinds.get((surfs[si] or {}).get("type")) if si is not None and si < len(surfs) else None
                    if not t or not face:
                        continue
                    ring = [[round(verts[v][0] * sc[0] + tr[0], 7), round(verts[v][1] * sc[1] + tr[1], 7),
                             round(verts[v][2] * sc[2] + tr[2], 2)] for v in face[0] if v < len(verts)]
                    if len(ring) >= 3:
                        out.setdefault(key_of(oid), []).append({"t": t, "ring": ring})
            if len(out) >= max_buildings:
                break
        return out

    def lod2_surfaces(self, bbox_utm) -> dict[str, list[dict]]:
        """LoD2 roof and wall faces in a UTM32 box (OGC API 3dg, CityJSON, dl-de/zero-2-0); {} when offline."""
        try:
            text = self._get(self.LOD2_API, {"f": "cityjson", "bbox": self._bbox_crs84(bbox_utm), "limit": "2000"})
            return self.parse_lod2_surfaces(json.loads(text))
        except (httpx.HTTPError, ValueError, OSError, KeyError, IndexError):
            return {}

    def building_heights(self, bbox_utm, alkis_fallback: bool = True) -> dict[str, dict]:
        """Building heights in a UTM32 box: {ALKIS oid: {'height_m', 'method'}}.

        Source: OGC API Features "3D-Gebäudemodell NW (LoD2)", Geobasis NRW, dl-de/zero-2-0,
        https://ogc-api.nrw.de/3dg/v1 (collection `building`, f=cityjson). VERIFIED (collections and one
        item fetched via WebFetch, 2026-10). Matching by ALKIS oid (Building `name`) - assumed to equal the
        ALKIS vereinfacht `oid`; NOT verified live (check with scripts/probe_v4.py).
        Fallback for buildings without LoD2: ALKIS anzahlgs x 3 m, method "abgeleitet".
        Offline: LoD2 errors are swallowed; with no data at all an empty dict is returned (no invented values)."""
        out: dict[str, dict] = {}
        try:
            text = self._get(self.LOD2_API, {"f": "cityjson", "bbox": self._bbox_crs84(bbox_utm), "limit": "2000"})
            for k, h in self.parse_lod2_cityjson(json.loads(text)).items():
                out[k] = {"height_m": h, "method": "gemessen (LoD2)"}
        except (httpx.HTTPError, ValueError, OSError):
            pass
        if alkis_fallback:
            try:
                blds = self.buildings_utm(bbox_utm)
            except (httpx.HTTPError, RuntimeError, ET.ParseError, OSError):
                blds = []
            for b in blds:
                n = _num(b.get("storeys"))
                if b["id"] not in out and n:
                    out[b["id"]] = {"height_m": round(n * 3.0, 1), "method": "abgeleitet (ALKIS anzahlgs x 3 m)"}
        return out

    @staticmethod
    def parse_spatial_plans(doc: dict) -> list[dict]:
        """GeoJSON of INSPIRE PLU spatialplan -> plan records. Property keys are literal dotted names (verified)."""
        out = []
        for f in doc.get("features", []) or []:
            p = f.get("properties", {}) or {}
            out.append({"id": f.get("id"), "name": p.get("officialTitle"), "type": p.get("planTypeName.title"),
                        "status": p.get("processStepGeneral.title"), "valid_from": p.get("validFrom"),
                        "municipality": p.get("kommune"), "number": (p.get("nr") or "").strip("_") or None,
                        "url": p.get("officialDocument"), "geometry": f.get("geometry")})
        return out

    def development_plans(self, bbox_utm) -> list[dict] | None:
        """Bebauungs-/Flächennutzungspläne intersecting a UTM32 box (geometry stays EPSG:25832 GeoJSON).

        Source: OGC API Features "Räumliche Pläne nach BauGB" (INSPIRE PLU, IT.NRW / MHKBD), dl-de/by-2-0,
        https://ogc-api.nrw.de/inspire-lu-bplan/v1 (collection `spatialplan`, GeoJSON). VERIFIED via WebFetch
        2026-10 (Münster sample: officialTitle, planTypeName.title, processStepGeneral.title, officialDocument).
        Coverage depends on what municipalities have delivered. No WMS verified, so no MapLayer is added.
        Returns None when the service cannot be reached."""
        x0, y0, x1, y1 = (round(v, 2) for v in bbox_utm)
        crs = "http://www.opengis.net/def/crs/EPSG/0/25832"
        try:
            text = self._get(self.BPLAN_API, {"f": "json", "bbox": f"{x0},{y0},{x1},{y1}", "bbox-crs": crs,
                                              "crs": crs, "limit": "500"})
            return self.parse_spatial_plans(json.loads(text))
        except (httpx.HTTPError, ValueError, OSError):
            return None
