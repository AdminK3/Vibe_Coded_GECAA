# GECAA CLIMADA worker

Runs the [CLIMADA](https://github.com/CLIMADA-project/climada_python) risk engine (ETH Zürich, **GPL-3.0**)
as a separate HTTP service for GECAA. GECAA itself (Apache-2.0) never imports CLIMADA; the two exchange JSON only.
This folder is licensed under the GNU GPL v3 or later (see `LICENSE`).

## Start

```bash
services/climada_worker/install.sh      # own venv with Python 3.11, CLIMADA 6.1, GDAL matching the system
services/climada_worker/run.sh          # http://127.0.0.1:8001  (GET /health, POST /impact)
```
or `docker compose up climada-worker`. Without the worker, GECAA uses its built-in fallback engine (same method)
and says so in every result.

## Contract `POST /impact`

```json
{"rps": [10, 30, 100],
 "exposures": [{"id": "DENW...", "lon": 6.97, "lat": 51.25, "value": 512000, "cls": "residential"}],
 "impfs": {"residential": {"depth": [0, 0.5, 1, 1.5, 2, 3, 4, 5, 6], "mdr": [0, 0.25, 0.4, ...]}},
 "depth": [[0.0, 0.12, 0.31]],            // per exposure, per return period (m above threshold height)
 "measure_depth": [[0.0, 0.05, 0.2]],     // optional: depths with the adaptation measure
 "cost_eur": 40000, "years": 30, "discount_rate": 0.02}
```
Answer: `aai_agg` (expected annual damage), `eai` per exposure, `freq_curve` per return period and, with a
measure, `measure.{aai_agg, benefit_annual, benefit_npv, bcr}` from `CostBenefit` (measure hazard via
`Measure(hazard_set=...)`). Events are return-period bands: f_i = 1/RP_i − 1/RP_(i+1).
