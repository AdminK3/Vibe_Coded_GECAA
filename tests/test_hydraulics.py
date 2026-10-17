import numpy as np
import pytest

from gecaa.risk import hydraulics_client, hydrology, whitebox_check


def test_client_falls_back_without_worker(monkeypatch):
    monkeypatch.setenv("GECAA_LISFLOOD_URL", "http://127.0.0.1:9")  # nothing listens there
    y, x = np.mgrid[0:20, 0:20]
    dem = 0.01 * ((x - 10) ** 2 + (y - 10) ** 2)
    r = hydraulics_client.simulate(dem, 2.0, [30, 60], coeff=0.9, manning=0.03, mask=None,
                                   duration_s=600, drain_s=300)
    assert r["engine"] == "gecaa-inertial" and r["version"]
    assert r["depth_max"].shape == (20, 20) and r["depth_max"].max() > 0
    assert r["velocity_max"] is not None
    with pytest.raises(RuntimeError):
        hydraulics_client.simulate(dem, 2.0, 30, engine="lisflood")


@pytest.mark.skipif(not whitebox_check.available(), reason="WhiteboxTools binary unavailable")
def test_whitebox_depression_depth_matches_priority_flood():
    dem = np.full((30, 30), 10.0)
    dem[0, :] = dem[:, 0] = 9.0  # outlet edge
    dem[10:15, 10:15] = 9.4  # 0.6 m pit
    dem[20:23, 5:8] = 9.85
    ref = hydrology.priority_flood(dem) - dem
    got = whitebox_check.depression_depth(dem, 2.0)
    assert np.nanmax(np.abs(got - ref)) < 0.01
