# SPDX-License-Identifier: Apache-2.0
"""NRW provider without network: service answers are replaced by recorded-style XML."""

import pytest

from gecaa.geodata import nrw
from gecaa.geodata.base import to_wgs
from shapely.geometry import box

GML = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:ave="http://repository.gdi-de.org/schemas/adv/produkt/alkis-vereinfacht/2.0">
 <wfs:member><ave:Flurstueck gml:id="a">
  <ave:flstkennz>05495801901068______</ave:flstkennz><ave:gemarkung>Köln</ave:gemarkung><ave:flur>019</ave:flur>
  <ave:flstnrzae>1068</ave:flstnrzae><ave:flaeche>377.0</ave:flaeche><ave:lagebeztxt>Tunisstr. 3</ave:lagebeztxt>
  <ave:geometrie><gml:MultiSurface srsName="urn:ogc:def:crs:EPSG::25832"><gml:surfaceMember><gml:Polygon>
   <gml:exterior><gml:LinearRing><gml:posList srsDimension="2">356000 5645000 356020 5645000 356020 5645020 356000 5645020 356000 5645000</gml:posList></gml:LinearRing></gml:exterior>
  </gml:Polygon></gml:surfaceMember></gml:MultiSurface></ave:geometrie>
 </ave:Flurstueck></wfs:member>
