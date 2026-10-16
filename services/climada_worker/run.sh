#!/usr/bin/env bash
# Start the CLIMADA worker on port 8001 (GECAA finds it via GECAA_CLIMADA_URL, default http://127.0.0.1:8001).
VENV="${1:-$HOME/.venvs/gecaa-climada}"
cd "$(dirname "$0")" && exec "$VENV/bin/uvicorn" app:app --host 127.0.0.1 --port "${PORT:-8001}"
