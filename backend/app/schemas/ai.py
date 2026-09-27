"""AI feature request schemas (PRD 12)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AINameplateExtractRequest(BaseModel):
    attachment_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    test_instance_id: uuid.UUID | None = None
    text_hint: str | None = Field(
        default=None, description="Optional machine-readable text when no file is supplied"
    )


class AIAnomalyCheckRequest(BaseModel):
    case_id: uuid.UUID
    test_instance_id: uuid.UUID


class AIClassifyRequest(BaseModel):
    attachment_id: uuid.UUID


class AIKnowledgeRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    standard_version_id: uuid.UUID | None = None


class AIDispositionRequest(BaseModel):
    event_id: uuid.UUID
    disposition: str = Field(pattern="^(accepted|edited|rejected|dismissed|confirmed)$")
    note: str | None = None
