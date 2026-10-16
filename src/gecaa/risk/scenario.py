# SPDX-License-Identifier: Apache-2.0
# magnitude_frequency_shift: ported from the CLIMAAX HEAVY_RAINFALL workflow
# (01_Extreme_precipitation/EXTREME_PRECIPITATION_Risk_Assessment.ipynb), https://github.com/CLIMAAX/HEAVY_RAINFALL,
# licensed Apache-2.0 OR CC-BY-4.0. See NOTICE.
"""Climate scenarios for heavy rain.

Scaling: short-duration rain extremes grow with warming at roughly the Clausius-Clapeyron rate of about 7 % per
kelvin (Lenderink & van Meijgaard 2008, Nature Geoscience 1, 511-514). GECAA uses it as a sensitivity, not as a
projection: "+1 K" and "+2 K" scale today's KOSTRA depths by 1.07 and 1.07^2.
"""

from __future__ import annotations

import numpy as np

CC_RATE = 0.07
SOURCE_CC = "Clausius-Clapeyron-Skalierung ≈ 7 %/K (Lenderink & van Meijgaard 2008, Nat. Geosci. 1, 511–514)"
SCENARIOS = {"heute": 0.0, "+1 K": 1.0, "+2 K": 2.0}


def factor(warming_k: float) -> float:
    return float((1 + CC_RATE) ** warming_k)


def magnitude_frequency_shift(th_int, th_freq, rp_base, prec_base, rp_future, prec_future):
    """CLIMAAX: new return period and magnitude of a critical threshold under climate change.

    th_int: intensity of the critical threshold (mm); th_freq: its return period (years);
    rp_*/prec_*: return periods and precipitation of baseline and future (same duration).
    Returns (shifted return period, shifted precipitation, factor) as in the CLIMAAX workflow.
    """
    ret_per_bs, prec_bs = np.asarray(rp_base, float), np.asarray(prec_base, float)
    ret_per_proj, prec_proj = np.asarray(rp_future, float), np.asarray(prec_future, float)
    # translation of threshold into baseline (interpolation of precipitation vs. log(return periods))
    prec_base_aux = np.interp(np.log(th_freq), np.log(ret_per_bs), prec_bs)
    # change in frequency
    shift_ret_per = np.interp(prec_base_aux, prec_proj, np.log(ret_per_proj))
    shift_ret_per = np.ceil(np.exp(shift_ret_per))
    # change in magnitude using proxy of baseline
    prec_proj_aux = np.interp(np.log(th_freq), np.log(ret_per_proj), prec_proj)
    factor_prec = prec_proj_aux / prec_base_aux
    shift_prec = th_int * factor_prec
    return float(shift_ret_per), round(float(shift_prec), 2), round(float(factor_prec), 2)


def return_period_shift(kostra: dict[int, float], warming_k: float, rp: int = 100) -> dict:
    """How often does today's `rp`-year rain occur with `warming_k` of warming? (CLIMAAX method on KOSTRA)."""
    rps = sorted(kostra)
    base = [kostra[r] for r in rps]
    fut = [kostra[r] * factor(warming_k) for r in rps]
    new_rp, new_mm, f = magnitude_frequency_shift(kostra[rp], rp, rps, base, rps, fut)
    return {"rp_today": rp, "mm_today": kostra[rp], "warming_k": warming_k, "rp_future": new_rp,
            "mm_future_same_rp": new_mm, "factor": f,
            "note": "unterhalb des kleinsten tabellierten Wiederkehrintervalls" if new_rp <= rps[0] else None}
