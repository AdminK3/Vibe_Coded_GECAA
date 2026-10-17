#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build LISFLOOD-FP 8.x (University of Bristol, GPL) from the official source release and set up the
# worker venv. The source is published on Zenodo (concept DOI 10.5281/zenodo.4073010; v8.1 = record
# 6912932). Override with LISFLOOD_SRC_URL=<zip or tar.gz> or LISFLOOD_SRC_DIR=<unpacked tree>.
# Usage: services/lisflood_worker/install.sh [prefix] [venv]  (defaults: /opt/lisflood, ~/.venvs/gecaa-lisflood)
set -euo pipefail
PREFIX="${1:-/opt/lisflood}"
VENV="${2:-$HOME/.venvs/gecaa-lisflood}"
HERE="$(cd "$(dirname "$0")" && pwd)"
URL="${LISFLOOD_SRC_URL:-https://zenodo.org/records/6912932/files/LISFLOOD-FP%20v8.1.zip?download=1}"
mkdir -p "$PREFIX/src" "$PREFIX/bin"
if [ -z "${LISFLOOD_SRC_DIR:-}" ]; then
  cd "$PREFIX/src"
  curl -fL --retry 3 -o lisflood-src.archive "$URL"
  case "$(file -b --mime-type lisflood-src.archive)" in
    application/zip) unzip -q -o lisflood-src.archive ;;
    *) tar xf lisflood-src.archive ;;
  esac
  LISFLOOD_SRC_DIR="$(dirname "$(find "$PREFIX/src" -name CMakeLists.txt -path '*lisflood*' -print -quit || true)")"
  # the Zenodo zip may wrap the tree in a nested archive
  if [ ! -f "$LISFLOOD_SRC_DIR/CMakeLists.txt" ]; then
    for a in $(find "$PREFIX/src" -mindepth 2 \( -name '*.zip' -o -name '*.tar.gz' \)); do
      (cd "$(dirname "$a")" && { unzip -q -o "$a" 2>/dev/null || tar xf "$a"; })
    done
    LISFLOOD_SRC_DIR="$(dirname "$(find "$PREFIX/src" -name CMakeLists.txt -print -quit)")"
  fi
fi
# CPU-only build (CUDA/NetCDF optional upstream)
cmake -S "$LISFLOOD_SRC_DIR" -B "$PREFIX/build" -DCMAKE_BUILD_TYPE=Release -D_NUMERIC_MODE=1 \
      -D_ONLY_RECT=1 -D_PROFILE_MODE=0 -D_BALANCE_TYPE=0 -D_DISABLE_WET_DRY=0 -D_CALCULATE_Q_MODE=0
cmake --build "$PREFIX/build" -j "$(nproc)"
BIN="$(find "$PREFIX/build" -maxdepth 2 -type f -name lisflood -perm -u+x -print -quit)"
install -m 755 "$BIN" "$PREFIX/bin/lisflood"
echo "LISFLOOD-FP binary: $PREFIX/bin/lisflood"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q -r "$HERE/requirements.txt"
echo "worker venv: $VENV  (start with services/lisflood_worker/run.sh)"
