# SPDX-License-Identifier: Apache-2.0
"""GECAA - Governmental Engine for Climate Adaptation Action."""

from pathlib import Path

__version__ = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent.parent


def data_dir() -> Path:
    """Directory holding knowledge files, blueprints and sample data.

    Set GECAA_HOME to point at another checkout or a mounted volume.
    """
    import os

    return Path(os.environ.get("GECAA_HOME", REPO_DIR))
