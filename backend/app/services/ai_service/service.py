"""AI facade: feature toggles, provider selection, traceability, degradation."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AIEvent, Attachment, SystemSetting, TestInstance
from app.services.ai_service.base import (
    AnomalyReport,
    AssistantAnswer,
    ClassificationResult,
    ConsistencyReport,
    ExtractionResult,
)
from app.services.ai_service.consistency import build_context as build_consistency_context
from app.services.ai_service.openai_provider import OpenAICompatibleProvider
from app.services.ai_service.stub_provider import StubAIProvider
from app.utils.decimals import decimal_str

FEATURE_NAMEPLATE = "nameplate_ocr"
FEATURE_ANOMALY = "anomaly_detection"
FEATURE_CLASSIFY = "document_classification"
FEATURE_ASSISTANT = "r76_assistant"
FEATURE_CONSISTENCY = "report_consistency"


def build_provider():
    if settings.AI_PROVIDER == "stub" or not settings.AI_API_KEY:
        return StubAIProvider()
    return OpenAICompatibleProvider()


class AIService:
    """Wraps a provider with governance: toggles, audit events, degradation."""

    def __init__(self, db: Session, *, actor=None, provider=None) -> None:
        self.db = db
        self.actor = actor
        self.provider = provider or build_provider()
        self._settings_cache: dict[str, bool] | None = None

    # ------------------------------------------------------------ toggles
    def _setting_enabled(self, key: str, default: bool) -> bool:
        if self._settings_cache is None:
            self._settings_cache = {}
            rows = self.db.execute(
                select(SystemSetting).where(SystemSetting.category == "ai")
            ).scalars().all()
            for row in rows:
                value = row.value or {}
                if isinstance(value, dict) and "enabled" in value:
                    # Cache per stored key: previously every row overwrote the
                    # same slot, so one toggle silently masked all the others.
                    self._settings_cache[row.key] = bool(value["enabled"])
        return self._settings_cache.get(key, default)

    def feature_enabled(self, feature_code: str) -> bool:
        if not settings.AI_ENABLED:
            return False
        mapping = {
            FEATURE_NAMEPLATE: ("ai.nameplate_extract", settings.AI_NAME_PLATE_EXTRACT),
            FEATURE_ANOMALY: ("ai.anomaly_detection", settings.AI_ANOMALY_DETECTION),
            FEATURE_CLASSIFY: ("ai.document_classification", settings.AI_DOCUMENT_CLASSIFICATION),
            FEATURE_ASSISTANT: ("ai.knowledge_assistant", settings.AI_KNOWLEDGE_ASSISTANT),
            FEATURE_CONSISTENCY: ("ai.report_consistency", settings.AI_REPORT_CONSISTENCY),
        }
        key, default = mapping.get(feature_code, ("ai.enabled", True))
        return self._setting_enabled(key, default)

    # ------------------------------------------------------- traceability
    def _record(
        self,
        *,
        feature_code: str,
        input_reference: str | None,
        output_summary: dict[str, Any] | None,
        confidence: float | None,
        degraded: bool,
        latency_ms: int,
        error_message: str | None = None,
        case_id: uuid.UUID | None = None,
        test_instance_id: uuid.UUID | None = None,
        attachment_id: uuid.UUID | None = None,
    ) -> AIEvent:
        event = AIEvent(
            case_id=case_id,
            test_instance_id=test_instance_id,
            attachment_id=attachment_id,
            feature_code=feature_code,
            provider=self.provider.name,
            model=getattr(self.provider, "vision_model", None) or getattr(self.provider, "text_model", None),
            input_reference=input_reference,
            output_summary=output_summary,
            confidence=None if confidence is None else str(confidence),
            latency_ms=latency_ms,
            degraded=degraded,
            error_message=error_message,
            created_by=getattr(self.actor, "id", None),
        )
        self.db.add(event)
        return event

    # ------------------------------------------------------------ features
    def nameplate_extract(
        self,
        *,
        image_bytes: bytes | None,
        filename: str,
        context: dict | None = None,
        case=None,
        test_instance_id: uuid.UUID | None = None,
        attachment_id: uuid.UUID | None = None,
    ) -> ExtractionResult:
        if not self.feature_enabled(FEATURE_NAMEPLATE):
            return ExtractionResult(
                available=False,
                provider="disabled",
                degraded=True,
                message="Nameplate extraction is disabled for this organisation. Enter values manually.",
            )
        started = time.perf_counter()
        result = self.provider.extract_nameplate(
            image_bytes=image_bytes, filename=filename, context=context or {}
        )
        self._record(
            feature_code=FEATURE_NAMEPLATE,
            input_reference=filename,
            output_summary=result.as_dict(),
            confidence=result.confidence,
            degraded=result.degraded or not result.available,
            latency_ms=int((time.perf_counter() - started) * 1000),
            case_id=getattr(case, "id", None),
            test_instance_id=test_instance_id,
            attachment_id=attachment_id,
        )
        return result

    def anomaly_check(self, *, case, test_instance: TestInstance) -> AnomalyReport:
        if not self.feature_enabled(FEATURE_ANOMALY):
            return AnomalyReport(
                available=False,
                provider="disabled",
                message="Anomaly detection is disabled for this organisation.",
            )
        rows = [
            {
                "observation_no": obs.observation_no,
                "load": decimal_str(obs.load),
                "indication": decimal_str(obs.indication),
                "additional_load": decimal_str(obs.additional_load),
                "value": decimal_str(obs.value),
                "unit": obs.unit,
            }
            for obs in test_instance.observations
        ]
        instrument = {
            "instrument_class": case.instrument.instrument_class,
            "e": decimal_str(case.instrument.verification_scale_interval),
            "unit": case.instrument.unit,
        }
        started = time.perf_counter()
        report = self.provider.detect_anomaly(
            test_code=test_instance.definition.test_code, rows=rows, instrument=instrument
        )
        self._record(
            feature_code=FEATURE_ANOMALY,
            input_reference=f"test_instance:{test_instance.id}",
            output_summary=report.as_dict(),
            confidence=None,
            degraded=report.degraded,
            latency_ms=int((time.perf_counter() - started) * 1000),
            case_id=case.id,
            test_instance_id=test_instance.id,
        )
        return report

    def classify_document(self, *, attachment: Attachment) -> ClassificationResult:
        if not self.feature_enabled(FEATURE_CLASSIFY):
            return ClassificationResult(
                available=False,
                provider="disabled",
                category=attachment.category or "other",
                message="Document classification is disabled for this organisation.",
            )
        started = time.perf_counter()
        result = self.provider.classify_document(
            filename=attachment.original_filename,
            content_type=attachment.content_type,
            caption=attachment.caption,
        )
        self._record(
            feature_code=FEATURE_CLASSIFY,
            input_reference=attachment.original_filename,
            output_summary=result.as_dict(),
            confidence=result.confidence,
            degraded=result.degraded,
            latency_ms=int((time.perf_counter() - started) * 1000),
            case_id=attachment.case_id,
            attachment_id=attachment.id,
        )
        if result.available:
            attachment.category = result.category
            attachment.category_source = "ai"
            attachment.classification_confidence = (
                None if result.confidence is None else str(result.confidence)
            )
        return result

    def consistency_check(self, *, case) -> ConsistencyReport:
        """Advisory review of the recorded case against the report content.

        The check never writes a value and never changes a result: it compares
        what the report will publish with what the case records.
        """
        if not self.feature_enabled(FEATURE_CONSISTENCY):
            return ConsistencyReport(
                available=False,
                provider="disabled",
                message="Report-consistency checking is disabled for this organisation.",
            )
        context = build_consistency_context(self.db, case)
        started = time.perf_counter()
        report = self.provider.check_report_consistency(context=context)
        self._record(
            feature_code=FEATURE_CONSISTENCY,
            input_reference=f"case:{case.id}",
            output_summary=report.as_dict(),
            confidence=None,
            degraded=report.degraded,
            latency_ms=int((time.perf_counter() - started) * 1000),
            case_id=case.id,
        )
        return report

    def answer_r76(self, *, question: str, sources: list[dict]) -> AssistantAnswer:
        if not self.feature_enabled(FEATURE_ASSISTANT):
            return AssistantAnswer(
                available=False,
                provider="disabled",
                grounded=False,
                answer="The R 76 assistant is disabled for this organisation.",
                message="Feature disabled.",
            )
        started = time.perf_counter()
        answer = self.provider.answer_r76(question=question, sources=sources)
        self._record(
            feature_code=FEATURE_ASSISTANT,
            input_reference=question[:200],
            output_summary={
                "citations": answer.citations,
                "grounded": answer.grounded,
                "answer_preview": answer.answer[:400],
            },
            confidence=None,
            degraded=answer.degraded,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return answer


def get_ai_service(db: Session, *, actor=None) -> AIService:
    return AIService(db, actor=actor)
