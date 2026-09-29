"""Clause -> rule -> calculation -> report mapping, end to end (audit item 9).

A correct calculation can still end up in the wrong place in the report: the
wrong clause text, a value in the wrong column, a unit that silently changes, a
summary row that counts something else. These tests compare a fully recorded
multi-range case against committed golden fixtures, and check every displayed
value back to the structured record it came from.

    cd backend
    $env:METRIQ_UPDATE_GOLDEN = "1"
    python -m pytest tests/test_report_mapping.py -q     # refresh the fixtures
    Remove-Item Env:METRIQ_UPDATE_GOLDEN
"""

from __future__ import annotations

import io
import json
import os
import uuid as uuid_module
from decimal import Decimal
from pathlib import Path

import pytest

from app.database import SessionLocal
from app.models import EvaluationCase
from app.rules.loader import load_test_catalogue
from app.services.calculation_engine.types import CalcContext
from app.services.report_engine.docx import render_docx
from app.services.report_engine.pdf import render_pdf
from app.services.report_engine.snapshot import build_report_snapshot

from tests.report_fixture import GOLDEN_TITLE, build_golden_case, scrub, scrub_text

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "report_golden"
SNAPSHOT_GOLDEN = FIXTURE_DIR / "expected_snapshot.json"
DOCX_GOLDEN = FIXTURE_DIR / "expected_docx.txt"
PDF_GOLDEN = FIXTURE_DIR / "expected_pdf.txt"

REFRESH = bool(os.environ.get("METRIQ_UPDATE_GOLDEN"))
REPORT_NO = "RPT-GOLDEN-00001"


@pytest.fixture(scope="module")
def golden_case(client, tokens, observations):
    """A fully recorded multi-range case, built once for this module."""
    db = SessionLocal()
    try:
        case_id = build_golden_case(client, tokens, db, observations)
        case = db.get(EvaluationCase, uuid_module.UUID(case_id))
        snapshot, digest = build_report_snapshot(
            db, case=case, revision_no=1, report_no=REPORT_NO
        )
        db.commit()
        return case_id, snapshot, digest
    finally:
        db.close()


@pytest.fixture(scope="module")
def rendered(golden_case) -> dict[str, str]:
    _case_id, snapshot, _digest = golden_case
    docx_bytes = render_docx(snapshot)
    pdf_bytes = render_pdf(snapshot)
    return {
        "docx": scrub_text(docx_text(docx_bytes)),
        "pdf": scrub_text(pdf_text(pdf_bytes)),
    }


def docx_text(payload: bytes) -> str:
    """Paragraph and table text of a generated DOCX, in document order."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(payload))
    lines: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, document).text
            if text.strip():
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            for row in Table(child, document).rows:
                cells = [cell.text for cell in row.cells]
                if any(cell.strip() for cell in cells):
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def pdf_text(payload: bytes) -> str:
    """Extracted text of a generated PDF."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write(body)


def test_the_snapshot_matches_the_golden_fixture(golden_case):
    _case_id, snapshot, _digest = golden_case
    actual = json.dumps(scrub(_stable(snapshot)), indent=2, sort_keys=False, ensure_ascii=False) + "\n"

    if REFRESH:
        _write(SNAPSHOT_GOLDEN, actual)
    expected = SNAPSHOT_GOLDEN.read_text(encoding="utf-8")

    assert actual == expected, (
        "the report snapshot changed; run with METRIQ_UPDATE_GOLDEN=1 to refresh the"
        " fixture once the change is understood"
    )


def test_the_docx_matches_the_golden_fixture(rendered):
    if REFRESH:
        _write(DOCX_GOLDEN, rendered["docx"] + "\n")

    assert rendered["docx"] + "\n" == DOCX_GOLDEN.read_text(encoding="utf-8")


