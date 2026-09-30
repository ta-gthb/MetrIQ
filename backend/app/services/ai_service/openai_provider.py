"""Optional hosted-model provider (OpenAI-compatible Chat Completions).

Design notes (PRD 12.5, 12.6, 19.2):

* the provider is replaceable - set ``AI_PROVIDER`` and ``AI_BASE_URL`` to point
  at any OpenAI-compatible endpoint;
* anomaly detection stays local and statistical, so observation tables are never
  shipped to a third party for the highest-volume feature;
* the R 76 assistant retrieves locally first and only then asks the model to
  summarise the retrieved excerpts, so answers remain grounded and cited;
* every failure degrades to the deterministic stub provider instead of raising.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from typing import Any

import httpx

from app.config import settings
from app.services.ai_service.base import (
    AnomalyReport,
    AssistantAnswer,
    ClassificationResult,
    ConsistencyReport,
    ExtractionField,
    ExtractionResult,
)
from app.services.ai_service.stub_provider import FIELD_LABELS, FIELD_ORDER, StubAIProvider

SYSTEM_PROMPT = (
    "You are the MetrIQ metrology assistant. You only answer from the supplied, approved "
    "configuration excerpts. If the excerpts do not contain the answer, say so plainly and "
    "direct the user to the controlled copy of OIML R 76. Never invent a rule, a tolerance or "
    "a clause number. Never state a PASS/FAIL compliance decision: compliance is decided only "
    "by the deterministic rules engine."
)

NAMEPLATE_PROMPT = (
    "Read this nameplate photograph. Return STRICT JSON only, shaped as "
    '{"fields": {"manufacturer": "...", "model": "...", "serial_number": "...", "class": "...", '
    '"max": "...", "min": "...", "e": "...", "d": "..."}, "confidence": 0.0}. '
    "Use null for any field you cannot read with confidence. Include the unit in the value "
    "string for max, min, e and d, for example '100 g'. Do not guess."
)


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self) -> None:
        self.base_url = (settings.AI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        self.api_key = settings.AI_API_KEY or ""
        self.vision_model = settings.AI_VISION_MODEL
        self.text_model = settings.AI_TEXT_MODEL
        self._fallback = StubAIProvider()

    # ------------------------------------------------------------ helpers
    def _chat(self, messages: list[dict[str, Any]], *, model: str | None = None,
              json_mode: bool = False) -> str:
        if not self.api_key:
            raise RuntimeError("AI_API_KEY is not configured")
        payload: dict[str, Any] = {"model": model or self.text_model, "messages": messages,
                                   "temperature": 0}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]

    # ---------------------------------------------------------------- OCR
    def extract_nameplate(
        self, *, image_bytes: bytes | None, filename: str, context: dict
    ) -> ExtractionResult:
        if not image_bytes:
            return self._fallback.extract_nameplate(image_bytes=image_bytes, filename=filename, context=context)
        mime = mimetypes.guess_type(filename or "")[0] or "image/jpeg"
        if not mime.startswith("image/"):
            return self._fallback.extract_nameplate(image_bytes=image_bytes, filename=filename, context=context)
        data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        try:
            raw = self._chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": NAMEPLATE_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                model=self.vision_model,
                json_mode=True,
            )
            payload = json.loads(raw)
        except Exception as exc:
            degraded = self._fallback.extract_nameplate(
                image_bytes=image_bytes, filename=filename, context=context
            )
            degraded.degraded = True
            degraded.message = f"Vision service unavailable ({exc}). {degraded.message}"
            return degraded

        raw_fields = payload.get("fields") or {}
        overall = payload.get("confidence")
        fields: list[ExtractionField] = []
        for key in FIELD_ORDER:
            value = raw_fields.get(key)
            if value in (None, "", "null"):
                continue
            confidence = float(overall) if isinstance(overall, (int, float)) else None
            fields.append(
                ExtractionField(
                    key=key,
                    label=FIELD_LABELS[key],
                    value=str(value),
                    confidence=confidence,
                    low_confidence=confidence is not None and confidence < settings.AI_MIN_CONFIDENCE,
                    source="vision-model",
                )
            )
        return ExtractionResult(
            available=bool(fields),
            provider=self.name,
            model=self.vision_model,
            fields=fields,
            confidence=float(overall) if isinstance(overall, (int, float)) else None,
            message=(
                "Extracted values are suggestions. Confirm or edit each field before saving."
                if fields
                else "The model could not read any field with confidence. Enter values manually."
            ),
        )

    # ------------------------------------------------------------ anomaly
    def detect_anomaly(self, *, test_code: str, rows: list[dict], instrument: dict) -> AnomalyReport:
        """Anomaly detection stays local and statistical, even with a model configured."""
        report = self._fallback.detect_anomaly(test_code=test_code, rows=rows, instrument=instrument)
        report.provider = f"{self.name}:local-statistics"
        return report

    # ------------------------------------------------------- consistency
    def check_report_consistency(self, *, context: dict) -> ConsistencyReport:
        """Consistency checks stay local: they compare authoritative records.

        The context carries applicant and manufacturer data, so it is never
        shipped to a third party.
        """
        report = self._fallback.check_report_consistency(context=context)
        report.provider = f"{self.name}:local-consistency"
        return report

    # ------------------------------------------------------ classification
    def classify_document(
        self, *, filename: str, content_type: str | None, caption: str | None
    ) -> ClassificationResult:
        baseline = self._fallback.classify_document(
            filename=filename, content_type=content_type, caption=caption
        )
        try:
            from app.models import AttachmentCategory

            raw = self._chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Classify this laboratory evidence file into exactly one category from: "
                            f"{', '.join(AttachmentCategory.ALL)}. "
                            f"Filename: {filename}. Declared type: {content_type}. Caption: {caption or 'none'}. "
                            'Return STRICT JSON: {"category": "...", "confidence": 0.0, "reason": "..."}'
                        ),
                    },
                ],
                json_mode=True,
            )
            payload = json.loads(raw)
            category = str(payload.get("category") or baseline.category)
            if category not in set(AttachmentCategory.ALL):
                raise ValueError(f"model returned an unsupported category: {category}")
            return ClassificationResult(
                available=True,
                provider=self.name,
                category=category,
                confidence=float(payload.get("confidence") or baseline.confidence or 0.5),
                alternatives=baseline.alternatives,
                message=str(payload.get("reason") or baseline.message),
            )
        except Exception as exc:
            baseline.degraded = True
            baseline.message = f"Model classification unavailable ({exc}); used heuristics. {baseline.message}"
            return baseline

    # --------------------------------------------------------- assistant
    def answer_r76(self, *, question: str, sources: list[dict]) -> AssistantAnswer:
        retrieval = self._fallback.answer_r76(question=question, sources=sources)
        if not retrieval.citations:
            return retrieval
        excerpts = "\n\n".join(
            f"[{index + 1}] {item['title']} ({item['code']}) clause {item.get('clause_reference') or 'n/a'}\n{item['excerpt']}"
            for index, item in enumerate(retrieval.citations)
        )
        try:
            answer = self._chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\nApproved configuration excerpts:\n{excerpts}\n\n"
                            "Answer in at most 150 words and cite the bracket numbers you used. "
                            "If the excerpts do not answer the question, say so."
                        ),
                    },
                ]
            )
            return AssistantAnswer(
                available=True,
                provider=self.name,
                answer=answer.strip(),
                citations=retrieval.citations,
                message="Model summary of retrieved, approved configuration excerpts.",
            )
        except Exception as exc:
            retrieval.degraded = True
            retrieval.message = (
                f"Model unavailable ({exc}); returned the retrieval-grounded result instead."
            )
            return retrieval
