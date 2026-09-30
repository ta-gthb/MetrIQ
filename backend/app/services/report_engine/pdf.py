"""PDF rendering of the report snapshot (PRD 17.1)."""

from __future__ import annotations

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.report_engine.sections import heading_text, section_number, section_required
from app.services.report_engine.verification import qr_drawing

ACCENT = colors.HexColor("#0F4C81")
MUTED = colors.HexColor("#5A6472")
LIGHT = colors.HexColor("#EEF2F7")
STATUS_COLORS = {
    "PASS": colors.HexColor("#0F7B4F"),
    "FAIL": colors.HexColor("#B3261E"),
    "INCOMPLETE": colors.HexColor("#8A5A00"),
    "INVALID": colors.HexColor("#8A5A00"),
    "NOT_APPLICABLE": MUTED,
    "WAIVED": colors.HexColor("#4A4A8A"),
    "PENDING": MUTED,
}


def _test_records(
    snapshot: dict[str, Any], tests: list[dict[str, Any]], styles, width: float, number: str
) -> list[Any]:
    """The record blocks for one section of the report: tables of observations."""
    story: list[Any] = []
    for index, test in enumerate(tests, start=1):
        result = test["result"]
        block: list[Any] = [
            Paragraph(f"{number}.{index} {test['name']} ({test['test_code']})", styles["h2"]),
            _kv_table(
                [
                    ("OIML clause", test.get("clause_reference")),
                    ("Applicability", f"{test['applicability']['status']} - {test['applicability'].get('reason') or ''}"),
                    ("Status", test.get("status")),
                    ("Result", result.get("status")),
                    ("Measured value", f"{_text(result.get('measured_value'))} {result.get('unit') or ''}".strip()),
                    ("Limit", f"{_text(result.get('limit_value'))} {result.get('unit') or ''}".strip()),
                    ("Margin", _text(result.get("margin"))),
                    ("Rule", f"{result.get('rule_id') or '-'} ({result.get('rule_version') or '-'})"),
                    ("Rounding policy", test["calculation"].get("rounding_policy")),
                    ("Explanation", result.get("explanation")),
                    ("Remarks", test.get("remarks")),
                ],
                styles,
                width,
            ),
            Spacer(1, 3 * mm),
        ]
        story.append(KeepTogether(block))
        if test["layout"] == "checklist":
            data = [["Item", "Conforms", "Remarks"]]
            for row in test["rows"]:
                detail = row.get("detail") or {}
                data.append(
                    [
                        Paragraph(_text(detail.get("item_code") or row.get("label")), styles["cell"]),
                        Paragraph("Yes" if detail.get("conforms") else "No", styles["cell"]),
                        Paragraph(_text(detail.get("remarks")), styles["cell"]),
                    ]
                )
        else:
            data = [["#", "Load", "Indication", "dL", "Error", "MPE", "Margin", "Within"]]
            for row in test["rows"]:
                data.append(
                    [
                        Paragraph(_text(row.get("observation_no")), styles["cell"]),
                        Paragraph(_text(row.get("load")), styles["cell"]),
                        Paragraph(_text(row.get("indication")), styles["cell"]),
                        Paragraph(_text(row.get("additional_load")), styles["cell"]),
                        Paragraph(_text(row.get("error")), styles["cell"]),
                        Paragraph(_text(row.get("mpe")), styles["cell"]),
                        Paragraph(_text(row.get("margin")), styles["cell"]),
                        Paragraph(_text(row.get("within")), styles["cell"]),
                    ]
                )
        table = Table(data, colWidths=[width / len(data[0])] * len(data[0]), hAlign="LEFT", repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B2A41")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 5 * mm))
    return story


def _verification_block(snapshot: dict[str, Any], styles, width: float) -> list[Any]:
    """The document-verification block: the mark, the code and what it proves."""
    meta = snapshot.get("meta") or {}
    rows = [
        ("Verification code", meta.get("verification_code")),
        ("Content hash (SHA-256)", meta.get("content_hash")),
    ]
    url = meta.get("verification_url")
    if url:
        rows.append(("Verification page", url))
    note = Paragraph(
        "Checking the code, or scanning the mark, recomputes this document's content hash from "
        "the recorded result and reports whether the two still agree.",
        styles["small"],
    )
    if not url:
        return [_kv_table(rows, styles, width), Spacer(1, 2 * mm), note]
    mark = qr_drawing(url, size=30 * mm)
    table = _kv_table(rows, styles, width * 0.64)
    grid = Table([[mark, table]], colWidths=[width * 0.34, width * 0.66], hAlign="LEFT")
    grid.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
            ]
        )
    )
    return [grid, Spacer(1, 2 * mm), note]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=20, leading=24,
                                textColor=ACCENT, alignment=TA_CENTER, spaceAfter=6),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=11, leading=15,
                                   textColor=MUTED, alignment=TA_CENTER, spaceAfter=14),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=13, leading=17,
                             textColor=ACCENT, spaceBefore=12, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11.5, leading=15,
                             textColor=colors.HexColor("#1B2A41"), spaceBefore=9, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12.5),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10.5),
        "cellhead": ParagraphStyle("cellhead", parent=base["Normal"], fontSize=8, leading=10.5,
                                   textColor=colors.white, alignment=TA_LEFT),
    }


