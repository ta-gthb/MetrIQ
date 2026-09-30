"""Deterministic, offline AI provider.

This provider performs genuinely useful work without an external model:

* anomaly detection uses robust statistics (median / MAD / decimal-shift tests);
* document classification uses filename, MIME type and caption heuristics;
* the R 76 assistant performs retrieval over the approved rule and test
  catalogue and cites what it retrieved;
* nameplate extraction is only attempted on machine-readable text payloads and
  otherwise reports itself unavailable rather than inventing values.

Nothing here can influence a compliance outcome.
"""

from __future__ import annotations

import re
from decimal import Decimal
from statistics import median

from app.models import AttachmentCategory
from app.services.ai_service.base import (
    AnomalyFinding,
    AnomalyReport,
    AssistantAnswer,
    ClassificationResult,
    ConsistencyReport,
    ExtractionField,
    ExtractionResult,
)
from app.services.ai_service.consistency import run_checks
from app.utils.decimals import decimal_str, to_decimal

MAD_THRESHOLD = 3.5          # modified z-score above which a point is an outlier
DECIMAL_SHIFT_TOLERANCE = Decimal("0.01")
FIELD_ORDER = ["manufacturer", "model", "type_designation", "serial_number", "class", "max", "min", "e", "d"]
FIELD_LABELS = {
    "manufacturer": "Manufacturer",
    "model": "Model",
    "type_designation": "Type designation",
    "serial_number": "Serial number",
    "class": "Accuracy class",
    "max": "Maximum capacity (Max)",
    "min": "Minimum capacity (Min)",
    "e": "Verification scale interval (e)",
    "d": "Actual scale interval (d)",
}

CLASSIFICATION_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (AttachmentCategory.CALIBRATION_CERTIFICATE,
     ("calibration", "calib_cert", "certificate", "traceab", "cert_")),
    (AttachmentCategory.NAMEPLATE_PHOTO,
     ("nameplate", "name_plate", "rating", "label", "plate", "idplate")),
    (AttachmentCategory.TEST_SETUP_PHOTO,
     ("setup", "rig", "arrangement", "test_setup", "installation")),
    (AttachmentCategory.TECHNICAL_MANUAL,
     ("manual", "handbook", "instruction", "user_guide", "datasheet", "spec")),
    (AttachmentCategory.DRAWING, ("drawing", "schematic", "dimension", "cad", "blueprint")),
    (AttachmentCategory.CORRESPONDENCE, ("letter", "email", "correspond", "application", "form")),
]


