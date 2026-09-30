"""Fixed-window rate limits for the endpoints that invite abuse.

Sign-in can be used to guess credentials, the public verification endpoint to
enumerate codes, and uploads to fill a bucket. Each of those gets a small
per-identity budget.

The counters live in this process. A single Render container serves the API, so
that is where the traffic arrives; the limiter is still described as
best-effort rather than a guarantee, because a restarted process starts with
empty counters and a second replica would keep its own. A deployment that needs
a hard quota puts one in front of the service - see
docs/deployment/README.md - and the limiter's job is to make the cheap attacks
expensive in the deployment as shipped.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass(frozen=True, slots=True)
class RateLimit:
    """``limit`` attempts per ``window_seconds`` for one identity."""

    name: str
    limit: int
    window_seconds: int


#: Sign-in: ten attempts a minute from one address is far above a human pace.
AUTH_LOGIN = RateLimit("auth-login", limit=10, window_seconds=60)
#: Token refresh and created sessions: generous, but not unbounded.
AUTH_SESSION = RateLimit("auth-session", limit=60, window_seconds=60)
#: The signed-out verification endpoint: a page reads it once per scan.
VERIFY = RateLimit("verify", limit=60, window_seconds=60)
#: Evidence uploads: a handful per minute is a person working, not a script.
UPLOAD = RateLimit("upload", limit=30, window_seconds=60)


class _Window:
    __slots__ = ("count", "started")

    def __init__(self, started: float) -> None:
        self.count = 0
        self.started = started


_windows: dict[tuple[str, str], _Window] = {}
_lock = threading.Lock()


def client_identity(request: Request) -> str:
    """The address a limit is counted against.

    Render terminates TLS in front of the service and forwards the real address
    in ``X-Forwarded-For``; the first entry is the client. A direct caller can
    forge the header, which is exactly why this is a cost, not a guarantee.
    """
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def check(request: Request, limit: RateLimit, identity: str | None = None) -> None:
    """Count one attempt and raise 429 once the window is exhausted."""
    key = (limit.name, identity or client_identity(request))
    now = time.monotonic()
    with _lock:
        window = _windows.get(key)
        if window is None or now - window.started >= limit.window_seconds:
            window = _Window(now)
            _windows[key] = window
        window.count += 1
        if window.count > limit.limit:
            retry_after = max(1, int(limit.window_seconds - (now - window.started)) + 1)
    if window.count > limit.limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def reset() -> None:
    """Forget every window. Used between tests."""
    with _lock:
        _windows.clear()