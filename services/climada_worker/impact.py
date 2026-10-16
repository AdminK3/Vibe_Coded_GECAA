# SPDX-License-Identifier: GPL-3.0-or-later
# GECAA CLIMADA worker - runs the CLIMADA risk engine (GPL-3.0) as a separate service.
# Copyright 2026 Khoch3 KlimaKarten UG. Licensed under the GNU General Public License v3 or later (see LICENSE).
"""Impact and cost-benefit with CLIMADA for buildings and return-period flood depths.

Hazard: one centroid per building (water depth at the building), one event per return period with banded
frequencies f_i = 1/RP_i - 1/RP_(i+1). Exposures: building values. Impact functions: depth-damage curves
sent by GECAA (JRC). Measure: a second hazard (water depths with the measure), passed to CLIMADA as
Measure(hazard_set=<hdf5 file>), and CostBenefit over `years` with a constant discount rate.
"""

from __future__ import annotations

import os
import tempfile
import warnings
from importlib.metadata import version

import numpy as np

warnings.filterwarnings("ignore")

import geopandas as gpd  # noqa: E402
from scipy import sparse  # noqa: E402

from climada.engine import CostBenefit, ImpactCalc  # noqa: E402
from climada.entity import DiscRates, Entity, Exposures, ImpactFunc, ImpactFuncSet, Measure, MeasureSet  # noqa: E402
from climada.hazard import Centroids, Hazard  # noqa: E402

HAZ = "FL"
PRESENT_YEAR = 2026


def _hazard(cent, rps, depth_rows) -> Hazard:
    depth = np.asarray(depth_rows, dtype=float).T          # events x centroids
    inv = 1.0 / np.asarray(rps, float)
    freq = inv - np.append(inv[1:], 0.0)
    haz = Hazard(haz_type=HAZ, units="m", centroids=cent, event_id=np.arange(1, len(rps) + 1),
                 event_name=[f"RP{r}" for r in rps], frequency=freq, date=np.ones(len(rps), int),
                 orig=np.ones(len(rps), bool), intensity=sparse.csr_matrix(depth),
                 fraction=sparse.csr_matrix((depth > 0).astype(float)))
    haz.check()
    return haz


def compute(p: dict) -> dict:
    order = np.argsort(p["rps"])
    rps = [p["rps"][i] for i in order]
    depth = [[row[i] for i in order] for row in p["depth"]]
    exps = p["exposures"]
    if not exps:
        return {"engine": "CLIMADA", "version": version("climada"), "aai_agg": 0.0, "eai": {},
                "freq_curve": [{"rp": r, "eur": 0.0} for r in rps]}
    lon = np.array([e["lon"] for e in exps])
    lat = np.array([e["lat"] for e in exps])
    cent = Centroids.from_lat_lon(lat, lon)
    classes = sorted(p["impfs"])
    impf_id = {c: i + 1 for i, c in enumerate(classes)}
    impfs = ImpactFuncSet([
        ImpactFunc(haz_type=HAZ, id=impf_id[c], name=c, intensity=np.asarray(p["impfs"][c]["depth"], float),
                   mdd=np.asarray(p["impfs"][c]["mdr"], float), paa=np.ones(len(p["impfs"][c]["depth"])),
                   intensity_unit="m") for c in classes])
    gdf = gpd.GeoDataFrame({"value": [e["value"] for e in exps], f"impf_{HAZ}": [impf_id[e["cls"]] for e in exps],
                            f"centr_{HAZ}": np.arange(len(exps))},
                           geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
    exp = Exposures(gdf, value_unit="EUR", ref_year=PRESENT_YEAR)
    haz = _hazard(cent, rps, depth)
    imp = ImpactCalc(exp, impfs, haz).impact(save_mat=False, assign_centroids=False)
    out = {"engine": "CLIMADA ImpactCalc", "version": version("climada"), "aai_agg": float(imp.aai_agg),
           "eai": {e["id"]: float(v) for e, v in zip(exps, imp.eai_exp)},
           "freq_curve": [{"rp": r, "eur": float(v)} for r, v in zip(rps, imp.at_event)]}

    if p.get("measure_depth") is not None:
        mdepth = [[row[i] for i in order] for row in p["measure_depth"]]
        haz_m = _hazard(cent, rps, mdepth)
        imp_m = ImpactCalc(exp, impfs, haz_m).impact(save_mat=False, assign_centroids=False)
        years = int(p.get("years", 30))
        rate = float(p.get("discount_rate", 0.02))
        cost = p.get("cost_eur")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "measure_hazard.h5")
            haz_m.write_hdf5(path)
            meas = Measure(name="Maßnahme", haz_type=HAZ, cost=float(cost) if cost else 1.0, hazard_set=path,
                           color_rgb=np.array([0.1, 0.5, 0.3]))
            mset = MeasureSet(measure_list=[meas])
            mset.check()
            yrs = np.arange(PRESENT_YEAR, PRESENT_YEAR + years)
            ent = Entity(exposures=exp, disc_rates=DiscRates(years=yrs, rates=np.full(yrs.size, rate)),
                         impact_func_set=impfs, measure_set=mset)
            cb = CostBenefit()
            cb.calc(haz, ent, future_year=int(yrs[-1]), assign_centroids=False)
            benefit = float(cb.benefit["Maßnahme"])
        out["measure"] = {"aai_agg": float(imp_m.aai_agg),
                          "freq_curve": [{"rp": r, "eur": float(v)} for r, v in zip(rps, imp_m.at_event)],
                          "benefit_annual": float(imp.aai_agg - imp_m.aai_agg), "benefit_npv": benefit,
                          "bcr": (benefit / float(cost)) if cost else None,
                          "climada_cost_benefit_ratio": float(cb.cost_ben_ratio["Maßnahme"]) if cost else None}
    return out


if __name__ == "__main__":       # JSON on stdin -> JSON on stdout (used by GECAA's agreement test)
    import contextlib
    import io
    import json
    import sys

    payload = json.load(sys.stdin)
    with contextlib.redirect_stdout(io.StringIO()):       # CostBenefit prints a table
        result = compute(payload)
    json.dump(result, sys.stdout)
