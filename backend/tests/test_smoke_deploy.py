"""The deployment smoke script must pass against this application (audit item 18).

The script is the release gate an operator runs after deploying, so it is held
to the same standard as the rest of the suite: every check it performs must
succeed against the real application, and every route it requires must exist.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.security.permissions import ENGINEER
from scripts.smoke_deploy import REQUIRED_OPENAPI_PATHS, check_instance


def test_every_route_the_smoke_test_requires_is_published():
    paths = set((app.openapi().get("paths") or {}).keys())
    missing = [path for path in REQUIRED_OPENAPI_PATHS if path not in paths]
    assert not missing, missing


def test_the_smoke_check_passes_against_this_application(accounts):
    with TestClient(app, base_url="http://smoke.test") as client:
        results = check_instance(
            client,
            api_base="http://smoke.test",
            frontend_base="http://smoke.test",
            user=accounts["emails"][ENGINEER],
            password=accounts["password"],
        )
    failures = [result.line() for result in results if not result.ok and not result.skipped]
    assert not failures, "\n".join(failures)
    names = {result.name for result in results}
    assert "Required API routes published" in names
    assert "Sign-in" in names
    assert "Authenticated profile" in names
    assert "Frontend API rewrite" in names
