"""Public platform endpoints.

Everything under this router is reachable without a token, because the home
page is: a visitor has to be able to read what the platform is and how much work
it has handled before they have an account. It therefore exposes aggregates and
published configuration only - never a record.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import platform_statistics

router = APIRouter(tags=["Platform"])


@router.get(
    "/platform/statistics",
    summary="Aggregate platform statistics for the home page",
)
def read_platform_statistics(db: Session = Depends(get_db)) -> dict:
    """Live counts of what this instance has handled.

    Deliberately unauthenticated and deliberately aggregate: no case number,
    party, instrument or person appears in the response, so publishing it
    discloses the platform's activity without disclosing anyone's work.
    """
    return platform_statistics.snapshot(db)
