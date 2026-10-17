# SPDX-License-Identifier: GPL-2.0-or-later
# GECAA QGIS plugin. Copyright 2026 Khoch3 KlimaKarten UG. GNU GPL v2 or later (see LICENSE).
"""Dock widget: map canvas centre -> EPSG:4326 -> POST GECAA_URL/v3/risk/rain -> layers.

Depth: GET /v3/risk/{run_id}/geotiff?name=... (GeoTIFF, EPSG:25832). If the server answers 404 (it keeps only
PNGs), the depth PNG is loaded with a world file in EPSG:3857 (the PNG bounds are Web-Mercator aligned).
Buildings: result["buildings"] as a GeoJSON layer (EPSG:4326). Network runs in a QgsTask (UI stays responsive).
"""

import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject,
                       QgsRasterLayer, QgsTask, QgsVectorLayer)
from qgis.PyQt.QtWidgets import (QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                                 QPushButton, QVBoxLayout, QWidget)

DEFAULT_URL = os.environ.get("GECAA_URL", "http://localhost:8000")
R = 6378137.0


def _merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def _http(method, url, body=None, timeout=300):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (user-configured server)
        return r.read()


def _run(task, base, provider, payload, outdir):
    """Worker thread: no QGIS layer objects here, only files. Returns a dict for the finished() slot."""
    q = f"?provider={urllib.parse.quote(provider)}" if provider else ""
    res = json.loads(_http("POST", f"{base}/v3/risk/rain{q}", payload))
    rid = res["run_id"]
    name = f"depth|{res['scenarios'][0]}|{max(res['rps'])}|base"
    out = {"result": res, "name": name}
    try:
        tif = _http("GET", f"{base}/v3/risk/{rid}/geotiff?name={urllib.parse.quote(name)}")
        path = os.path.join(outdir, f"{rid}_depth.tif")
        with open(path, "wb") as f:
            f.write(tif)
        out["raster"], out["raster_crs"] = path, "EPSG:25832"
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        png = _http("GET", f"{base}/v3/risk/{rid}/layer?name={urllib.parse.quote(name)}")
        path = os.path.join(outdir, f"{rid}_depth.png")
        with open(path, "wb") as f:
            f.write(png)
        out["raster"], out["raster_crs"], out["needs_world"] = path, "EPSG:3857", True
    feats = [{"type": "Feature", "geometry": b["geometry"],
              "properties": {k: v for k, v in b.items() if k != "geometry" and not isinstance(v, (dict, list))}
              | {"depth_" + k: v for k, v in (b.get("depth") or {}).items()}}
             for b in res.get("buildings", []) if b.get("geometry")]
    gj = os.path.join(outdir, f"{rid}_gebaeude.geojson")
    with open(gj, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    out["buildings"] = gj
    return out


def _write_world_file(png_path, bounds):
    """World file (.pgw) in EPSG:3857 from result bounds [[S, W], [N, E]] and the PNG size."""
    from qgis.PyQt.QtGui import QImageReader
    size = QImageReader(png_path).size()
    (s, w), (n, e) = bounds
    x0, y0 = _merc(w, n)
    x1, y1 = _merc(e, s)
    px, py = (x1 - x0) / size.width(), (y1 - y0) / size.height()
    with open(os.path.splitext(png_path)[0] + ".pgw", "w") as f:
        f.write(f"{px}\n0\n0\n{py}\n{x0 + px / 2}\n{y0 + py / 2}\n")


class GecaaDock(QDockWidget):
    def __init__(self, iface):
        super().__init__("GECAA Starkregen")
        self.iface = iface
        self.task = None
        self.tmp = tempfile.mkdtemp(prefix="gecaa_qgis_")
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.url = QLineEdit(DEFAULT_URL)
        self.provider = QComboBox()
        self.provider.addItems(["nrw", "synthetic"])
        self.box = QDoubleSpinBox()
        self.box.setRange(50, 2000)
        self.box.setValue(300)
        self.box.setSuffix(" m")
        form.addRow("GECAA_URL", self.url)
        form.addRow("Datengrundlage", self.provider)
        form.addRow("Ausschnitt", self.box)
        lay.addLayout(form)
        self.btn = QPushButton("Kartenmitte analysieren")
        self.btn.clicked.connect(self.start)
        lay.addWidget(self.btn)
        self.status = QLabel("Entscheidungshilfe, kein Gutachten.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.setWidget(w)

    def _centre_wgs84(self):
        canvas = self.iface.mapCanvas()
        src = canvas.mapSettings().destinationCrs()
        xf = QgsCoordinateTransform(src, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        p = xf.transform(canvas.center())
        return p.x(), p.y()

    def start(self):
        lon, lat = self._centre_wgs84()
        payload = {"lon": lon, "lat": lat, "box_m": self.box.value()}
        base = self.url.text().rstrip("/")
        self.btn.setEnabled(False)
        self.status.setText(f"Berechne für {lon:.5f}, {lat:.5f} …")
        self.task = QgsTask.fromFunction("GECAA Starkregen", _run, base, self.provider.currentText(), payload,
                                         self.tmp, on_finished=self.finished)
        QgsApplication.taskManager().addTask(self.task)

    def finished(self, exception, out=None):
        self.btn.setEnabled(True)
        if exception is not None or out is None:
            msg = exception.read().decode("utf-8", "replace")[:300] if hasattr(exception, "read") else str(exception)
            self.status.setText(f"Fehler: {msg}")
            return
        res = out["result"]
        if out.get("needs_world"):
            _write_world_file(out["raster"], res["bounds"])
        rl = QgsRasterLayer(out["raster"], f"Wassertiefe {out['name'].split('|', 1)[1]} ({res['run_id']})")
        rl.setCrs(QgsCoordinateReferenceSystem(out["raster_crs"]))
        vl = QgsVectorLayer(out["buildings"], f"Gebäude ({res['run_id']})", "ogr")
        proj = QgsProject.instance()
        for lyr in (rl, vl):
            if lyr.isValid():
                proj.addMapLayer(lyr)
        eai = (res.get("impact") or {}).get(res["scenarios"][0], {}).get("aai_agg")
        kind = "GeoTIFF" if not out.get("needs_world") else "PNG mit World-File"
        self.status.setText(f"Lauf {res['run_id']}: {len(res.get('buildings', []))} Gebäude, Wassertiefe als {kind}."
                            + (f" Erwarteter Jahresschaden (Untergrenze/Größenordnung): {eai:,.0f} €" if eai else ""))
