# SPDX-License-Identifier: Apache-2.0
"""GECAA v2: K3 logic (PASSED / OPEN / BLOCKED), evaluation units and live NRW geodata."""

from .engine import assess, evaluate_unit, load_logic, load_logics, site_result, unit_result, validate_logic
from .model import InputValue, Quality, Result

__all__ = ["assess", "evaluate_unit", "load_logic", "load_logics", "site_result", "unit_result",
           "validate_logic", "InputValue", "Quality", "Result"]
