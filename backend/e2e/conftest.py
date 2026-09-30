"""Browser end-to-end fixtures: a real server, a throwaway database, one browser.

These tests are opt-in and live outside the deterministic suite:

    cd backend
    python -m playwright install chromium     # once
    python -m pytest e2e -q

`pytest.ini` points the default test run at `tests/`, so a missing browser or a
slow machine never blocks the unit and API suites. The server is the real
application served by uvicorn over HTTP, seeded with the demonstration dataset,
so the browser exercises the same wiring a deployment uses.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="metriq-e2e-"))
(_TMP / "storage").mkdir(parents=True, exist_ok=True)
(_TMP / "reports").mkdir(parents=True, exist_ok=True)

# Set before any application module is imported: settings are read once.
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'metriq-e2e.db').as_posix()}"
os.environ["DB_SCHEMA"] = ""
os.environ["AUTH_PROVIDER"] = "local"
os.environ["DEMO_MODE"] = "true"
os.environ["AI_PROVIDER"] = "stub"
os.environ["AI_ENABLED"] = "true"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = str(_TMP / "storage")
os.environ["REPORT_STORAGE_PATH"] = str(_TMP / "reports")
os.environ["JWT_SECRET"] = "e2e-only-secret-not-for-deployment"
os.environ["AUTO_INIT_DB"] = "true"
os.environ["AUTO_SEED_REFERENCE"] = "true"

from scripts.seed_db import DEFAULT_PASSWORD  # noqa: E402

ADMIN_ACCOUNT = "admin@metriq.local"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str, *, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - the last error is reported
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"the server did not answer {url}: {last_error}")


@pytest.fixture(scope="session")
def server() -> str:
    """A live uvicorn process serving the application with demo data."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    creation = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    log = (_TMP / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creation,
    )
    try:
        _wait_for(f"{url}/health")
        seeded = subprocess.run(
            [sys.executable, "scripts/seed_db.py", "--password", DEFAULT_PASSWORD],
            cwd=BACKEND_DIR,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if seeded.returncode != 0:
            raise RuntimeError(
                f"demo seeding failed ({seeded.returncode}):\n{seeded.stdout}\n{seeded.stderr}"
            )
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn process
            process.kill()
        log.close()


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="Playwright is not installed (pip install playwright)"
    )
    with sync_api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - the browser binary is a prerequisite
            pytest.skip(
                "Chromium for Playwright is not installed "
                f"({exc}); run: python -m playwright install chromium"
            )
        yield instance
        instance.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(20000)
    yield page
    context.close()
