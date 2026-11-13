"""Transport-level protection for the engine.

The database is private research data and several routes are destructive
(clear fixtures, rebuild ratings, replay backtests), so a production engine
must never answer an unauthenticated caller. This module holds the policy the
HTTP middleware applies: which paths are public, how a client is identified
behind the proxy, and the in-memory rate budgets that make key guessing
impractical without adding a dependency.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Set, Tuple

DOCS_PATHS: Set[str] = frozenset(
    {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)
ALWAYS_PUBLIC: Set[str] = frozenset({"/", "/health"})

# Replaying history or re-fitting the model is minutes of CPU per call.
EXPENSIVE_PREFIXES: Tuple[str, ...] = (
    "/combos/backtest",
    "/learning/train",
    "/evaluation/",
    "/backtest/",
    "/fixtures/seed-historical",
    "/fixtures/fetch-",
    "/live/export",
    "/ledger/settle",
)

UPLOAD_PREFIXES: Tuple[str, ...] = ("/fixtures/upload",)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "test", "testing", "local"})


def is_production(environment: str) -> bool:
    return environment.strip().lower() not in DEVELOPMENT_ENVIRONMENTS


def docs_are_public(environment: str) -> bool:
    return not is_production(environment)


def is_public_path(path: str, environment: str) -> bool:
    if path in ALWAYS_PUBLIC:
        return True
    if path in DOCS_PATHS:
        return docs_are_public(environment)
    return False


def request_class(method: str, path: str) -> str:
    if any(path.startswith(prefix) for prefix in EXPENSIVE_PREFIXES):
        return "expensive"
    if method.upper() in WRITE_METHODS:
        return "write"
    return "read"


def body_limit(path: str, default: int, upload: int) -> int:
    return upload if any(path.startswith(prefix) for prefix in UPLOAD_PREFIXES) else default


def client_identity(headers, peer: Optional[str]) -> str:
    """Best-effort client IP behind a single reverse proxy.

    Proxies append the address they saw to the right of ``X-Forwarded-For``;
    the rightmost entry is the one that cannot be chosen by the caller. An
    empty or unparsable chain falls back to the socket peer.
    """
    forwarded = headers.get("x-forwarded-for") if headers else None
    if forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1]
    return peer or "unknown"


class RateLimiter:
    """A sliding-window counter per key, held in process memory."""

    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: Optional[float] = None) -> Tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        if limit <= 0:
            return True, 0
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(1, int(round(hits[0] + self.window_seconds - current)))
                return False, retry
            hits.append(current)
        return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def limit_for(kind: str, read: int, write: int, expensive: int) -> int:
    if kind == "expensive":
        return expensive
    if kind == "write":
        return write
    return read


SECURITY_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}
