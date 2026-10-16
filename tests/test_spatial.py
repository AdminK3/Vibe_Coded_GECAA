# SPDX-License-Identifier: Apache-2.0
import pytest

from gecaa.spatial import load_parcels, site_evidence
from gecaa.spatial.analysis import contamination_evidence, space_evidence


@pytest.fixture(scope="module")
def parcels(root):
    return {p.id: p for p in load_parcels(root / "data" / "samples" / "parcels.geojson")}


def test_sample_parcels_load(parcels):
    assert set(parcels) == {"demo-a", "demo-b", "demo-c"}
    assert parcels["demo-a"].area_m2() == pytest.approx(70 * 70)


def test_sandy_west_parcel(gecaa_app, blueprint, parcels):
    ev = site_evidence(gecaa_app.dataset, parcels["demo-a"], blueprint.parameters)
    assert ev["kf"]["distribution"]["suitable"] > 0.95
    assert ev["groundwater"]["distribution"]["sufficient"] > 0.95
    assert ev["protection_zone"]["distribution"] == {"none": 1.0}
    assert ev["slope"]["distribution"]["flat"] > 0.95


def test_clay_east_parcel_in_zone_iii(gecaa_app, blueprint, parcels):
    ev = site_evidence(gecaa_app.dataset, parcels["demo-b"], blueprint.parameters)
    assert ev["kf"]["distribution"]["too_low"] > 0.7
    assert ev["protection_zone"]["distribution"] == {"zone_iii": 1.0}


def test_parcel_given_in_wgs84_is_reprojected(gecaa_app, blueprint, parcels):
    from rasterio.warp import transform_geom
    geom = transform_geom("EPSG:25832", "EPSG:4326", parcels["demo-a"].geometry)
    [p] = load_parcels({"type": "Feature", "properties": {"id": "x"}, "geometry": geom})
    ev = site_evidence(gecaa_app.dataset, p, blueprint.parameters)
    assert ev["kf"]["distribution"]["suitable"] > 0.95


def test_parcel_outside_data_is_an_error(gecaa_app, blueprint):
    far = {"type": "Polygon", "coordinates": [[[13.40, 52.52], [13.41, 52.52], [13.41, 52.53], [13.40, 52.52]]]}
    [p] = load_parcels(far)
    with pytest.raises(ValueError):
        site_evidence(gecaa_app.dataset, p, blueprint.parameters)


def test_context_evidence(blueprint):
    params = blueprint.parameters
    assert contamination_evidence("unknown", params)["distribution"]["suspected"] == params["unknown_contamination_p"]
    assert space_evidence(1000, 50, params)["distribution"] == {"insufficient": 1.0}
    assert space_evidence(1000, 150, params)["distribution"] == {"sufficient": 1.0}
    assert space_evidence(None, None, params)["distribution"] == {"sufficient": 0.5, "insufficient": 0.5}
