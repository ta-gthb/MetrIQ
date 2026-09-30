"""Deployment smoke test for a hosted MetrIQ instance (audit item 18).

Run it after a deployment to prove the pieces a release depends on answer:

* the API index, health summary and OpenAPI document;
* the aggregate statistics the home page reads without a token;
* the frontend entry pages and the /api rewrite that fronts the API;
* optionally, one authenticated round trip (--token, or --user/--password).

The script is read-only: it never creates, changes or deletes a record.

Usage:
  python scripts/smoke_deploy.py --api https://metriq-api-vgao.onrender.com
  python scripts/smoke_deploy.py --api https://metriq-api-vgao.onrender.com \
      --frontend https://metriq.vercel.app
  python scripts/smoke_deploy.py --api ... --user <user-id> --password <password>

Exits 0 when every check passed and 1 otherwise. A free hosting instance may
sleep, so --timeout 90 --retries 3 is the safe default for a first request.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import httpx

#: Paths a release must expose for the browser application to work end to end.
REQUIRED_OPENAPI_PATHS = (
    "/api/v1/auth/login",
    "/api/v1/me",
    "/api/v1/cases",
    "/api/v1/ai/report-consistency",
    "/api/v1/verify/{code}",
)


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False

    def line(self) -> str:
        if self.skipped:
            return f"[SKIP] {self.name}" + (f" - {self.detail}" if self.detail else "")
        mark = "PASS" if self.ok else "FAIL"
        return f"[{mark}] {self.name}" + (f" - {self.detail}" if self.detail else "")


class Smoke:
    """Small HTTP helper that records one result per check."""

    def __init__(self, client: httpx.Client, *, retries: int = 0, pause: float = 5.0) -> None:
        self.client = client
        self.retries = max(0, retries)
        self.pause = pause
        self.results: list[CheckResult] = []
        self.token: str | None = None

    def record(self, name: str, ok: bool, detail: str = "", *, skipped: bool = False) -> None:
        self.results.append(CheckResult(name=name, ok=ok, detail=detail, skipped=skipped))

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self.client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt > self.retries:
                    raise
                print(f"      retrying {url} after {exc.__class__.__name__} (attempt {attempt})")
                time.sleep(self.pause)

    def get(
        self,
        name: str,
        url: str,
        *,
        expect: int = 200,
        contains: tuple[str, ...] = (),
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | None:
        try:
            response = self.request("GET", url, headers=headers)
        except httpx.HTTPError as exc:
            self.record(name, False, f"request failed: {exc}")
            return None
        if response.status_code != expect:
            self.record(name, False, f"HTTP {response.status_code} (expected {expect})")
            return None
        for marker in contains:
            if marker not in response.text:
                self.record(name, False, f"response does not contain {marker!r}")
                return None
        self.record(name, True, f"HTTP {expect}")
        return response

    @property
    def failures(self) -> list[CheckResult]:
        return [result for result in self.results if not result.ok and not result.skipped]


def check_instance(
    client: httpx.Client,
    *,
    api_base: str,
    frontend_base: str | None = None,
    user: str | None = None,
    password: str | None = None,
    token: str | None = None,
    retries: int = 0,
    pause: float = 5.0,
) -> list[CheckResult]:
    """Run every deployment check and return the results in order."""
    smoke = Smoke(client, retries=retries, pause=pause)
    api = api_base.rstrip("/")
    frontend = (frontend_base or "").rstrip("/")

    smoke.get("API index", f"{api}/api/v1", contains=("OIML R 76-1:2006",))

    health = smoke.get("Health summary", f"{api}/health")
    if health is not None:
        try:
            body = health.json()
        except ValueError:
            body = {}
            smoke.record("Health payload", False, "response is not JSON")
        problems = []
        if body.get("status") != "ok":
            problems.append(f"status={body.get('status')}")
        if body.get("database") != "ok":
            problems.append(f"database={body.get('database')}")
        if body.get("calculation_engine_independent_of_ai") is not True:
            problems.append("computation is not independent of AI")
        detail = (
            f"version {body.get('version')}, commit {body.get('git_commit')}, "
            f"schema {body.get('schema_revision')}, storage {body.get('storage_backend')}"
        )
        smoke.record("Health details", not problems, "; ".join(problems) or detail)

    document = smoke.get("OpenAPI document", f"{api}/openapi.json")
    if document is not None:
        paths = set((document.json().get("paths") or {}).keys())
        missing = sorted(path for path in REQUIRED_OPENAPI_PATHS if path not in paths)
        smoke.record("Required API routes published", not missing, ", ".join(missing))

    smoke.get(
        "Public platform statistics",
        f"{api}/api/v1/platform/statistics",
        contains=("evaluations",),
    )

    if frontend:
        smoke.get("Frontend home page", f"{frontend}/", contains=("MetrIQ",))
        smoke.get("Frontend evaluation page", f"{frontend}/evaluation.html", contains=("evaluation.js",))
        smoke.get(
            "Frontend API rewrite",
            f"{frontend}/api/v1",
            contains=("OIML R 76-1:2006",),
        )

    if not token and user and password:
        try:
            login = smoke.request(
                "POST", f"{api}/api/v1/auth/login", json={"user_id": user, "password": password}
            )
        except httpx.HTTPError as exc:
            smoke.record("Sign-in", False, f"request failed: {exc}")
        else:
            if login.status_code == 200:
                token = (login.json() or {}).get("access_token")
                smoke.record("Sign-in", bool(token), "access token issued")
            elif login.status_code == 403:
                smoke.record(
                    "Sign-in",
                    True,
                    "password sign-in is disabled on this deployment; pass --token instead",
                    skipped=True,
                )
            else:
                smoke.record("Sign-in", False, f"HTTP {login.status_code}")

    if token:
        headers = {"Authorization": f"Bearer {token}"}
        smoke.get("Authenticated profile", f"{api}/api/v1/me", headers=headers, contains=("role_code",))
        smoke.get("Authenticated case list", f"{api}/api/v1/cases?page_size=1", headers=headers)

    return smoke.results


def build_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only smoke test for a deployed MetrIQ instance")
    parser.add_argument("--api", required=True, help="API base URL, for example https://metriq-api-vgao.onrender.com")
    parser.add_argument("--frontend", help="Frontend base URL, for example https://metriq.vercel.app")
    parser.add_argument("--user", help="User ID or e-mail for an authenticated check")
    parser.add_argument("--password", help="Password for --user")
    parser.add_argument("--token", help="Access token for an authenticated check")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="Retries per request (cold starts)")
    args = parser.parse_args(argv)

    with build_client(args.timeout) as client:
        results = check_instance(
            client,
            api_base=args.api,
            frontend_base=args.frontend,
            user=args.user,
            password=args.password,
            token=args.token,
            retries=args.retries,
        )

    print(f"MetrIQ deployment smoke test: {args.api}")
    for result in results:
        print("  " + result.line())
    failures = [result for result in results if not result.ok and not result.skipped]
    passed = len([result for result in results if result.ok and not result.skipped])
    print(f"{passed} check(s) passed, {len(failures)} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
