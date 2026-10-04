"""DOCX rendering of the report snapshot (PRD 17.1: editable internal workflow)."""

from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from app.services.report_engine.sections import heading_text, section_number, section_required
from app.services.report_engine.verification import qr_png

ACCENT = RGBColor(0x0F, 0x4C, 0x81)
MUTED = RGBColor(0x5A, 0x64, 0x72)


def _text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items())
    return str(value)


def _heading(document: Document, text: str, level: int = 1) -> None:
    heading = document.add_heading(text, level=level)
    for run in heading.runs:
        run.font.color.rgb = ACCENT
        run.font.size = Pt(15 if level == 1 else 12)


def _key_values(document: Document, rows: list[tuple[str, Any]]) -> None:
    table = document.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    for key, value in rows:
        cells = table.add_row().cells
        cells[0].text = str(key)
        cells[1].text = _text(value)
    document.add_paragraph()


def _table(document: Document, headers: list[str], rows: list[list[Any]]) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = str(header)
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            if index < len(cells):
                cells[index].text = _text(value)
    document.add_paragraph()


def _test_records(document: Document, tests: list[dict], number: str) -> None:
    """The figure blocks for one section of the report."""
    for index, test in enumerate(tests, start=1):
        _heading(document, f"{number}.{index} {test['name']} ({test['test_code']})", level=2)
        result = test["result"]
        _key_values(
            document,
            [
                ("OIML clause", test.get("clause_reference")),
                ("Applicability", f"{test['applicability']['status']} - {test['applicability'].get('reason') or ''}"),
                ("Status", test.get("status")),
                ("Result", result.get("status")),
                ("Measured value", f"{_text(result.get('measured_value'))} {result.get('unit') or ''}".strip()),
                ("Limit", f"{_text(result.get('limit_value'))} {result.get('unit') or ''}".strip()),
                ("Margin", result.get("margin")),
                ("Rule", f"{result.get('rule_id') or '-'} ({result.get('rule_version') or '-'})"),
                ("Explanation", result.get("explanation")),
                ("Remarks", test.get("remarks")),
            ],
        )
        if test["layout"] == "checklist":
            _table(
                document,
                ["Item", "Conforms", "Remarks"],
                [
                    [
                        (row.get("detail") or {}).get("item_code") or row.get("label"),
                        "Yes" if (row.get("detail") or {}).get("conforms") else "No",
                        (row.get("detail") or {}).get("remarks"),
                    ]
                    for row in test["rows"]
                ],
            )
        else:
            _table(
                document,
                ["#", "Load", "Indication", "dL", "Error", "MPE", "Margin", "Within"],
                [
                    [row.get("observation_no"), row.get("load"), row.get("indication"),
                     row.get("additional_load"), row.get("error"), row.get("mpe"),
                     row.get("margin"), row.get("within")]
                    for row in test["rows"]
                ],
            )


