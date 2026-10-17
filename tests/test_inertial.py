import numpy as np

from gecaa.risk import inertial


def _bowl(n=40):
    y, x = np.mgrid[0:n, 0:n]
    return (0.002 * ((x - n / 2) ** 2 + (y - n / 2) ** 2)).astype("f4")


def test_volume_conserved_closed_basin():
    b = np.zeros((40, 40), bool); b[10:14, 10:20] = True
    r = inertial.simulate(_bowl(), 2.0, [30, 80, 20], 1200, 600, coeff=0.9, manning=0.03,
                          buildings=b, boundary="closed")
    v = r.volume
    assert v["outflow_m3"] == 0
    assert abs(v["stored_m3"] - v["rain_in_m3"]) / v["rain_in_m3"] < 0.01
    assert r.depth_max.max() > 0.05 and not b[r.depth_max > 0].any()


def test_lake_at_rest_stays_at_rest():
    dem = _bowl()
    h0 = np.maximum(0.4 - dem, 0)  # flat water surface over an uneven bed
    r = inertial.simulate(dem, 2.0, 0.0, 0, 600, boundary="closed", h0=h0)
    assert r.velocity_max.max() < 1e-4
    assert np.abs(r.depth_final - h0).max() < 1e-5


def test_tilted_plane_drains_downhill():
    y, x = np.mgrid[0:30, 0:30]
    dem = (10 - 0.01 * x * 2.0).astype("f4")  # falls towards +x (east)
    r = inertial.simulate(dem, 2.0, 100.0, 600, 600)
    v = r.volume
    assert v["outflow_m3"] > 0.5 * v["rain_in_m3"]
    assert r.depth_max[:, -1].mean() > 3 * r.depth_max[:, 0].mean()
    assert abs(v["rel_error"]) < 0.01
