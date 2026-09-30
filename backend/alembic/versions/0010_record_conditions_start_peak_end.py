"""record environmental conditions at the start, the maximum and the end

Audit item 12 asks for conditions to be captured across the observation period,
not as a single number: the reading when the period opened, the extreme seen
during it, and the reading when it closed. The existing ``temperature_c`` and
``relative_humidity_pct`` remain the opening readings, so nothing already
recorded changes meaning; the four new columns carry the peak and the closing
reading for temperature and for relative humidity.

Revision ID: 0010
Revises: 0009
Created: 2026-09-30 22:30:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
# Rendered rather than imported by Alembic: JSONB's astext_type comes out as a
# bare `Text()`, which would be a NameError without this line.
from sqlalchemy import Text  # noqa: F401


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

COLUMNS = (
    'max_temperature_c',
    'end_temperature_c',
    'max_relative_humidity_pct',
    'end_relative_humidity_pct',
)


def upgrade() -> None:
    with op.batch_alter_table('environmental_conditions', schema=None) as batch_op:
        for name in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Numeric(10, 3), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('environmental_conditions', schema=None) as batch_op:
        for name in reversed(COLUMNS):
            batch_op.drop_column(name)
