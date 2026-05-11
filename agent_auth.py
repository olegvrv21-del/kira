"""Optional bearer-token authentication and per-IP rate limiting.

Both features are OFF by default and enabled via environment variables:

  KIRA_AUTH_TOKEN=<secret>          -> require Authorization: Bearer <secret>
                                       (or X-Kira-Token: <secret>) on protected
                                       routes. Comma-separate to allow multiple.
  KIRA_AUTH_ALLOW_PUBLIC=/healthz,/  -> CSV of routes that bypass auth
                                       (defaults below cover health, static
                                       assets, and the HTML shell).

  KIRA_RATE_LIMIT=<n>                -> max requests per RATE_WINDOW_SECONDS
                                       on protected POST endpoints, per IP.
                                       Defaults to 60.
  KIRA_RATE_WINDOW=<seconds>         -> sliding window length (default 60).

When disabled (no token set) the middleware is a no-op besides recording
request counts for /agent/limits style introspection.
"""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_DEFAULT_PUBLIC = (
    "/healthz",
    "/static/",
    "/favicon.ico",
    "/apple-touch-icon.png",
    "/robots.txt",
)

# Routes we want to rate-limit (everything that triggers an LLM call or a
# tool execution). Coverage-run is also limited because it's heavy.
_RATE_LIMITED_PREFIXES = (
    "/agent",
    "/chat",
)


def _split_csv(v: str | None) -> tuple[str, ...]:
    if not v:
        return ()
    return tuple(x.strip() for x in v.split(",") if x.strip())


class _Limiter:
    """Per-IP sliding-window rate limiter. Thread-unsafe by design — FastAPI
    runs middleware on the asyncio loop so we serialise naturally."""

    def __init__(self, limit: int, window: float):
        self.limit = max(0, limit)
        self.window = max(1.0, window)
        self.hits: dict[str, deque[float]] = {}

    def allow(self, ip: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        if self.limit <= 0:
            return True, 0
        now = time.time()
        bucket = self.hits.setdefault(ip, deque())
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry = max(1, int(self.window - (now - bucket[0])))
            return False, retry
        bucket.append(now)
        return True, 0

    def snapshot(self) -> dict:
        return {
            "limit": self.limit,
            "window_seconds": self.window,
            "tracked_ips": len(self.hits),
            "total_recent_hits": sum(len(q) for q in self.hits.values()),
        }


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, tokens: Iterable[str], public_prefixes: Iterable[str], limiter: _Limiter):
        super().__init__(app)
        self.tokens = tuple(tokens)
        self.public = tuple(public_prefixes)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # ---- bearer auth ----
        if self.tokens and not self._is_public(path):
            token = self._extract_token(request)
            if token not in self.tokens:
                return JSONResponse({"error": "unauthorized"}, status_code=401)

        # ---- rate limit on heavy endpoints ----
        if self._is_rate_limited(path, request.method):
            ip = self._client_ip(request)
            ok, retry = self.limiter.allow(ip)
            if not ok:
                return JSONResponse(
                    {"error": "rate_limit", "retry_after": retry},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )

        return await call_next(request)

    def _is_public(self, path: str) -> bool:
        # Exact match to '/' (the HTML shell) is always public.
        if path == "/":
            return True
        return any(path == p or path.startswith(p) for p in self.public)

    def _is_rate_limited(self, path: str, method: str) -> bool:
        if method == "GET":
            # Reading metrics/sessions is cheap; rate-limit only state-changing
            # or LLM-triggering requests.
            return False
        return any(path.startswith(p) for p in _RATE_LIMITED_PREFIXES)

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        h = request.headers.get("authorization") or ""
        if h.lower().startswith("bearer "):
            return h.split(" ", 1)[1].strip()
        return request.headers.get("x-kira-token")

    @staticmethod
    def _client_ip(request: Request) -> str:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


def build_middleware() -> tuple[type[AuthRateLimitMiddleware] | None, dict]:
    """Read env, return (middleware_class, init_kwargs) or (None, {}) when off."""
    tokens = _split_csv(os.environ.get("KIRA_AUTH_TOKEN"))
    public_extra = _split_csv(os.environ.get("KIRA_AUTH_ALLOW_PUBLIC"))
    limit = int(os.environ.get("KIRA_RATE_LIMIT") or 60)
    window = float(os.environ.get("KIRA_RATE_WINDOW") or 60)
    limiter = _Limiter(limit, window)
    auth_on = bool(tokens)
    rl_on = limit > 0
    if not auth_on and not rl_on:
        return None, {}
    return AuthRateLimitMiddleware, {
        "tokens": tokens,
        "public_prefixes": tuple(_DEFAULT_PUBLIC) + public_extra,
        "limiter": limiter,
    }


# Singleton limiter so /agent/auth_status can report it.
_GLOBAL_LIMITER: _Limiter | None = None


def install(app) -> dict:
    """Attach middleware to the FastAPI app and return a status summary."""
    global _GLOBAL_LIMITER
    cls, kwargs = build_middleware()
    if cls is None:
        _GLOBAL_LIMITER = None
        return {"auth": False, "rate_limit": False}
    app.add_middleware(cls, **kwargs)
    _GLOBAL_LIMITER = kwargs["limiter"]
    return {
        "auth": bool(kwargs["tokens"]),
        "tokens_configured": len(kwargs["tokens"]),
        "public_prefixes": list(kwargs["public_prefixes"]),
        "rate_limit": kwargs["limiter"].limit > 0,
        "limit_per_window": kwargs["limiter"].limit,
        "window_seconds": kwargs["limiter"].window,
    }


def snapshot() -> dict:
    if _GLOBAL_LIMITER is None:
        return {"enabled": False}
    return {"enabled": True, **_GLOBAL_LIMITER.snapshot()}
