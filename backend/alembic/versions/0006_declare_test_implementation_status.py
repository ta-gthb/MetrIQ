"""declare whether a catalogue test is executable

Audit item 7: every prescribed OIML R 76-1 test has a catalogue entry, and each
entry now states its implementation status. Unsupported tests are marked rather
than hidden, so the test plan and the coverage matrix can show them.

Revision ID: 0006
Revises: 4e0ca7bb29b3
Created: 2026-09-29 21:10:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
# Rendered rather than imported by Alembic: JSONB's astext_type comes out as a
# bare `Text()`, which would be a NameError without this line.
from sqlalchemy import Text  # noqa: F401


revision = '0006'
down_revision = '4e0ca7bb29b3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('test_definitions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'implementation_status',
                sa.String(length=24),
                nullable=False,
                server_default='implemented',
            )
        )
        batch_op.add_column(sa.Column('unsupported_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('test_definitions', schema=None) as batch_op:
        batch_op.drop_column('unsupported_reason')
        batch_op.drop_column('implementation_status')
