# GECAA QGIS-Plugin (GPL-2.0-or-later)

Dock-Widget „GECAA Starkregen“: nimmt die Mitte der Kartenansicht, transformiert sie nach EPSG:4326 und ruft
`POST $GECAA_URL/v3/risk/rain` auf (Standard `http://localhost:8000`, im Widget änderbar).

Geladene Ebenen:
- **Wassertiefe** (größtes Wiederkehrintervall, erstes Szenario): `GET /v3/risk/{run_id}/geotiff?name=depth|<Szenario>|<RP>|base`
  liefert ein GeoTIFF in EPSG:25832. Antwortet der Server mit 404 (er hält derzeit nur PNG-Ebenen vor), wird das
  PNG aus `/v3/risk/{run_id}/layer` mit World-File (`.pgw`, EPSG:3857) geladen.
- **Gebäude** als GeoJSON (EPSG:4326) mit Wassertiefe je Wiederkehrintervall (`depth_<RP>`) und `eai_eur`.

Installation: Ordner `gecaa_qgis` nach `…/QGIS3/profiles/default/python/plugins/` kopieren (oder als ZIP über
„Erweiterungen → Aus ZIP installieren“), QGIS neu starten, Plugin aktivieren. Getestet werden kann es nur in QGIS
(≥ 3.22); die Server-Endpunkte sind in `tests/test_v3_extras.py` getestet.

Entscheidungshilfe, kein Gutachten. Lizenz: GNU GPL v2 oder später (siehe `LICENSE`).