def render_docx(snapshot: dict[str, Any]) -> bytes:
    """Render the snapshot into an editable DOCX document."""
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)

    cover = snapshot["cover"]
    meta = snapshot["meta"]
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(cover.get("laboratory_name") or "Laboratory")
    run.font.size = Pt(12)
    run.font.color.rgb = MUTED
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(cover.get("report_title") or "Type Evaluation Test Report")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = ACCENT
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(cover.get("subtitle") or "")
    run.font.size = Pt(11)
    run.font.color.rgb = MUTED

    _key_values(
        document,
        [
            (key, value)
            for key, value in [
                ("Laboratory code", cover.get("laboratory_code")),
                ("Location", cover.get("laboratory_location")),
                ("Address", cover.get("laboratory_address")),
                ("Contact email", cover.get("laboratory_contact_email")),
                ("Accreditation number", cover.get("laboratory_accreditation_no")),
            ]
            if value
        ],
    )

    _key_values(
        document,
        [
            ("Application number", meta.get("application_no")),
            ("Report number", meta.get("report_no")),
            ("Report revision", meta.get("revision_no")),
            ("Case revision", meta.get("case_revision_no")),
            ("Generated at (UTC)", meta.get("generated_at")),
            ("Ruleset version", snapshot["versions"].get("ruleset_label")),
            ("Report template version", snapshot["versions"].get("template_label")),
            ("Report mode", snapshot["versions"].get("template_mode_label")),
            ("Overall result", snapshot["summary"].get("overall")),
            ("Verification code", meta.get("verification_code")),
        ],
    )

    _heading(document, heading_text(snapshot, "applicant", "Applicant and manufacturer"))
    _key_values(document, list(snapshot["applicant"].items()))
    _key_values(document, list(snapshot["manufacturer"].items()))

    _heading(document, heading_text(snapshot, "instrument", "Instrument identification and metrological characteristics"))
    instrument = snapshot["instrument"]
    _key_values(
        document,
        [(key.replace("_", " ").title(), value) for key, value in instrument.items() if key != "ranges"],
    )
    if instrument.get("ranges"):
        _heading(document, "Ranges", level=2)
        _table(
            document,
            ["Range", "Min", "Max", "e", "d", "Unit"],
            [
                [item.get("range_no"), item.get("min_capacity"), item.get("max_capacity"),
                 item.get("e"), item.get("d"), item.get("unit")]
                for item in instrument["ranges"]
            ],
        )

    _heading(document, heading_text(snapshot, "conditions", "Laboratory and test conditions"))
    if snapshot["conditions"]:
        for index, condition in enumerate(snapshot["conditions"], start=1):
            _heading(document, f"Condition set {index}", level=2)
            _key_values(document, list(condition.items()))
    else:
        document.add_paragraph("No environmental conditions recorded.")

    _heading(document, heading_text(snapshot, "equipment", "Test equipment and traceability"))
    if snapshot["equipment"]:
        _table(
            document,
            ["Code", "Name", "Type", "Serial", "Class", "Role"],
            [[row.get("code"), row.get("name"), row.get("type"), row.get("serial_no"),
              row.get("accuracy_class"), row.get("role")] for row in snapshot["equipment"]],
        )
    else:
        document.add_paragraph("No test equipment referenced.")

    _heading(document, heading_text(snapshot, "summary", "Summary of applicable tests and results"))
    summary = snapshot["summary"]
    _key_values(
        document,
        [
            ("Tests in plan", summary.get("total")),
            ("Applicable", summary.get("applicable")),
            ("Completed", summary.get("completed")),
            ("Pending", summary.get("pending")),
            ("Overall", summary.get("overall")),
            ("Result breakdown", summary.get("by_status")),
        ]
        + (
            [("Superseded re-tests", summary.get("superseded"))]
            if summary.get("superseded")
            else []
        ),
    )
    _table(
        document,
        ["Test", "Code", "Clause", "Applicability", "Result", "Measured", "Limit", "Margin"],
        [
            [
                test["name"], test["test_code"], test.get("clause_reference"),
                test["applicability"]["status"], test["result"].get("status"),
                test["result"].get("measured_value"), test["result"].get("limit_value"),
                test["result"].get("margin"),
            ]
            for test in snapshot["tests"]
        ],
    )

    metrological = [item for item in snapshot["tests"] if item.get("layout") != "checklist"]
    checklists = [item for item in snapshot["tests"] if item.get("layout") == "checklist"]
    _heading(document, heading_text(snapshot, "tests", "Individual test records"))
    _test_records(document, metrological, section_number(snapshot, "tests", "6"))
    if checklists:
        _heading(document, heading_text(snapshot, "checklist", "Construction and identification checklist"))
        _test_records(document, checklists, section_number(snapshot, "checklist", "7"))

    if snapshot["evidence"] or section_required(snapshot, "evidence"):
        _heading(document, heading_text(snapshot, "evidence", "Evidence and attachments"))
    if snapshot["evidence"]:
        _table(
            document,
            ["File", "Category", "Caption", "Size (bytes)", "SHA-256"],
            [[item.get("filename"), item.get("category"), item.get("caption"),
              item.get("size_bytes"), item.get("sha256")] for item in snapshot["evidence"]],
        )
    elif section_required(snapshot, "evidence"):
        document.add_paragraph("No evidence attached.")

    _heading(document, heading_text(snapshot, "review", "Review, approval and signatures"))
    _key_values(document, list(snapshot["review"].items()))
    _heading(document, heading_text(snapshot, "versions", "Ruleset, template and revision history"))
    _key_values(
        document,
        [
            ("Ruleset version", snapshot["versions"].get("ruleset_label")),
            ("Ruleset status", snapshot["versions"].get("ruleset_status")),
            ("Standard", f"{_text(snapshot['versions'].get('standard'))} {_text(snapshot['versions'].get('standard_edition'))}"),
            ("Source reference", snapshot["versions"].get("source_reference")),
            ("Template version", snapshot["versions"].get("template_label")),
        ],
    )
    _heading(document, heading_text(snapshot, "verification", "Document verification"))
    _key_values(
        document,
        [
            ("Verification code", snapshot["meta"].get("verification_code")),
            ("Content hash (SHA-256)", snapshot["meta"].get("content_hash")),
            ("Verification page", snapshot["meta"].get("verification_url")),
        ],
    )
    verification_url_value = snapshot["meta"].get("verification_url")
    if verification_url_value:
        document.add_picture(io.BytesIO(qr_png(verification_url_value, scale=4)), width=Inches(1.1))
    paragraph = document.add_paragraph(
        "Checking the code, or scanning the mark, recomputes this document's content hash from "
        "the recorded result and reports whether the two still agree."
    )
    for run in paragraph.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = MUTED
    paragraph = document.add_paragraph(snapshot.get("disclaimer") or "")
    for run in paragraph.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = MUTED

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
