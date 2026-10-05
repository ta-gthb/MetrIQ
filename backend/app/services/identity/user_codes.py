"""User IDs the platform issues for itself - one format per role.

An identifier is ``<role prefix><four-digit year><three digits>``::

    stmadm2026042   System Administrator
    labadm2026007   Laboratory Admin / Manager
    temadm2026118   Test Engineer / Metrologist
    trvadm2026053   Technical Reviewer / Verifier
    apradm2026002   Approving Authority / Signatory

A System Administrator identifier is issued only by ``backend/scripts/manage_admin.py``,
because that script is the supported way to create a platform administrator.
Every other identifier is issued when the account is created - by a System Administrator
through ``POST /api/v1/users``, or by the demonstration seed, which stands
in for that administrator on a deployed demonstration.

The digits are drawn at random and the unique index on ``users.user_code`` is
what makes the value a handle: :func:`generate_user_code` draws again until it
finds one the table does not already hold. The year is the year the account was
created, so an identifier also says how old an account is.

Two rules are deliberate:

* the role always comes from the account, never from the identifier. A code
  stays with the account if an administrator later changes its role, exactly as
  a staff number does;
* an account that predates this module is given an identifier the first time it
  signs in, so no account is left without one.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import User
from app.security.permissions import (
    APPROVER,
    ENGINEER,
    LAB_ADMIN,
    REVIEWER,
    SUPER_ADMIN,
)

#: Role code -> the six letters its identifiers start with.
PREFIXES: dict[str, str] = {
    SUPER_ADMIN: "stmadm",
    LAB_ADMIN: "labadm",
    ENGINEER: "temadm",
    REVIEWER: "trvadm",
    APPROVER: "apradm",
}

SERIAL_DIGITS = 3
SERIAL_LIMIT = 10 ** SERIAL_DIGITS
#: How many draws to make before deciding the space is full.
DRAW_ATTEMPTS = 200

CODE_PATTERN = re.compile(r"^(?P<prefix>[a-z]{6})(?P<year>[0-9]{4})(?P<serial>[0-9]{3})$")
KNOWN_PREFIXES = frozenset(PREFIXES.values())


class UnknownRole(ValueError):
    """The role has no identifier format, so none can be issued."""


def current_year() -> int:
    """The year an identifier issued now carries."""
    return datetime.now(timezone.utc).year


def prefix_for(role_code: str | None) -> str:
    try:
        return PREFIXES[role_code]
    except KeyError:
        raise UnknownRole(
            f"no user ID format is defined for role {role_code!r}"
        ) from None


def _draw() -> int:
    """One candidate serial. Replaced in tests to make the draws predictable."""
    return secrets.randbelow(SERIAL_LIMIT)


def normalise(value: str | None) -> str:
    """Identifiers are stored and compared in lower case."""
    return (value or "").strip().lower()


def is_user_code(value: str | None) -> bool:
    """Whether ``value`` is shaped like an identifier this platform issues."""
    match = CODE_PATTERN.match(normalise(value))
    return bool(match) and match.group("prefix") in KNOWN_PREFIXES


def compose(role_code: str, serial: int, *, year: int | None = None) -> str:
    """Build the identifier for ``role_code`` without checking the table."""
    year = current_year() if year is None else int(year)
    return f"{prefix_for(role_code)}{year}{int(serial) % SERIAL_LIMIT:0{SERIAL_DIGITS}d}"


def _taken(db, code: str) -> bool:
    return db.execute(
        select(User.id).where(User.user_code == code)
    ).scalars().first() is not None


def generate_user_code(db, role_code: str, *, year: int | None = None) -> str:
    """An identifier for ``role_code`` that no account holds yet.

    Raises :class:`RuntimeError` when the year's space for the role is
    exhausted, rather than returning something that is already in use.
    """
    prefix = prefix_for(role_code)
    for _ in range(DRAW_ATTEMPTS):
        candidate = compose(role_code, _draw(), year=year)
        if not _taken(db, candidate):
            return candidate
    raise RuntimeError(
        f"no free {prefix} identifier is left for {year or current_year()};"
        " an administrator has to resolve which accounts are still in use"
    )


def ensure_user_code(db, user: User, *, year: int | None = None) -> str:
    """The identifier ``user`` holds, issuing one first if it has none."""
    if user.user_code:
        return user.user_code
    user.user_code = generate_user_code(db, user.role_code, year=year)
    db.flush()
    return user.user_code


def find_by_code(db, value: str | None) -> User | None:
    code = normalise(value)
    if not code:
        return None
    return db.execute(select(User).where(User.user_code == code)).scalars().first()


def find_sign_in_user(db, identifier: str | None) -> User | None:
    """The account a sign-in name refers to, by identifier or by email address.

    A user ID is the identifier the platform issued; an email address stays
    accepted so an account can still be reached the way it always was. An
    unqualified name is tried as an identifier first, because that is what the
    format looks like, and as an address second.
    """
    value = (identifier or "").strip()
    if not value:
        return None
    if is_user_code(value):
        return find_by_code(db, value)
    if "@" in value:
        return db.execute(
            select(User).where(User.email == value.lower())
        ).scalars().first()
    return find_by_code(db, value) or db.execute(
        select(User).where(User.email == value.lower())
    ).scalars().first()