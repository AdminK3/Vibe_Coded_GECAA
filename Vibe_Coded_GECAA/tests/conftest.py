# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("GECAA_HOME", str(ROOT))


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def gecaa_app():
    from gecaa.service import Gecaa
    return Gecaa.from_home(ROOT)


@pytest.fixture(scope="session")
def blueprint(gecaa_app):
    return gecaa_app.blueprints["mulden_rigolen_system_nrw"]
