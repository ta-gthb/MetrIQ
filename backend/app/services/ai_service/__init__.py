"""AI assistance layer.

AI is deliberately bounded (PRD 12): it may extract, flag, classify and answer
questions, but it never decides metrological compliance and never writes an
authoritative value without human confirmation.
"""

from app.services.ai_service.base import (
    AIUnavailable,
    AnomalyFinding,
    AnomalyReport,
    AssistantAnswer,
    ClassificationResult,
    ExtractionField,
    ExtractionResult,
)
from app.services.ai_service.service import (
    FEATURE_ANOMALY,
    FEATURE_ASSISTANT,
    FEATURE_CLASSIFY,
    FEATURE_NAMEPLATE,
    AIService,
    get_ai_service,
)

__all__ = [
    "AIUnavailable",
    "AIService",
    "AnomalyFinding",
    "AnomalyReport",
    "AssistantAnswer",
    "ClassificationResult",
    "FEATURE_ANOMALY",
    "FEATURE_ASSISTANT",
    "FEATURE_CLASSIFY",
    "FEATURE_NAMEPLATE",
    "ExtractionField",
    "ExtractionResult",
    "get_ai_service",
]
