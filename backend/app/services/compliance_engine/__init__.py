"""Deterministic compliance decision engine (PRD 11.4)."""

from app.services.compliance_engine.engine import COMPARATORS, decide_status, evaluate

__all__ = ["COMPARATORS", "decide_status", "evaluate"]
