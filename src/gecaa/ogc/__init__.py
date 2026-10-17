# SPDX-License-Identifier: Apache-2.0
"""OGC API - Processes via pygeoapi (optional extra `ogc`)."""

from __future__ import annotations

import os
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / "ogc" / "pygeoapi-config.yml"


def starlette_app():
    """The pygeoapi Starlette app for mounting under /ogc. Generates the OpenAPI document on first use."""
    from .. import data_dir

    os.environ.setdefault("PYGEOAPI_CONFIG", str(CONFIG))
    if "PYGEOAPI_OPENAPI" not in os.environ:
        from pygeoapi.openapi import generate_openapi_document
        out = data_dir() / "data" / "cache" / "ogc" / "openapi.yml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generate_openapi_document(Path(os.environ["PYGEOAPI_CONFIG"]), "yaml"), encoding="utf-8")
        os.environ["PYGEOAPI_OPENAPI"] = str(out)
    from pygeoapi.starlette_app import APP
    return APP