def test_the_pdf_matches_the_golden_fixture(rendered):
    if REFRESH:
        _write(PDF_GOLDEN, rendered["pdf"] + "\n")

    assert rendered["pdf"] + "\n" == PDF_GOLDEN.read_text(encoding="utf-8")


def test_the_snapshot_is_deterministic(golden_case):
    """Two snapshots of the same case differ only in volatile metadata."""
    _case_id, snapshot, digest = golden_case
    db = SessionLocal()
    try:
        case = db.get(EvaluationCase, uuid_module.UUID(_case_id))
        again, digest_again = build_report_snapshot(
            db, case=case, revision_no=1, report_no=REPORT_NO
        )
    finally:
        db.close()

    assert digest == digest_again, "the content hash must not depend on when it was computed"
    assert scrub(_stable(snapshot)) == scrub(_stable(again))


def _stable(snapshot: dict) -> dict:
    """Drop the volatile metadata so two runs compare equal."""
    payload = json.loads(json.dumps(snapshot, default=str))
    meta = payload.get("meta", {})
    for key in ("application_no", "report_no", "generated_at", "generated_by"):
        if key in meta:
            meta[key] = "<removed>"
    return payload


def test_every_test_row_names_its_clause_and_report_section(golden_case):
    """The clause and section a result claims must be the catalogue's."""
    _case_id, snapshot, _digest = golden_case
    catalogue = {entry["test_code"]: entry for entry in load_test_catalogue()["tests"]}
    numbers = [entry["report_section_mapping"]["number"] for entry in catalogue.values()]

    assert snapshot["tests"], "the golden case must contain tests"
    assert len(numbers) == len(set(numbers)), "two tests share a report section number"
    for test in snapshot["tests"]:
        entry = catalogue[test["test_code"]]
        mapping = entry["report_section_mapping"]
        assert test["clause_reference"] == entry["clause_reference"]
        assert mapping["number"] and mapping["title"]
        assert mapping["section"] in {"tests", "checklist"}, (
            f"{test['test_code']} maps to an unexpected report section {mapping['section']!r}"
        )


def test_every_displayed_result_carries_its_own_audit_trail(golden_case):
    """Status, unit, rule version and explanation: no bare numbers in the report."""
    _case_id, snapshot, _digest = golden_case
    catalogue = {entry["test_code"]: entry for entry in load_test_catalogue()["tests"]}

    for test in snapshot["tests"]:
        result = test["result"]
        if test["applicability"]["status"] != "APPLICABLE" or not result["status"]:
            continue
        if result["status"] in {"PENDING", "NOT_APPLICABLE"}:
            continue
        assert result["status"] in {"PASS", "FAIL", "INCOMPLETE", "INVALID", "WAIVED"}, result
        assert result["rule_version"], f"{test['test_code']} reports a result without a rule version"
        assert result["clause_reference"] or test["clause_reference"]
        assert result["explanation"], f"{test['test_code']} reports a result without an explanation"
        # A test that measures a mass must publish the unit it measured in. A
        # checklist reports a count of non-conforming items, which has none.
        measures_a_value = any(
            column.get("unit") for column in catalogue[test["test_code"]]["input_schema"]["columns"]
        )
        if measures_a_value:
            assert result["unit"], f"{test['test_code']} reports a value without a unit"
        if result["status"] in {"PASS", "FAIL"}:
            assert result["measured_value"] is not None
            assert result["limit_value"] is not None
            assert test["rows"], "a decided test must publish its row detail"


def test_multi_range_rows_keep_the_range_they_were_measured_in(golden_case):
    _case_id, snapshot, _digest = golden_case
    weighing = next(test for test in snapshot["tests"] if test["test_code"] == "T-WP")

    ranges = [row["detail"].get("range_no") for row in weighing["rows"]]
    assert ranges == [1, 1, 2], "the range attribution must survive into the report"
    mpes = [Decimal(row["mpe"]) for row in weighing["rows"]]
    # 1.0 e with e = 10 g for range 1, 1.0 e with e = 20 g for range 2.
    assert mpes[1] == Decimal("10")
    assert mpes[2] == Decimal("20"), "range 2 must be judged with its own interval"


