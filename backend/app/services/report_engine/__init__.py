"""Report generation: structured snapshot -> PDF and DOCX (PRD 17)."""

from app.services.report_engine.service import (
    build_report_snapshot,
    generate_report,
    render_document,
)

__all__ = ["build_report_snapshot", "generate_report", "render_document"]