def _kv_table(rows: list[tuple[str, Any]], styles, width: float) -> Table:
    data = [
        [Paragraph(str(key), styles["cell"]), Paragraph(_text(value), styles["cell"])]
        for key, value in rows
    ]
    table = Table(data, colWidths=[width * 0.34, width * 0.66], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                ("BACKGROUND", (0, 0), (0, -1), LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items())
    return str(value)


def _test_table(test: dict[str, Any], styles, width: float) -> Table:
    columns = test.get("columns") or []
    rows = test.get("rows") or []
    if columns:
        headers = [column.get("label") or column.get("key") for column in columns]
        keys = [column.get("key") for column in columns]
    else:
        headers = ["#", "Load", "Indication", "Error", "MPE", "Margin", "Result"]
        keys = ["observation_no", "load", "indication", "error", "mpe", "margin", "within"]
    data = [[Paragraph(str(header), styles["cellhead"]) for header in headers]]
    for row in rows:
        data.append([Paragraph(_text(row.get(key)), styles["cell"]) for key in keys])
    if len(data) == 1:
        data.append([Paragraph("No observations recorded", styles["cell"])] + [""] * (len(headers) - 1))
    table = Table(data, colWidths=[width / len(headers)] * len(headers), hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
            ]
        )
    )
    return table


def _footer(canvas, doc) -> None:  # pragma: no cover - rendering hook
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 12 * mm, f"{doc.metriq_report_no} - revision {doc.metriq_revision}")
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#D5DEE9"))
    canvas.line(18 * mm, 16 * mm, A4[0] - 18 * mm, 16 * mm)
    canvas.restoreState()


