"""Deterministic metrological calculation engine.

This package is the authoritative source of mathematical results (PRD 11.1).
It never calls an AI service and, given identical inputs and the same rule
version, always produces identical outputs.
"""

from app.services.calculation_engine.engine import (
    ENGINE_VERSION,
    CalcContext,
    CalcOutcome,
    build_observation,
    evaluate,
    supported_test_codes,
)
from app.services.calculation_engine.types import ObservationRow

__all__ = [
    "ENGINE_VERSION",
    "CalcContext",
    "CalcOutcome",
    "ObservationRow",
    "build_observation",
    "evaluate",
    "supported_test_codes",
]
