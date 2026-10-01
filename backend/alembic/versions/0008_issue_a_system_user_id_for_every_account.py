"""issue a system user ID for every account

Every account carries an identifier the platform issues for itself, so a person
can sign in with it and the application reads the account - and therefore the
role - from it. New accounts are given one as they are created
(``app.services.identity.user_codes``); this migration gives one to the accounts
that already exist.

The backfill is deterministic: the last three digits count up per role in a
fixed order rather than being drawn at random the way a new account's are, so
the same database always produces the same identifiers. ``PREFIXES`` below is
frozen as of this revision - ``tests/test_user_codes.py`` fails if it drifts
from ``app.services.identity.user_codes``.

Revision ID: 0008
Revises: 0007
Created: 2026-09-30 00:40:00.000000

"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import context, op
import sqlalchemy as sa
# Rendered rather than imported by Alembic: JSONB's astext_type comes out as a
# bare `Text()`, which would be a NameError without this line.
from sqlalchemy import Text  # noqa: F401


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

#: (role code, identifier prefix) as of this revision.
PREFIXES: tuple[tuple[str, str], ...] = (
    ("SUPER_ADMIN", "stmadm"),
    ("LAB_ADMIN", "labadm"),
    ("ENGINEER", "temadm"),
    ("REVIEWER", "trvadm"),
    ("APPROVER", "apradm"),
    ("AUDITOR", "audadm"),
)


def upgrade() -> None:
    # Added nullable so the rows that already exist can be given a value, then
    # tightened: an account without an identifier cannot sign in by one, and
    # nothing should be able to create one.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_code', sa.String(length=32), nullable=True))

    prefixes = dict(PREFIXES)
    year = datetime.now(timezone.utc).year
    counters: dict[str, int] = {}
    bind = op.get_bind()
    if context.is_offline_mode():
        # Offline Alembic has no result set to iterate. Keep the generated
        # PostgreSQL script executable by expressing the same deterministic
        # role-local numbering as one set-based update.
        op.execute(sa.text("""
            WITH numbered AS (
                SELECT id, role_code,
                       row_number() OVER (
                           PARTITION BY role_code ORDER BY created_at, email
                       ) AS sequence_number
                FROM users
            )
            UPDATE users
            SET user_code = CASE numbered.role_code
                WHEN 'SUPER_ADMIN' THEN 'stmadm'
                WHEN 'LAB_ADMIN' THEN 'labadm'
                WHEN 'ENGINEER' THEN 'temadm'
                WHEN 'REVIEWER' THEN 'trvadm'
                WHEN 'APPROVER' THEN 'apradm'
                WHEN 'AUDITOR' THEN 'audadm'
            END
            || EXTRACT(YEAR FROM CURRENT_TIMESTAMP)::integer::text
            || LPAD(numbered.sequence_number::text, 3, '0')
            FROM numbered
            WHERE users.id = numbered.id
        """))
    else:
        rows = bind.execute(
            sa.text("select id, role_code from users order by created_at, email")
        ).fetchall()
        for user_id, role_code in rows:
            prefix = prefixes.get(role_code)
            if prefix is None:
                raise RuntimeError(
                    f"users.role_code {role_code!r} has no identifier format; add one"
                    " before migrating"
                )
            counters[prefix] = counters.get(prefix, 0) + 1
            bind.execute(
                sa.text("update users set user_code = :code where id = :user_id"),
                {"code": f"{prefix}{year}{counters[prefix]:03d}", "user_id": user_id},
            )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column(
            'user_code', existing_type=sa.String(length=32), nullable=False
        )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index('ix_users_user_code', ['user_code'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('ix_users_user_code')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('user_code')