def render_pdf(snapshot: dict[str, Any]) -> bytes:
    """Render the snapshot to a printable, archival PDF."""
    styles = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"Type Evaluation Report {snapshot['meta'].get('application_no')}",
        author=snapshot["cover"].get("laboratory_name") or "MetrIQ",
        subject="OIML R 76 type evaluation test report",
    )
    document.metriq_report_no = snapshot["meta"].get("report_no") or snapshot["meta"].get("application_no")
    document.metriq_revision = snapshot["meta"].get("revision_no")
    width = document.width
    story: list[Any] = []

    cover = snapshot["cover"]
    meta = snapshot["meta"]
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph(cover.get("laboratory_name") or "Laboratory", styles["subtitle"]))
    story.append(Paragraph(cover.get("report_title") or "Type Evaluation Test Report", styles["title"]))
    story.append(Paragraph(cover.get("subtitle") or "", styles["subtitle"]))
    story.append(
        _kv_table(
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
            styles,
            width,
        )
    )
    story.append(PageBreak())

    story.append(Paragraph(heading_text(snapshot, "applicant", "Applicant and manufacturer"), styles["h1"]))
    story.append(_kv_table(list(snapshot["applicant"].items()), styles, width))
    story.append(Spacer(1, 4 * mm))
    story.append(_kv_table(list(snapshot["manufacturer"].items()), styles, width))

    story.append(Paragraph(heading_text(snapshot, "instrument", "Instrument identification and metrological characteristics"), styles["h1"]))
    instrument = snapshot["instrument"]
    story.append(
        _kv_table(
            [(key.replace("_", " ").title(), value) for key, value in instrument.items() if key != "ranges"],
            styles,
            width,
        )
    )
    if instrument.get("ranges"):
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Ranges", styles["h2"]))
        ranges = instrument["ranges"]
        data = [["Range", "Min", "Max", "e", "d", "Unit"]] + [
            [str(item.get("range_no")), _text(item.get("min_capacity")), _text(item.get("max_capacity")),
             _text(item.get("e")), _text(item.get("d")), _text(item.get("unit"))]
            for item in ranges
        ]
        table = Table(data, colWidths=[width / 6] * 6, hAlign="LEFT")
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0"))]))
        story.append(table)

    story.append(Paragraph(heading_text(snapshot, "conditions", "Laboratory and test conditions"), styles["h1"]))
    if snapshot["conditions"]:
        for index, condition in enumerate(snapshot["conditions"], start=1):
            story.append(Paragraph(f"Condition set {index}", styles["h2"]))
            story.append(_kv_table(list(condition.items()), styles, width))
            story.append(Spacer(1, 3 * mm))
    else:
        story.append(Paragraph("No environmental conditions recorded.", styles["body"]))

    story.append(Paragraph(heading_text(snapshot, "equipment", "Test equipment and traceability"), styles["h1"]))
    if snapshot["equipment"]:
        rows = snapshot["equipment"]
        headers = ["Code", "Name", "Type", "Serial", "Class", "Role"]
        keys = ["code", "name", "type", "serial_no", "accuracy_class", "role"]
        data = [headers] + [[_text(row.get(key)) for key in keys] for row in rows]
        table = Table(data, colWidths=[width / len(headers)] * len(headers), hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("No test equipment referenced.", styles["body"]))

    story.append(PageBreak())
    story.append(Paragraph(heading_text(snapshot, "summary", "Summary of applicable tests and results"), styles["h1"]))
    summary = snapshot["summary"]
    story.append(
        _kv_table(
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
            styles,
            width,
        )
    )
    story.append(Spacer(1, 4 * mm))
    data = [["Test", "Clause", "Applicability", "Result", "Measured", "Limit", "Margin"]]
    for test in snapshot["tests"]:
        result = test["result"]
        data.append(
            [
                Paragraph(f"{test['name']}<br/><font size=7 color='#5A6472'>{test['test_code']}</font>", styles["cell"]),
                Paragraph(_text(test.get("clause_reference")), styles["cell"]),
                Paragraph(_text(test["applicability"]["status"]), styles["cell"]),
                Paragraph(_text(result.get("status")), styles["cell"]),
                Paragraph(_text(result.get("measured_value")), styles["cell"]),
                Paragraph(_text(result.get("limit_value")), styles["cell"]),
                Paragraph(_text(result.get("margin")), styles["cell"]),
            ]
        )
    story.append(Paragraph(f"{section_number(snapshot, 'summary', '5')}.1 Test matrix", styles["h2"]))
    table = Table(data, colWidths=[width * 0.24, width * 0.16, width * 0.12, width * 0.1,
                                   width * 0.13, width * 0.13, width * 0.12], hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)

    story.append(PageBreak())
    metrological = [item for item in snapshot["tests"] if item.get("layout") != "checklist"]
    checklists = [item for item in snapshot["tests"] if item.get("layout") == "checklist"]
    story.append(Paragraph(heading_text(snapshot, "tests", "Individual test records"), styles["h1"]))
    story.extend(
        _test_records(snapshot, metrological, styles, width, section_number(snapshot, "tests", "6"))
    )
    if checklists:
        story.append(PageBreak())
        story.append(
            Paragraph(
                heading_text(snapshot, "checklist", "Construction and identification checklist"),
                styles["h1"],
            )
        )
        story.extend(
            _test_records(snapshot, checklists, styles, width, section_number(snapshot, "checklist", "7"))
        )

    if snapshot["evidence"] or section_required(snapshot, "evidence"):
        story.append(
            Paragraph(heading_text(snapshot, "evidence", "Evidence and attachments"), styles["h1"])
        )
    if snapshot["evidence"]:
        data = [["File", "Category", "Caption", "Size (bytes)", "SHA-256"]]
        for item in snapshot["evidence"]:
            data.append([
                Paragraph(_text(item.get("filename")), styles["cell"]),
                Paragraph(_text(item.get("category")), styles["cell"]),
                Paragraph(_text(item.get("caption")), styles["cell"]),
                Paragraph(_text(item.get("size_bytes")), styles["cell"]),
                Paragraph((item.get("sha256") or "")[:32], styles["cell"]),
            ])
        table = Table(data, colWidths=[width * 0.3, width * 0.18, width * 0.24, width * 0.12, width * 0.16],
                      hAlign="LEFT", repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ]
            )
        )
        story.append(table)
    elif section_required(snapshot, "evidence"):
        story.append(Paragraph("No evidence attached.", styles["body"]))

    story.append(Paragraph(heading_text(snapshot, "review", "Review, approval and signatures"), styles["h1"]))
    story.append(_kv_table(list(snapshot["review"].items()), styles, width))
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            heading_text(snapshot, "versions", "Ruleset, template and revision history"), styles["h1"]
        )
    )
    story.append(
        _kv_table(
            [
                ("Ruleset version", snapshot["versions"].get("ruleset_label")),
                ("Ruleset status", snapshot["versions"].get("ruleset_status")),
                ("Standard", f"{_text(snapshot['versions'].get('standard'))} {_text(snapshot['versions'].get('standard_edition'))}"),
                ("Source reference", snapshot["versions"].get("source_reference")),
                ("Template version", snapshot["versions"].get("template_label")),
            ],
            styles,
            width,
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(heading_text(snapshot, "verification", "Document verification"), styles["h1"])
    )
    story.extend(_verification_block(snapshot, styles, width))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(snapshot.get("disclaimer") or "", styles["small"]))

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
