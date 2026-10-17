# SPDX-License-Identifier: Apache-2.0
"""pygeoapi (MIT) process plugins: OGC API - Processes for the GECAA heavy-rain chain and heat assessment.

Configured in ogc/pygeoapi-config.yml (processor name: gecaa.ogc.processes.StarkregenProcessor / HitzeProcessor).
"""

from __future__ import annotations

from pygeoapi.process.base import BaseProcessor, ProcessorExecuteError

_LONLAT = {
    "lon": {"title": "Länge (WGS84)", "schema": {"type": "number", "minimum": -180, "maximum": 180},
            "minOccurs": 1, "maxOccurs": 1},
    "lat": {"title": "Breite (WGS84)", "schema": {"type": "number", "minimum": -90, "maximum": 90},
            "minOccurs": 1, "maxOccurs": 1},
    "provider": {"title": "Datengrundlage", "description": "nrw (Geodaten NRW) oder synthetic (Demodaten)",
                 "schema": {"type": "string", "enum": ["nrw", "synthetic"], "default": "nrw"},
                 "minOccurs": 0, "maxOccurs": 1},
}


def _meta(pid: str, title: str, desc: str, inputs: dict, example: dict) -> dict:
    return {"version": "0.1.0", "id": pid, "title": {"de": title, "en": title}, "description": {"de": desc, "en": desc},
            "jobControlOptions": ["sync-execute"], "keywords": ["GECAA", "Klimaanpassung"],
            "links": [{"type": "text/html", "rel": "about", "title": "GECAA", "href": "https://www.khoch3klimakarten.de"}],
            "inputs": inputs,
            "outputs": {"result": {"title": "Ergebnis (GECAA-JSON)",
                                   "schema": {"type": "object", "contentMediaType": "application/json"}}},
            "example": {"inputs": example}}


STARKREGEN = _meta(
    "gecaa-starkregen", "GECAA Starkregen", "Senken- und Fließweganalyse, Wassertiefen an Gebäuden und "
    "erwarteter Jahresschaden (Untergrenze/Größenordnung). Entscheidungshilfe, kein Gutachten.",
    {**_LONLAT,
     "box_m": {"title": "Ausschnitt (m)", "schema": {"type": "number", "default": 300}, "minOccurs": 0, "maxOccurs": 1},
     "rps": {"title": "Wiederkehrintervalle (Jahre)", "schema": {"type": "array", "items": {"type": "integer"}},
             "minOccurs": 0, "maxOccurs": 1},
     "prefer_worker": {"title": "CLIMADA-Worker nutzen", "schema": {"type": "boolean", "default": True},
                       "minOccurs": 0, "maxOccurs": 1}},
    {"lon": 6.95, "lat": 51.25, "box_m": 300, "provider": "nrw"})
HITZE = _meta("gecaa-hitze", "GECAA Hitze", "PET aus der Klimaanalyse NRW und heiße Tage (DWD) am Standort.",
              _LONLAT, {"lon": 6.95, "lat": 51.25, "provider": "nrw"})


def _prov(data: dict):
    from ..api_v2 import _provider
    prov = _provider(data.get("provider") or "nrw")
    lon, lat = data.get("lon"), data.get("lat")
    if lon is None or lat is None:
        raise ProcessorExecuteError("lon und lat sind erforderlich")
    if not prov.covers(float(lon), float(lat)):
        raise ProcessorExecuteError("Der Ort liegt außerhalb des Datengebiets.")
    return prov, float(lon), float(lat)


class StarkregenProcessor(BaseProcessor):
    def __init__(self, processor_def: dict):
        super().__init__(processor_def, STARKREGEN)

    def execute(self, data: dict, outputs: dict | None = None):
        from ..api_v3 import _kostra
        from ..risk import chain
        prov, lon, lat = _prov(data)
        fields = {k: data[k] for k in ("box_m", "rps", "prefer_worker", "duration_min", "threshold_m", "sewer_mm")
                  if data.get(k) is not None}
        try:
            req = chain.RainRequest(lon=lon, lat=lat, **fields)
            res = chain.run(prov, req, kostra=None if prov.id == "synthetic" else _kostra())
        except ValueError as exc:
            raise ProcessorExecuteError(str(exc)) from exc
        return "application/json", {"id": "result", "value": res}

    def __repr__(self):
        return "<StarkregenProcessor>"


class HitzeProcessor(BaseProcessor):
    def __init__(self, processor_def: dict):
        super().__init__(processor_def, HITZE)

    def execute(self, data: dict, outputs: dict | None = None):
        from ..api_v3 import _hot_days
        from ..risk import heat
        prov, lon, lat = _prov(data)
        res = heat.assess(prov, lon, lat, None if prov.id == "synthetic" else _hot_days())
        return "application/json", {"id": "result", "value": res}

    def __repr__(self):
        return "<HitzeProcessor>"
