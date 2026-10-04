"""retire the Auditor / Read-only role

The platform runs five roles: Super Admin, Laboratory Admin / Manager, Test
Engineer / Metrologist, Technical Reviewer / Verifier and Approving Authority.
The Auditor role was a read-only observer that the operating model does not
need, so it is removed from the catalogue and any account still holding it is
disabled. The ``roles`` row itself is kept as an inert tombstone because
existing user rows reference it through a foreign key.

Revision ID: 0012
Revises: 0011
Created: 2026-10-04 10:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    users = sa.table(
        'users',
        sa.column('role_code', sa.String()),
        sa.column('is_active', sa.Boolean()),
    )
    op.execute(
        users.update().where(users.c.role_code == 'AUDITOR').values(is_active=sa.false())
    )

    grants = sa.table('role_permissions', sa.column('role_code', sa.String()))
    op.execute(grants.delete().where(grants.c.role_code == 'AUDITOR'))

    roles = sa.table(
        'roles',
        sa.column('code', sa.String()),
        sa.column('name', sa.String()),
        sa.column('description', sa.Text()),
    )
    op.execute(
        roles.update()
        .where(roles.c.code == 'AUDITOR')
        .values(
            name='Auditor (retired)',
            description='Retired role. Accounts holding it are disabled and cannot sign in.',
        )
    )


def downgrade() -> None:
    roles = sa.table(
        'roles',
        sa.column('code', sa.String()),
        sa.column('name', sa.String()),
        sa.column('description', sa.Text()),
    )
    op.execute(
        roles.update()
        .where(roles.c.code == 'AUDITOR')
        .values(
            name='Auditor / Read-only',
            description='Independent read-only access to history and audit trails',
        )
    )
