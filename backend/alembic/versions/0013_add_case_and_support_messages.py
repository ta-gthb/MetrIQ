"""add case discussion and Super Admin support messages

Every evaluation case carries a discussion between its laboratory and the
people assigned to it, and every account has one support thread with the
platform administrator. Both tables are append-only.

Revision ID: 0013
Revises: 0012
Created: 2026-10-04 10:10:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'case_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('case_id', sa.Uuid(), nullable=False),
        sa.Column('sender_id', sa.Uuid(), nullable=True),
        sa.Column('sender_role', sa.String(length=32), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['evaluation_cases.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_case_messages_case_id', 'case_messages', ['case_id'])
    op.create_index('ix_case_messages_sender_id', 'case_messages', ['sender_id'])

    op.create_table(
        'support_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('thread_user_id', sa.Uuid(), nullable=False),
        sa.Column('sender_id', sa.Uuid(), nullable=True),
        sa.Column('sender_role', sa.String(length=32), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['thread_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_support_messages_thread_user_id', 'support_messages', ['thread_user_id'])
    op.create_index('ix_support_messages_sender_id', 'support_messages', ['sender_id'])


def downgrade() -> None:
    op.drop_index('ix_support_messages_sender_id', table_name='support_messages')
    op.drop_index('ix_support_messages_thread_user_id', table_name='support_messages')
    op.drop_table('support_messages')
    op.drop_index('ix_case_messages_sender_id', table_name='case_messages')
    op.drop_index('ix_case_messages_case_id', table_name='case_messages')
    op.drop_table('case_messages')
