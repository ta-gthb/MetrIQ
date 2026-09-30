"""Provider-neutral AI contracts (PRD 12.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class AIUnavailable(RuntimeError):
    """Raised when an AI feature cannot run; the core app must keep working."""


@dataclass(slots=True)
class ExtractionField:
    key: str
    label: str
    value: str | None = None
    confidence: float | None = None
    low_confidence: bool = False
    source: str = "ai"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
            "source": self.source,
        }


@dataclass(slots=True)
class ExtractionResult:
    available: bool
    provider: str
    model: str | None = None
    fields: list[ExtractionField] = field(default_factory=list)
    confidence: float | None = None
    degraded: bool = False
    message: str = ""
    raw: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "fields": [item.as_dict() for item in self.fields],
            "confidence": self.confidence,
            "degraded": self.degraded,
            "message": self.message,
            "advisory_only": True,
        }


@dataclass(slots=True)
class AnomalyFinding:
    row: int
    field: str
    kind: str
    severity: str
    message: str
    value: str | None = None
    score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "field": self.field,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "score": self.score,
        }


@dataclass(slots=True)
class AnomalyReport:
    available: bool
    provider: str
    findings: list[AnomalyFinding] = field(default_factory=list)
    checked_rows: int = 0
    degraded: bool = False
    message: str = ""

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "findings": [item.as_dict() for item in self.findings],
            "checked_rows": self.checked_rows,
            "has_findings": self.has_findings,
            "degraded": self.degraded,
            "message": self.message,
            "advisory_only": True,
            "alters_compliance": False,
        }


@dataclass(slots=True)
class ClassificationResult:
    available: bool
    provider: str
    category: str = "other"
    confidence: float | None = None
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "category": self.category,
            "confidence": self.confidence,
            "alternatives": self.alternatives,
            "degraded": self.degraded,
            "message": self.message,
            "advisory_only": True,
        }


@dataclass(slots=True)
class AssistantAnswer:
    available: bool
    provider: str
    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    grounded: bool = True
    degraded: bool = False
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "answer": self.answer,
            "citations": self.citations,
            "grounded": self.grounded,
            "degraded": self.degraded,
            "message": self.message,
        }


@dataclass(slots=True)
class ConsistencyFinding:
    """One advisory observation about the recorded case versus its report."""

    code: str
    message: str
    severity: str = "warning"
    scope: str = "case"
    test_code: str | None = None
    citation: str | None = None
    expected: str | None = None
    observed: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "scope": self.scope,
            "test_code": self.test_code,
            "citation": self.citation,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(slots=True)
class ConsistencyReport:
    """Advisory result of a report-consistency review; never a decision."""

    available: bool
    provider: str
    findings: list[ConsistencyFinding] = field(default_factory=list)
    checks_run: int = 0
    degraded: bool = False
    message: str = ""

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self.provider,
            "findings": [item.as_dict() for item in self.findings],
            "checks_run": self.checks_run,
            "has_findings": self.has_findings,
            "degraded": self.degraded,
            "message": self.message,
            "advisory_only": True,
            "alters_compliance": False,
        }


class AIProvider(Protocol):
    name: str

    def extract_nameplate(self, *, image_bytes: bytes | None, filename: str, context: dict) -> ExtractionResult: ...

    def detect_anomaly(self, *, test_code: str, rows: list[dict], instrument: dict) -> AnomalyReport: ...

    def check_report_consistency(self, *, context: dict) -> ConsistencyReport: ...

    def classify_document(self, *, filename: str, content_type: str | None, caption: str | None) -> ClassificationResult: ...

    def answer_r76(self, *, question: str, sources: list[dict]) -> AssistantAnswer: ...
