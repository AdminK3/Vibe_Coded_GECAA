"""Cross-checks with WhiteboxTools (MIT, via the `whitebox` PyPI package) on temporary GeoTIFFs.

The `whitebox` package downloads the WhiteboxTools binary on first use; if it is missing and cannot be
fetched, every function raises RuntimeError.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np


def _wbt():
    try:
        import whitebox
    except ImportError as e:
        raise RuntimeError("WhiteboxTools unavailable: pip install 'gecaa[hydraulics]'") from e
    exe_dir = Path(whitebox.__file__).parent / "WBT"
    exe = exe_dir / ("whitebox_tools.exe" if os.name == "nt" else "whitebox_tools")
    if not exe.is_file():
        try:
            whitebox.download_wbt(verbose=False)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"WhiteboxTools binary unavailable (download failed: {e})") from e
        if not exe.is_file():
            raise RuntimeError(f"WhiteboxTools binary unavailable at {exe}")
    w = whitebox.WhiteboxTools()
    w.set_whitebox_dir(str(exe_dir))
    w.set_verbose_mode(False)
    return w


def available() -> bool:
    try:
        _wbt()
        return True
    except RuntimeError:
        return False


def _write(path, a, res):
    import rasterio
    from rasterio.transform import from_origin
    with rasterio.open(path, "w", driver="GTiff", height=a.shape[0], width=a.shape[1], count=1,
                       dtype="float32", crs="EPSG:25832", transform=from_origin(0, a.shape[0] * res, res, res),
                       nodata=-9999.0) as d:
        d.write(np.asarray(a, "float32"), 1)


def _read(path):
    import rasterio
    with rasterio.open(path) as d:
        a = d.read(1).astype("float64")
        if d.nodata is not None:
            a[a == d.nodata] = np.nan
    return a


def _run(fn, dem, res):
    w = _wbt()
    with tempfile.TemporaryDirectory(prefix="gecaa_wbt_") as t:
        src, out = os.path.join(t, "dem.tif"), os.path.join(t, "out.tif")
        _write(src, dem, res)
        if fn(w, src, out, t) != 0 or not os.path.exists(out):
            raise RuntimeError("WhiteboxTools run failed")
        return _read(out)


def depression_depth(dem, res) -> np.ndarray:
    """FillDepressions(dem) - dem in m."""
    filled = _run(lambda w, s, o, t: w.fill_depressions(s, o, fix_flats=False), dem, res)
    return np.maximum(filled - np.asarray(dem, "float64"), 0)


def wetness_index(dem, res) -> np.ndarray:
    """Topographic wetness index ln(a / tan b) from D-infinity specific catchment area."""
    def fn(w, s, o, t):
        sca, slope, fill = (os.path.join(t, n) for n in ("sca.tif", "slope.tif", "fill.tif"))
        rc = w.breach_depressions_least_cost(s, fill, dist=50, fill=True)
        rc = rc or w.d_inf_flow_accumulation(fill, sca, out_type="Specific Contributing Area")
        rc = rc or w.slope(fill, slope)
        return rc or w.wetness_index(sca, slope, o)
    return _run(fn, dem, res)