def test_the_failing_row_drives_the_reported_status(golden_case):
    _case_id, snapshot, _digest = golden_case
    weighing = next(test for test in snapshot["tests"] if test["test_code"] == "T-WP")

    assert weighing["result"]["status"] == "FAIL"
    failing = [row for row in weighing["rows"] if row.get("within") is False]
    assert len(failing) == 1
    assert Decimal(failing[0]["corrected_error"]) > Decimal(failing[0]["mpe"])
    assert Decimal(weighing["result"]["measured_value"]) == abs(
        Decimal(failing[0]["corrected_error"])
    )


def test_the_summary_counts_the_tests_it_publishes(golden_case):
    _case_id, snapshot, _digest = golden_case
    summary = snapshot["summary"]
    statuses = [test["result"]["status"] for test in snapshot["tests"]]

    assert summary["total"] == len(snapshot["tests"])
    assert summary["by_status"].get("FAIL") == statuses.count("FAIL")
    assert summary["overall"] == "FAIL"
    assert summary["applicable"] == sum(
        1 for test in snapshot["tests"] if test["applicability"]["status"] == "APPLICABLE"
    )


def test_every_reported_value_comes_from_the_stored_structured_records(golden_case):
    """Nothing in the report is typed by the template layer."""
    _case_id, snapshot, _digest = golden_case

    for test in snapshot["tests"]:
        for row in test["rows"]:
            # Row keys are exactly the engine's RowResult fields plus the nested
            # detail block; there is no free-text result path into the report.
            assert "observation_no" in row
            extra = set(row) - _ROW_KEYS - {"detail"}
            assert not extra, f"{test['test_code']} publishes unexpected row keys: {sorted(extra)}"


_ROW_KEYS = {
    "observation_no",
    "label",
    "load",
    "indication",
    "additional_load",
    "value",
    "error",
    "reference_error",
    "corrected_error",
    "mpe",
    "margin",
    "within",
}


def test_the_rendered_documents_identify_the_report_and_every_test(golden_case, rendered):
    """Both renderers are driven by the same snapshot, so they carry the same
    report identity and list the same tests."""
    _case_id, snapshot, _digest = golden_case

    for key, text in rendered.items():
        assert REPORT_NO in text, f"{key} does not identify the report"
        assert "<verification-code>" in text, f"{key} does not publish the verification code"
        assert snapshot["versions"]["ruleset_label"] in text, f"{key} omits the ruleset version"
        for test in snapshot["tests"]:
            assert test["test_code"] in text, f"{key} omits {test['test_code']}"


def test_the_docx_and_the_pdf_agree_on_the_result(golden_case, rendered):
    _case_id, snapshot, _digest = golden_case

    for test in snapshot["tests"]:
        result = test["result"]
        if result.get("status") not in {"PASS", "FAIL"}:
            continue
        for key, text in rendered.items():
            assert result["status"] in text, f"{key} does not report the {result['status']} status"


def test_a_missing_limit_is_reported_not_guessed():
    """The engine refuses to invent a compliance decision."""
    from app.services.calculation_engine.engine import evaluate
    from app.services.compliance_engine import evaluate as evaluate_compliance

    ctx = CalcContext(
        test_code="T-TILT",
        instrument={"e": Decimal("10"), "unit": "g", "instrument_class": "III"},
        observations=[],
        ruleset={"tolerances": {}},
        test_definition={"calculation_rules": {}, "compliance_rules": {}},
    )
    outcome = evaluate(ctx)
    decision = evaluate_compliance(outcome, compliance_rules={}, applicability_status="APPLICABLE")

    assert outcome.status == "INVALID"
    assert decision.status == "INVALID"
    assert "no deterministic calculator" in " ".join(outcome.errors)
