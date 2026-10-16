#!/usr/bin/env bash
# Install the CLIMADA worker into its own virtual environment (Python 3.11; CLIMADA needs < 3.13).
# Usage: services/climada_worker/install.sh [venv-dir]   (default: ~/.venvs/gecaa-climada)
set -euo pipefail
VENV="${1:-$HOME/.venvs/gecaa-climada}"
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v uv >/dev/null || pip install -q uv
# GDAL Python bindings need the system library with headers
if ! command -v gdal-config >/dev/null; then
  if command -v apt-get >/dev/null; then
    SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq libgdal-dev >/dev/null
  fi
fi
GDAL_VERSION="$(gdal-config --version)"
uv venv -q -p 3.11 "$VENV"
uv pip install -q -p "$VENV/bin/python" "gdal==${GDAL_VERSION}" -r "$HERE/requirements.txt"
"$VENV/bin/python" -c "import climada, importlib.metadata as m; print('CLIMADA', m.version('climada'), 'installed in $VENV')"
