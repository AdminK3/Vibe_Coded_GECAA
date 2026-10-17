# SPDX-License-Identifier: GPL-3.0-or-later
import base64

import numpy as np
from fastapi.testclient import TestClient

import app as worker


def test_case_files(tmp_path):
    dem = np.arange(12, dtype="<f4").reshape(3, 4)
    req = worker.SimRequest(dem=base64.b64encode(dem.tobytes()).decode(), nx=4, ny=3, res=2,
                            rain_mm_h=[10, 30], dt_s=600, duration_s=1800, manning=0.04,
                            mask=base64.b64encode(np.eye(3, 4, dtype="u1").tobytes()).decode())
    worker.build_case(req, tmp_path)
    assert "fpfric 0.04" in (tmp_path / "run.par").read_text()
    a = worker.read_ascii(tmp_path / "dem.asc")
    assert a[0, 0] == 10 and a[0, 1] == 1
    assert "4 seconds" in (tmp_path / "rain.rain").read_text()


def test_health_without_binary(monkeypatch):
    monkeypatch.setattr(worker, "BIN", "/nonexistent/lisflood")
    c = TestClient(worker.app)
    assert c.get("/health").json()["ok"] is False
    assert c.post("/simulate", json={"dem": "", "nx": 0, "ny": 0, "res": 2, "rain_mm_h": [1],
                                     "dt_s": 1, "duration_s": 1}).status_code == 503
