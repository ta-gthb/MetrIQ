"""Make the backend package importable and pin the working directory.

Run every script from the repository root, for example:

    python backend/scripts/init_db.py
    python backend/scripts/seed_rules.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Resolve relative database and storage paths against the backend directory so
# scripts behave identically regardless of the caller's working directory.
os.chdir(BACKEND_DIR)


def banner(title: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)


def ok(message: str) -> None:
    print(f"  [ok] {message}")


def warn(message: str) -> None:
    print(f"  [!!] {message}")
