"""In-process sliding-window rate limiter (per client IP).

Suitable for a single API process. For horizontally scaled deployments put a gateway-level
limiter (or a Redis-backed one) in front of the API; this remains as a second line of defence.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float = 60.0) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False, int(window - (now - q[0])) + 1
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory under abuse
                for k in list(self._hits)[:10_000]:
                    self._hits.pop(k, None)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindow()

AUTH_PATHS = ("/api/v1/auth/login", "/api/v1/auth/register")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and settings.ENVIRONMENT != "test":
            ip = request.client.host if request.client else "unknown"
            if path in AUTH_PATHS and request.method == "POST":
                ok, retry = limiter.allow(f"auth:{ip}", settings.AUTH_RATE_LIMIT_PER_MINUTE)
            else:
                ok, retry = limiter.allow(f"api:{ip}", settings.RATE_LIMIT_PER_MINUTE)
            if not ok:
                return JSONResponse(
                    status_code=429,
                    headers={"retry-after": str(retry)},
                    content={"error": {"code": "rate_limited", "message": "Too many requests. Please slow down.",
                                       "details": {"retry_after_seconds": retry}}},
                )
        return await call_next(request)
