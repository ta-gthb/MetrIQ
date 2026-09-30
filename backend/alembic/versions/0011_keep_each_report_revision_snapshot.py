"""keep the snapshot each report revision printed

Audit item 13 asks the repository to answer questions about history: what an
earlier revision of a report said, and what changed between two revisions.
The generated report row holds only the snapshot of its latest revision, so
older snapshots are not recoverable. Each ``report_revisions`` row now carries
the snapshot it printed, which is also what lets a historical report keep the
ruleset and template it was generated under (audit item 15).

Revision ID: 0011
Revises: 0010
Created: 2026-09-30 23:10:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
# Rendered rather than imported by Alembic: JSONB's astext_type comes out as a
# bare `Text()`, which would be a NameError without this line.
from sqlalchemy import Text  # noqa: F401


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('report_revisions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'data_snapshot',
                sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('report_revisions', schema=None) as batch_op:
        batch_op.drop_column('data_snapshot')
