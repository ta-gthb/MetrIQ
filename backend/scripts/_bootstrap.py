"""Make the backend package importable, pin the working directory, and provide
the terminal prompts the operator scripts share.

Run every script from the repository root, for example:

    python backend/scripts/init_db.py
    python backend/scripts/seed_rules.py

`manage_admin.py` and `reinit_db.py` also accept no arguments at all: they then
ask for everything they need. Flags always win over prompts, so the same script
still works unattended in a pipe or a CI job.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Resolve relative database and storage paths against the backend directory so
# scripts behave identically regardless of the caller's working directory.
os.chdir(BACKEND_DIR)

# The scripts run from here, so this is the file app.config reads as `.env`.
ENV_FILE = BACKEND_DIR / ".env"


def banner(title: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)


def ok(message: str) -> None:
    print(f"  [ok] {message}")


def warn(message: str) -> None:
    print(f"  [!!] {message}")


def note(message: str) -> None:
    print(f"  {message}")


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """KEY=value pairs from backend/.env, for prompts to offer as defaults.

    Environment variables take precedence over this file at the call sites,
    which is the same order the application's settings use.
    """
    target = path or ENV_FILE
    if not target.is_file():
        return {}
    values: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def remember_env(values: dict[str, str], path: Path | None = None) -> Path:
    """Store KEY=value pairs in backend/.env, leaving every other line alone."""
    target = path or ENV_FILE
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    for key, value in values.items():
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.partition("=")[0].strip() == key:
                lines[index] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Terminal prompts
#
# A value given as a flag is never asked for again, so `manage_admin.py create
# --email x --password y` runs to completion without blocking. Prompts are only
# used when stdin is a real terminal: piping the script (CI, tests, `| tee`)
# behaves exactly as it did before they existed.
# ---------------------------------------------------------------------------


def interactive() -> bool:
    """True when there is a terminal to ask questions on."""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - closed stdin
        return False


_PASSWORD_IN_URL = re.compile(r"://([^:/@\s]+):[^@\s]*@")


def mask_url(url: str) -> str:
    """Hide the password in a connection string before printing it."""
    return _PASSWORD_IN_URL.sub(r"://\1:***@", url or "")


def prompt(label: str, *, default: str | None = None, allow_blank: bool = False) -> str:
    """Ask for a plain value; an empty answer keeps `default`."""
    hint = f" [{default}]" if default else ""
    while True:
        try:
            answer = input(f"  {label}{hint}: ").strip()
        except EOFError:
            return default or ""
        if answer:
            return answer
        if default is not None:
            return default
        if allow_blank:
            return ""
        warn("a value is required")


def read_secret(text: str) -> str:
    """Read a line without echoing it, degrading gracefully with no console."""
    try:
        return getpass.getpass(text)
    except (OSError, ValueError):  # pragma: no cover - no usable console
        warn("this terminal cannot hide input, so what you type will be visible")
        return input(text)


def prompt_secret(label: str, *, confirm_label: str | None = None) -> str:
    """Ask for a secret twice without echoing it; an empty answer returns ""."""
    while True:
        try:
            first = read_secret(f"  {label}: ")
        except EOFError:
            return ""
        if not first:
            return ""
        try:
            again = read_secret(f"  {confirm_label or 'repeat ' + label.lower()}: ")
        except EOFError:
            return ""
        if again != first:
            warn("the two entries did not match - try again")
            continue
        return first


def prompt_choice(label: str, choices, *, default: str | None = None) -> str:
    """Pick from `choices` - a sequence of (value, description) - by name or number."""
    for index, (value, description) in enumerate(choices, start=1):
        print(f"  {index}. {value:<14} {description}")
    names = {value for value, _ in choices}
    while True:
        answer = prompt(label, default=default).lower()
        if answer in names:
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1][0]
        warn("choose one of the numbers above")


def confirm(label: str, *, default: bool = False) -> bool:
    """Yes/no question; an empty answer keeps `default`."""
    hint = " [Y/n]" if default else " [y/N]"
    try:
        answer = input(f"  {label}{hint}: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")