"""Create every database table (PRD 20.2).

    python backend/scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, ok  # noqa: E402  (path bootstrap first)

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.models import Base  # noqa: E402
from app.services.reference_data.bootstrap import ensure_schema, require_schema  # noqa: E402


def main() -> None:
    banner("MetrIQ - initialise database schema")
    print(f"  target: {engine.url.render_as_string(hide_password=True)}")
    if settings.DB_SCHEMA:
        print(f"  schema: {settings.DB_SCHEMA}")
    ensure_schema()
    require_schema()
    tables = sorted(Base.metadata.tables)
    ok(f"schema ready with {len(tables)} tables")
    for name in tables:
        print(f"       - {name}")
    print()
    print("  Next: python backend/scripts/seed_rules.py")
    print("        python backend/scripts/seed_identity.py")
    print("        python backend/scripts/create_admin.py")
    print("        python backend/scripts/seed_db.py")
    if settings.DB_SCHEMA:
        print()
        print(f"  NOTE: tables live in the '{settings.DB_SCHEMA}' schema; other schemas are untouched.")
    elif settings.DATABASE_URL.startswith("sqlite"):
        print()
        print("  NOTE: SQLite is being used. Configure DATABASE_URL for PostgreSQL in production.")


if __name__ == "__main__":
    main()