class StubAIProvider:
    name = "stub"

    # ---------------------------------------------------------------- OCR
    def extract_nameplate(
        self, *, image_bytes: bytes | None, filename: str, context: dict
    ) -> ExtractionResult:
        text = self._readable_text(image_bytes, filename)
        if text is None:
            return ExtractionResult(
                available=False,
                provider=self.name,
                degraded=True,
                message=(
                    "No vision provider is configured, so the photograph cannot be read. "
                    "Enter the instrument fields manually; the core workflow is unaffected."
                ),
            )

        found: dict[str, tuple[str, float]] = {}
        patterns = {
            "manufacturer": r"(?:manufacturer|make|brand)\s*[:=]\s*(.+)",
            "model": r"(?:model|type)\s*[:=]\s*(.+)",
            "type_designation": r"(?:type designation|designation)\s*[:=]\s*(.+)",
            "serial_number": r"(?:serial(?:\s*(?:no|number))?|s/?n)\s*[:=]\s*([A-Za-z0-9\-/]+)",
            "class": r"(?:class|accuracy class)\s*[:=]\s*([IVX]+|[0-9]+)",
            "max": r"(?:max|max\.|maximum)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)",
            "min": r"(?:min|min\.|minimum)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)",
            "e": r"\be\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)",
            "d": r"\bd\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if not match:
                continue
            value = match.group(1).strip()
            if len(match.groups()) > 1 and match.group(2):
                value = f"{value} {match.group(2).strip()}".strip()
            found[key] = (value, 0.9)

        if not found:
            return ExtractionResult(
                available=False,
                provider=self.name,
                degraded=True,
                message=(
                    "The uploaded file does not contain machine-readable nameplate text. "
                    "Configure a vision provider for photographic extraction."
                ),
            )

        fields = [
            ExtractionField(key=key, label=FIELD_LABELS[key], value=found[key][0],
                            confidence=found[key][1], low_confidence=False, source="text-parse")
            for key in FIELD_ORDER
            if key in found
        ]
        return ExtractionResult(
            available=True,
            provider=self.name,
            model="deterministic-text-parser",
            fields=fields,
            confidence=round(sum(item.confidence or 0 for item in fields) / len(fields), 3) if fields else None,
            message="Values were parsed from machine-readable text. Confirm before saving.",
        )

    @staticmethod
    def _readable_text(image_bytes: bytes | None, filename: str) -> str | None:
        if not image_bytes:
            return None
        suffix = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
        if suffix not in {"txt", "csv", "json", "tsv", "md"}:
            return None
        try:
            return image_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return None

    # ------------------------------------------------------------ anomaly
    def detect_anomaly(self, *, test_code: str, rows: list[dict], instrument: dict) -> AnomalyReport:
        findings: list[AnomalyFinding] = []
        checked = 0
        units = {str(row.get("unit")).strip().lower() for row in rows if row.get("unit")}
        if len(units) > 1:
            findings.append(
                AnomalyFinding(
                    row=0,
                    field="unit",
                    kind="unit_inconsistency",
                    severity="warning",
                    message=f"Rows use more than one unit ({', '.join(sorted(units))}); confirm the entries.",
                )
            )

        for field in ("indication", "value", "error"):
            series: list[tuple[int, Decimal]] = []
            for index, row in enumerate(rows, start=1):
                raw = row.get(field)
                if raw is None:
                    continue
                try:
                    number = to_decimal(raw, field=field)
                except Exception:
                    findings.append(
                        AnomalyFinding(
                            row=index, field=field, kind="unparseable",
                            severity="warning",
                            message=f"Row {index}: {field} could not be read as a number.",
                            value=str(raw),
                        )
                    )
                    continue
                if number is not None:
                    series.append((index, number))
            if len(series) < 4:
                continue
            checked += len(series)
            findings.extend(self._outlier_findings(series, field))
            findings.extend(self._decimal_shift_findings(series, field))

        return AnomalyReport(
            available=True,
            provider=self.name,
            findings=findings,
            checked_rows=checked,
            message=(
                "Statistical review only. These warnings are advisory, do not alter any "
                "observation and never change the compliance result."
            ),
        )

    @staticmethod
    def _outlier_findings(series: list[tuple[int, Decimal]], field: str) -> list[AnomalyFinding]:
        values = [value for _row, value in series]
        centre = median(values)
        deviations = [abs(value - centre) for value in values]
        mad = median(deviations)
        findings: list[AnomalyFinding] = []
        if mad == 0:
            nonzero = [value for value in values if value != centre]
            if not nonzero:
                return findings
            scale = median([abs(value - centre) for value in nonzero]) or Decimal(1)
        else:
            scale = mad
        for row, value in series:
            score = Decimal("0.6745") * (value - centre) / scale
            if abs(score) >= Decimal(str(MAD_THRESHOLD)):
                findings.append(
                    AnomalyFinding(
                        row=row,
                        field=field,
                        kind="outlier",
                        severity="warning",
                        message=(
                            f"Row {row}: {field} {decimal_str(value)} deviates markedly from the "
                            f"median {decimal_str(centre)}. Potential anomaly - please verify observation."
                        ),
                        value=decimal_str(value),
                        score=float(score),
                    )
                )
        return findings

    @staticmethod
    def _decimal_shift_findings(series: list[tuple[int, Decimal]], field: str) -> list[AnomalyFinding]:
        values = [value for _row, value in series]
        centre = median(values)
        if centre == 0:
            return []
        findings: list[AnomalyFinding] = []
        for row, value in series:
            if value == 0:
                continue
            ratio = abs(value / centre)
            for exponent in (1, 2, 3, -1, -2, -3):
                target = Decimal(10) ** exponent
                if abs(ratio - target) / target <= DECIMAL_SHIFT_TOLERANCE:
                    findings.append(
                        AnomalyFinding(
                            row=row,
                            field=field,
                            kind="decimal_shift",
                            severity="warning",
                            message=(
                                f"Row {row}: {field} {decimal_str(value)} differs from the median by a "
                                f"factor of about {target}. Possible decimal or unit error - please verify."
                            ),
                            value=decimal_str(value),
                            score=float(exponent),
                        )
                    )
                    break
        return findings

    # ------------------------------------------------------- consistency
    def check_report_consistency(self, *, context: dict) -> ConsistencyReport:
        """Advisory structural review of the recorded case versus its report.

        Every check is deterministic and local; the context is never sent to a
        hosted model. Nothing here can change a compliance outcome.
        """
        return run_checks(context, provider=self.name)

    # ------------------------------------------------------ classification
    def classify_document(
        self, *, filename: str, content_type: str | None, caption: str | None
    ) -> ClassificationResult:
        haystack = " ".join(filter(None, [filename or "", caption or ""])).lower()
        scores: list[tuple[str, float]] = []
        for category, keywords in CLASSIFICATION_KEYWORDS:
            hits = sum(1 for keyword in keywords if keyword in haystack)
            if hits:
                scores.append((category, min(0.95, 0.55 + 0.15 * hits)))

        mime = (content_type or "").lower()
        if mime.startswith("image/"):
            scores.append((AttachmentCategory.NAMEPLATE_PHOTO, 0.5))
        elif mime == "application/pdf":
            scores.append((AttachmentCategory.CALIBRATION_CERTIFICATE, 0.45))
        elif "word" in mime or "document" in mime:
            scores.append((AttachmentCategory.CORRESPONDENCE, 0.4))

        if not scores:
            return ClassificationResult(
                available=True,
                provider=self.name,
                category=AttachmentCategory.OTHER,
                confidence=0.3,
                message="No strong signal; classified as 'other'. Please correct if needed.",
            )

        merged: dict[str, float] = {}
        for category, score in scores:
            merged[category] = max(merged.get(category, 0.0), score)
        ranked = sorted(merged.items(), key=lambda item: item[1], reverse=True)
        top_category, top_score = ranked[0]
        return ClassificationResult(
            available=True,
            provider=self.name,
            category=top_category,
            confidence=round(top_score, 3),
            alternatives=[{"category": name, "confidence": round(score, 3)} for name, score in ranked[1:4]],
            message="Suggested category. Always correctable by the user.",
        )

    # --------------------------------------------------------- assistant
    def answer_r76(self, *, question: str, sources: list[dict]) -> AssistantAnswer:
        terms = [term for term in re.split(r"[^a-z0-9]+", (question or "").lower()) if len(term) > 2]
        if not terms:
            return AssistantAnswer(
                available=True,
                provider=self.name,
                grounded=False,
                answer=(
                    "Please ask a more specific question about the configured OIML R 76 rules, "
                    "tests, tolerances or report structure."
                ),
                message="The question did not contain searchable terms.",
            )

        scored: list[tuple[float, dict]] = []
        for source in sources:
            haystack = " ".join(
                str(source.get(key, "")) for key in ("title", "code", "text", "clause_reference", "category")
            ).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                scored.append((float(score) / len(terms), source))
        scored.sort(key=lambda item: item[0], reverse=True)

        if not scored:
            return AssistantAnswer(
                available=True,
                provider=self.name,
                grounded=False,
                answer=(
                    "No configured rule, test definition or template section matches that question. "
                    "The platform does not invent rules: consult the controlled copy of OIML R 76 or "
                    "ask the laboratory's metrology authority."
                ),
                message="No grounding source found.",
            )

        top = scored[:4]
        lines = ["Based on the configured, approved sources:"]
        citations: list[dict] = []
        for score, source in top:
            excerpt = (source.get("text") or "").strip()
            excerpt = (excerpt[:320] + "...") if len(excerpt) > 320 else excerpt
            lines.append(f"- {source.get('title')} ({source.get('code')}): {excerpt}")
            citations.append(
                {
                    "code": source.get("code"),
                    "title": source.get("title"),
                    "clause_reference": source.get("clause_reference"),
                    "category": source.get("category"),
                    "score": round(score, 3),
                    "excerpt": excerpt,
                }
            )
        lines.append(
            "This answer is retrieved from versioned configuration data only. "
            "It is not a metrological decision and does not replace the standard."
        )
        return AssistantAnswer(
            available=True,
            provider=self.name,
            answer="\n".join(lines),
            citations=citations,
            message="Retrieval-grounded answer with citations.",
        )
