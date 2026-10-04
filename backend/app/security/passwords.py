"""Password hashing for the local (offline/demo) authentication provider.

Supabase Auth remains the production identity provider. This module exists so
the platform stays fully usable when Supabase is not configured, which is an
explicit PRD requirement (12.5 "Failure behaviour", 22.4 "graceful degradation").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

try:  # pragma: no cover - optional acceleration
    import bcrypt as _bcrypt
except Exception:  # pragma: no cover
    _bcrypt = None

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    if _bcrypt is not None:
        digest = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt(rounds=12))
        return digest.decode("utf-8")
    salt = os.urandom(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "{}${}${}${}".format(
        PBKDF2_ALGORITHM,
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    if stored.startswith("$2"):  # bcrypt
        if _bcrypt is None:
            return False
        try:
            return _bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except ValueError:
            return False
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != PBKDF2_ALGORITHM:
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(hash_b64)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(derived, expected)


def generate_temporary_password(length: int = 14) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%"
    raw = os.urandom(length)
    return "".join(alphabet[byte % len(alphabet)] for byte in raw)


PASSWORD_POLICY_TEXT = (
    "at least 8 characters, including at least one letter, one digit and one special character"
)


def password_policy_error(password: str | None) -> str | None:
    """Return a human-readable policy failure, or None when the value passes.

    Alphanumeric means at least one letter and one digit; a special character
    is any non-alphanumeric character other than whitespace.
    """
    if not password or len(password) < 8:
        return f"Password must be {PASSWORD_POLICY_TEXT}."
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    has_special = any((not ch.isalnum()) and (not ch.isspace()) for ch in password)
    if not (has_letter and has_digit and has_special):
        return f"Password must be {PASSWORD_POLICY_TEXT}."
    return None


def validate_password_strength(password: str) -> str:
    """Validate and return the password, raising ValueError when it fails."""
    error = password_policy_error(password)
    if error:
        raise ValueError(error)
    return password