</wfs:FeatureCollection>"""

BUILDINGS = GML.replace("Flurstueck", "GebaeudeBauwerk").replace("<ave:flstkennz>05495801901068______</ave:flstkennz>",
                                                               "<ave:oid>DENW1</ave:oid><ave:gebnutzbez>Wohnen</ave:gebnutzbez>") \
    .replace("356000 5645000 356020 5645000 356020 5645020 356000 5645020 356000 5645000",
             "356000 5645000 356010 5645000 356010 5645010 356000 5645010 356000 5645000")

BK50 = """<?xml version="1.0"?><FeatureInfoResponse xmlns="http://www.esri.com/wms">
<FIELDS KF="12,5" SIC="sic_2" GW_CODE="GW3" GW_STUFE="3" GW_TIEFE="10" TYP_TEXT="Braunerde-Pseudogley" ART_TEXT="stark lehmig-sandig"/>
</FeatureInfoResponse>"""
BK50_EMPTY = """<?xml version="1.0"?><FeatureInfoResponse><FIELDS KF="0" SIC="" GW_CODE="" GW_STUFE="0" TYP_TEXT=""/></FeatureInfoResponse>"""
BK50_NOGW = BK50.replace('GW_CODE="GW3" GW_STUFE="3" GW_TIEFE="10"', 'GW_CODE="GW0" GW_STUFE="0" GW_TIEFE="99"')
WSG_II = """<?xml version="1.0"?><FeatureInfoResponse><FIELDS zone_="2" zone_roemisch="II" name="Haltern-West" status="F"/></FeatureInfoResponse>"""
EMPTY = """<?xml version="1.0"?><FeatureInfoResponse></FeatureInfoResponse>"""

LOC = to_wgs(box(356005, 5645005, 356012, 5645012))


def provider(answers):
    p = nrw.NRWProvider()
    p._get = lambda url, params: answers(url, params)   # noqa: SLF001
    return p


def test_gml_parsing():
    feats = nrw.parse_gml_features(GML, "Flurstueck")
    assert len(feats) == 1
    assert feats[0]["properties"]["flstkennz"].startswith("05495801901068")
    assert abs(feats[0]["geometry"].area - 400) < 1e-6


def test_parcel_and_buildings():
    def ans(url, params):
        return BUILDINGS if "GebaeudeBauwerk" in params.get("TYPENAMES", "") else GML
    p = provider(ans)
    lon, lat = to_wgs(box(356009, 5645009, 356011, 5645011).centroid)["coordinates"]
    parcel = p.parcel_at(lon, lat)
    assert parcel["properties"]["label"] == "Köln, Flur 19, Nr. 1068"
    assert parcel["properties"]["id"] == "05495801901068"
    fc = p.buildings((lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001))
    assert fc["features"][0]["properties"]["function"] == "Wohnen"


def test_kf_from_bk50_in_metres_per_second():
    kf = provider(lambda u, p: BK50).kf(LOC)
    assert kf.value == pytest.approx(12.5 / 100 / 86400, rel=0.05)
    assert kf.quality.value == "mittel" and kf.distribution[0] == "log10normal"


def test_groundwater_class_uses_shallow_edge():
    gwf = provider(lambda u, p: BK50).groundwater_depth(LOC)
    assert gwf.value == 0.8 and "abgeleitet" in gwf.note
    nogw = provider(lambda u, p: BK50_NOGW).groundwater_depth(LOC)
    assert nogw.value == 2.0 and nogw.distribution is None
    deep = provider(lambda u, p: BK50.replace('GW_CODE="GW3" GW_STUFE="3"', 'GW_CODE="GWA5" GW_STUFE="5"')).groundwater_depth(LOC)
    assert deep.value == 2.0 and "GWA5" in deep.note and "abgesenkt" in deep.note


def test_sealed_area_gives_missing_not_a_guess():
    p = provider(lambda u, params: BK50_EMPTY)
    assert p.kf(LOC).value is None
    assert p.groundwater_depth(LOC).value is None


def test_protection_zones():
    designated = provider(lambda u, params: WSG_II if params["LAYERS"] == "4" else EMPTY).protection_zone(LOC)
    assert designated.value == "II" and "Haltern-West" in designated.note
    planned = provider(lambda u, params: WSG_II if params["LAYERS"] == "3" else EMPTY).protection_zone(LOC)
    assert planned.value == "geplant"
    none = provider(lambda u, params: EMPTY).protection_zone(LOC)
    assert none.value == "kein"


def test_assessment_with_nrw_answers():
    from gecaa.api_v2 import logics
    from gecaa.k3.service import AssessmentRequest, run_assessment

    def ans(url, params):
        if url == nrw.ALKIS_WFS:
            return BUILDINGS
        if url == nrw.BK50_WMS:
            return BK50
        return EMPTY
    far = to_wgs(box(356030, 5645030, 356040, 5645040))
    req = AssessmentRequest(units=[{"id": "S1", "name": "Mulde", "location": far, "inputs": {"TA": 0.3}}],
                            site_inputs={"ALT": "keine", "KT": 0})
    res = run_assessment(logics()["k3_dwa_a_138_1_mre"], provider(ans), req)
    checks = {c.id: c.result.value for c in res.units[0].checks}
    assert checks["CH01"] == "PASSED"          # no building under the location
    assert checks["CH02"] == "PASSED"          # 1.4e-6 m/s
    assert checks["CH03"] == "BLOCKED"         # 0.8 - 0.3 < 1.0 m
    assert checks["CH05"] == "PASSED"


# ---- regressions from the independent review

SERVICE_ERROR = """<?xml version="1.0"?><ServiceExceptionReport><ServiceException>Layer not queryable</ServiceException></ServiceExceptionReport>"""


def test_service_error_is_not_no_zone():
    with pytest.raises(RuntimeError):
        provider(lambda u, p: SERVICE_ERROR).protection_zone(LOC)
    with pytest.raises(RuntimeError):
        provider(lambda u, p: "<html><body>502 Bad Gateway</body></html").protection_zone(LOC)


def test_failed_zone_lookup_gives_open_in_assessment():
    from gecaa.api_v2 import logics
    from gecaa.k3.service import AssessmentRequest, run_assessment

    def ans(url, params):
        if url == nrw.WSG_WMS:
            return SERVICE_ERROR
        return BUILDINGS if url == nrw.ALKIS_WFS else BK50
    req = AssessmentRequest(units=[{"id": "S1", "name": "M", "location": to_wgs(box(356030, 5645030, 356040, 5645040))}])
    res = run_assessment(logics()["k3_dwa_a_138_1_mre"], provider(ans), req)
    assert next(c for c in res.units[0].checks if c.id == "CH05").result.value == "OPEN"


def test_outside_nrw_stays_open():
    from gecaa.api_v2 import logics
    from gecaa.k3.service import AssessmentRequest, run_assessment
    munich = {"type": "Polygon", "coordinates": [[[11.575, 48.137], [11.5751, 48.137], [11.5751, 48.1371], [11.575, 48.1371], [11.575, 48.137]]]}
    req = AssessmentRequest(units=[{"id": "S1", "name": "M", "location": munich,
                                    "inputs": {"KF": 1e-5, "GWF": 5, "TA": 0.5}}], site_inputs={"ALT": "keine", "KT": 0})
    res = run_assessment(logics()["k3_dwa_a_138_1_mre"], provider(lambda u, p: EMPTY), req)
    assert res.units[0].result.value == "OPEN"
    assert {c.id for c in res.units[0].checks if c.result.value == "OPEN"} >= {"CH01", "CH04", "CH05"}


def test_building_outage_gives_open_not_crash():
    from gecaa.api_v2 import logics
    from gecaa.k3.service import AssessmentRequest, run_assessment

    def ans(url, params):
        if url == nrw.ALKIS_WFS:
            raise RuntimeError("timeout")
        return BK50 if url == nrw.BK50_WMS else EMPTY
    req = AssessmentRequest(units=[{"id": "S1", "name": "M", "location": LOC}])
    res = run_assessment(logics()["k3_dwa_a_138_1_mre"], provider(ans), req)
    assert next(c for c in res.units[0].checks if c.id == "CH01").result.value == "OPEN"


def test_kf_zero_is_missing_and_not_rounded_across_limit():
    zero = provider(lambda u, p: BK50.replace('KF="12,5"', 'KF="0"')).kf(LOC)
    assert zero.value is None
    edge = provider(lambda u, p: BK50.replace('KF="12,5"', 'KF="8,62"')).kf(LOC)
    assert edge.value < 1e-6                     # 9.98e-7 m/s must stay below the 1e-6 limit


def test_parcel_without_hit_is_none():
    p = provider(lambda u, params: GML)
    lon, lat = to_wgs(box(356100, 5645100, 356101, 5645101).centroid)["coordinates"]
    assert p.parcel_at(lon, lat) is None
