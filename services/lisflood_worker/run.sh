#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Start the LISFLOOD-FP worker on port 8002 (GECAA finds it via GECAA_LISFLOOD_URL, default http://127.0.0.1:8002).
VENV="${1:-$HOME/.venvs/gecaa-lisflood}"
export LISFLOOD_BIN="${LISFLOOD_BIN:-/opt/lisflood/bin/lisflood}"
cd "$(dirname "$0")" && exec "$VENV/bin/uvicorn" app:app --host 127.0.0.1 --port "${PORT:-8002}"
