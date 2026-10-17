# GECAA LISFLOOD-FP worker

Runs [LISFLOOD-FP 8](https://doi.org/10.5281/zenodo.4073010) (University of Bristol; the source release is
licensed GPL — v8.1 on Zenodo states **GPL-2.0-only**) as a separate HTTP service for GECAA. GECAA itself
(Apache-2.0) never links or imports LISFLOOD-FP; the worker calls the binary as a subprocess and both sides
exchange JSON only. This folder is licensed under the GNU GPL v3 or later (see `LICENSE`).

## Start

```bash
services/lisflood_worker/install.sh      # downloads + CMake-builds LISFLOOD-FP into ~/.local/lisflood, own venv
services/lisflood_worker/run.sh          # http://127.0.0.1:8002  (GET /health, POST /simulate)
```
Without the worker, GECAA uses its built-in local-inertial solver (`gecaa.risk.inertial`, same Bates et al.
2010 scheme as LISFLOOD-FP ACC) and says so in every result (`engine` field).

## Contract `POST /simulate`

```json
{"dem": "<base64 float32, ny*nx, north row first>", "nx": 200, "ny": 200, "res": 2.0,
 "rain_mm_h": [20, 60, 40, 10], "dt_s": 900, "duration_s": 5400,
 "manning": 0.03,               // or base64 float32 grid
 "mask": "<base64 uint8>"}      // optional building mask, cells raised by 10 m
```
Answer: `depth_max` (base64 float32), `engine`, `version`, `log_tail`. Rain blocks of `dt_s` seconds, then zero
until `duration_s`; all four edges are `FREE` outflow boundaries; solver `acceleration` (local inertial).
Env: `LISFLOOD_BIN` (default `~/.local/lisflood/bin/lisflood`), `LISFLOOD_TIMEOUT_S` (default 900).

## Licences
This worker (app.py, install.sh, run.sh) is GPL-3.0-or-later. **LISFLOOD-FP itself is GPL-2.0-only**: it is
downloaded and built separately by `install.sh` and only executed as a separate program (subprocess), never linked
or combined with the worker's code. GECAA talks to the worker over HTTP only.
