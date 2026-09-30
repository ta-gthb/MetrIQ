"""Evidence schemas: how a document is tied to the record it supports (FR-09)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AttachmentLinkIn(BaseModel):
    """Link one attachment to the test it supports (audit item 12)."""

    test_instance_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)
