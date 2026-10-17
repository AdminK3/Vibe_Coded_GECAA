# SPDX-License-Identifier: Apache-2.0
"""Live check of every v4 endpoint; prints status, content type and the detail each parser relies on.

Run in an environment with internet: python scripts/probe_v4.py
"""

import io
import json
import re
import zipfile

import httpx

UA = {"User-Agent": "GECAA/0.4 probe"}
DWD = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/daily/kl/historical/"


class _Failed:
    is_success = False
    status_code = 0
    content = b""
    text = ""
    headers: dict = {}

    def json(self):
        return {}


def get(url, **params):
    try:
        r = httpx.get(url, params=params, headers=UA, timeout=60, follow_redirects=True)
    except Exception as exc:  # keep probing the other endpoints
        print(f"\n== {url}\n   FEHLER {type(exc).__name__}: {exc}")
        return _Failed()
    print(f"\n== {r.request.url}\n   {r.status_code} {r.headers.get('content-type')} {len(r.content)} B")
    return r


def main():
    # 1 LoD2 OGC API (CityJSON), Münster centre
    r = get("https://ogc-api.nrw.de/3dg/v1/collections/building/items", f="cityjson", limit="5",
            bbox="7.620,51.959,7.622,51.961")
    if r.is_success:
        objs = r.json().get("CityObjects", {})
        for k, o in list(objs.items())[:4]:
            print("  ", k, o.get("type"), o.get("name"), o.get("attributes"))
    r = get("https://www.wfs.nrw.de/geobasis/wfs_nw_alkis_vereinfacht", SERVICE="WFS", VERSION="2.0.0",
            REQUEST="GetFeature", TYPENAMES="ave:GebaeudeBauwerk", COUNT="3", SRSNAME="urn:ogc:def:crs:EPSG::25832",
            BBOX="404850,5757400,404950,5757500,urn:ogc:def:crs:EPSG::25832")
    print("   ALKIS oids (compare with LoD2 'name'):", re.findall(r"<ave:oid>([^<]+)", r.text)[:3])
    # 2 Zensus downloads (HEAD only) + atlas guess
    for f in ("Zensus2022_Bevoelkerungszahl.zip", "Alter_in_5_Altersklassen.zip"):
        h = httpx.head("https://www.destatis.de/static/DE/zensus/gitterdaten/" + f, headers=UA, timeout=60,
                       follow_redirects=True)
        print(f"\n== {f}: {h.status_code} {h.headers.get('content-type')} {h.headers.get('content-length')} B")
    # 3 DWD
    r = get(DWD + "KL_Tageswerte_Beschreibung_Stationen.txt")
    print("  ", r.content[:200].decode("latin-1").splitlines()[:3])
    r = get(DWD)
    z = re.findall(r"tageswerte_KL_01078_\d{8}_\d{8}_hist\.zip", r.text)
    print("   Düsseldorf zip:", z[:1])
    if z:
        zr = get(DWD + z[0])
        with zipfile.ZipFile(io.BytesIO(zr.content)) as zf:
            name = next(n for n in zf.namelist() if n.startswith("produkt_klima_tag"))
            print("  ", name, zf.read(name)[:300].decode("latin-1").splitlines()[:2])
    # 4 Planetary Computer STAC
    r = get("https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2")
    if r.is_success:
        ia = r.json().get("item_assets", {})
        print("   lwir11:", json.dumps(ia.get("lwir11", {}).get("raster:bands", ia.get("lwir11")))[:300])
        print("   qa_pixel present:", "qa_pixel" in ia)
    try:
        from gecaa.risk.lst import lst_map
        res = lst_map(6.7735, 51.2277, box_m=1000)
        print("   lst_map:", None if res is None else (res["scene_ids"], float(res["lst_c"].mean())))
    except Exception as e:  # noqa: BLE001
        print("   lst_map failed:", e)
    # 5 Bauleitplanung OGC API + candidate WMS (unverified)
    r = get("https://ogc-api.nrw.de/inspire-lu-bplan/v1/collections/spatialplan/items", f="json", limit="2",
            bbox="7.60,51.94,7.65,51.97")
    if r.is_success:
        for f in r.json().get("features", []):
            print("  ", {k: v for k, v in f["properties"].items() if not k.endswith(".href")})
    get("https://ogc-api.nrw.de/inspire-lu-bplan/v1/collections/spatialplan", f="json")
    print("\nNo WMS for Bauleitplanung verified; search geoportal.nrw for one before adding a MapLayer.")


if __name__ == "__main__":
    main()
