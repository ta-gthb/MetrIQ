"""Drop and recreate every table. DEVELOPMENT ONLY (PRD 20.2).

    python backend/scripts/reset_db.py --yes
"""

from __future__ import annotations

import sys

from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._bootstrap import banner, warn  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.models import Base  # noqa: E402


def main() -> int:
    banner("MetrIQ - reset database (DESTRUCTIVE)")
    if settings.ENVIRONMENT == "production":
        print("  Refusing to run: ENVIRONMENT is 'production'.")
        return 2
    if "--yes" not in sys.argv:
        print("  This deletes all data. Re-run with --yes to confirm.")
        return 1
    warn(f"dropping all tables in {engine.url.render_as_string(hide_password=True)}")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("  [ok] database reset complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
