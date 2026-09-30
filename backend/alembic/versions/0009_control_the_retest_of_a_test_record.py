"""control the re-test of a test record

Audit item 16 asks for a re-test workflow that does not destroy the record it
replaces. A re-test is therefore a *new* test instance: the superseded row keeps
its observations, its calculation runs and its result, the new row points at it
through ``supersedes_test_instance_id``, and the case summary counts only the
live revision. Nothing is edited in place and nothing is deleted, so the history
of what was measured stays readable next to what replaced it.

Revision ID: 0009
Revises: 0008
Created: 2026-09-30 21:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
# Rendered rather than imported by Alembic: JSONB's astext_type comes out as a
# bare `Text()`, which would be a NameError without this line.
from sqlalchemy import Text  # noqa: F401


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('test_instances', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('revision_no', sa.Integer(), nullable=False, server_default='1')
        )
        batch_op.add_column(sa.Column('supersedes_test_instance_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('superseded_by_test_instance_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('retest_reason', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(
            'ix_test_instances_supersedes_test_instance_id', ['supersedes_test_instance_id']
        )
        batch_op.create_index(
            'ix_test_instances_superseded_by_test_instance_id', ['superseded_by_test_instance_id']
        )
        batch_op.create_index('ix_test_instances_superseded_at', ['superseded_at'])
        batch_op.create_foreign_key(
            'fk_test_instances_supersedes_test_instance_id',
            'test_instances',
            ['supersedes_test_instance_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_foreign_key(
            'fk_test_instances_superseded_by_test_instance_id',
            'test_instances',
            ['superseded_by_test_instance_id'],
            ['id'],
            ondelete='SET NULL',
        )
    # Every test that already exists is the first revision of its own record.
    op.execute("update test_instances set revision_no = 1 where revision_no is null")


def downgrade() -> None:
    with op.batch_alter_table('test_instances', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_test_instances_superseded_by_test_instance_id', type_='foreignkey'
        )
        batch_op.drop_constraint('fk_test_instances_supersedes_test_instance_id', type_='foreignkey')
        batch_op.drop_index('ix_test_instances_superseded_at')
        batch_op.drop_index('ix_test_instances_superseded_by_test_instance_id')
        batch_op.drop_index('ix_test_instances_supersedes_test_instance_id')
        batch_op.drop_column('superseded_at')
        batch_op.drop_column('retest_reason')
        batch_op.drop_column('superseded_by_test_instance_id')
        batch_op.drop_column('supersedes_test_instance_id')
        batch_op.drop_column('revision_no')