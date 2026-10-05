"""Case discussion and System Administrator support messages (FR-05, PRD 18.3).

Two threads exist:

* ``case_messages`` - the discussion attached to one evaluation case, between
  the laboratory that owns the case and the people assigned to it;
* ``support_messages`` - a per-user conversation with the platform
  administrator, so every role has a direct way to reach the System Administrator.

Both are append-only: a message is never edited or deleted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CaseMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One message in an evaluation case's discussion thread."""

    __tablename__ = "case_messages"

    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender_role: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    case = relationship("EvaluationCase")
    sender = relationship("User")


class SupportMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One message in a user's support thread with the platform administrator.

    ``thread_user_id`` identifies the thread: the user sees only their own
    conversation, while the System Administrator sees every thread.
    """

    __tablename__ = "support_messages"

    thread_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender_role: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    thread_user = relationship("User", foreign_keys=[thread_user_id])
    sender = relationship("User", foreign_keys=[sender_id])
