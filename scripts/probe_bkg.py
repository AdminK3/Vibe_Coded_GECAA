# SPDX-License-Identifier: Apache-2.0
"""Show raw BKG heavy-rain GetFeatureInfo answers and ALKIS building attributes near Mettmann."""

import re

import httpx

U = "https://sgx.geodatenzentrum.de/wms_starkregen"
for x, y in [(358700, 5679620), (358500, 5679800), (358900, 5679500), (358620, 5679700)]:
    for layer in ("nw_tiefe_agw", "nw_tiefe_extrem"):
        for fmt in ("text/xml", "application/json", "text/plain"):
            r = httpx.get(U, timeout=30, params={
                "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetFeatureInfo", "LAYERS": layer, "QUERY_LAYERS": layer,
                "STYLES": "", "CRS": "EPSG:25832", "BBOX": f"{x-50},{y-50},{x+50},{y+50}", "WIDTH": "101", "HEIGHT": "101",
                "I": "50", "J": "50", "INFO_FORMAT": fmt})
            print(x, y, layer, fmt, r.status_code, r.text[:400].replace("\n", " "))
r = httpx.get("https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht", timeout=30, params={
    "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": "ave:GebaeudeBauwerk", "COUNT": "4",
    "BBOX": "358600,5679600,358700,5679700,urn:ogc:def:crs:EPSG::25832"})
print(re.sub(r"<gml:posList[^>]*>[^<]*</gml:posList>", "", re.sub(r"<gml:[A-Za-z]+[^>]*>|</gml:[A-Za-z]+>", "", r.text))[-2500:])
