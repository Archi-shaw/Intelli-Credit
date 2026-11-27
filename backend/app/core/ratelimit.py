"""Distributed rate limiting (Redis token buckets) with a documented in-process fallback.

Every API request is checked against the buckets that apply to it:

* unauthenticated requests      → per client IP                      (``RATE_LIMIT_IP``)
* login / registration          → stricter per client IP              (``RATE_LIMIT_AUTH``)
* authenticated requests        → per user and per organisation        (``RATE_LIMIT_USER`` / ``RATE_LIMIT_ORG``)
* AI endpoints (assistant, research, provider tests)
                                → extra per-user / per-org buckets     (``RATE_LIMIT_AI_USER`` / ``RATE_LIMIT_AI_ORG``)
* document processing / analysis (uploads, re-processing, reconciliation, assessment, CAM)
                                → extra per-user / per-org buckets     (``RATE_LIMIT_PROCESSING_USER`` / ``..._ORG``)

Atomicity: one Lua script refills and checks *all* buckets of a request and consumes from them only if every
bucket has a token (all-or-nothing), using Redis server time, so concurrent requests on any number of API
instances can never over-admit and instances need no clock agreement.

Client identity comes from the session JWT (signature-verified, no database round-trip) and the client IP from
``app.core.netutil.client_ip`` (``X-Forwarded-For`` honoured only from ``TRUSTED_PROXIES``).

Redis failure behaviour (``RATE_LIMIT_FAIL_MODE``): ``local`` (default) keeps enforcing the same limits per
process; ``open`` allows the request; ``closed`` rejects with 503. A short circuit breaker avoids paying a
connection timeout on every request during an outage.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.netutil import client_ip

log = logging.getLogger("intellicredit.ratelimit")

_PERIODS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True)
class Limit:
    capacity: int
    period_s: int

    @property
    def per_ms(self) -> float:
        return self.capacity / (self.period_s * 1000.0)

    def __str__(self) -> str:
        return f"{self.capacity}/{self.period_s}s"


def parse_limit(spec: str) -> Limit:
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d*)\s*(second|minute|hour|day)s?\s*", spec or "")
    if not m:
        raise ValueError(f"Invalid rate limit '{spec}'. Use e.g. '300/minute' or '20/10minute'.")
    n, mult, unit = int(m.group(1)), int(m.group(2) or 1), m.group(3)
    if n <= 0:
        raise ValueError("Rate limit capacity must be positive.")
    return Limit(n, mult * _PERIODS[unit])


@dataclass(frozen=True)
class Bucket:
    key: str
    limit: Limit
    scope: str  # human-readable: "ip", "auth", "user", "organisation", "ai:user", ...


@dataclass
class Decision:
    allowed: bool
    retry_after_s: int = 0
    limit: int = 0
    remaining: int = 0
    scope: str = ""
    degraded: bool = False  # decided without Redis


# ---------------------------------------------------------------------------------------------- Redis backend

TOKEN_BUCKET_LUA = """
-- KEYS: bucket keys; ARGV: cost, then (capacity, rate_per_ms, ttl_ms) per key.
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local cost = tonumber(ARGV[1])
local n = #KEYS
local tokens = {}
local allowed = 1
local retry_ms = 0
local deny_idx = 0
local min_remaining = -1
local min_idx = 1
for i = 1, n do
  local cap = tonumber(ARGV[2 + (i - 1) * 3])
  local rate = tonumber(ARGV[3 + (i - 1) * 3])
  local state = redis.call('HMGET', KEYS[i], 'tk', 'ts')
  local tk = tonumber(state[1])
  local ts = tonumber(state[2])
  if tk == nil or ts == nil then
    tk = cap
    ts = now
  end
  local elapsed = now - ts
  if elapsed < 0 then elapsed = 0 end
  tk = math.min(cap, tk + elapsed * rate)
  tokens[i] = tk
  if tk < cost then
    allowed = 0
    local wait = math.ceil((cost - tk) / rate)
    if wait > retry_ms then
      retry_ms = wait
      deny_idx = i
    end
  end
end
for i = 1, n do
  local ttl = tonumber(ARGV[4 + (i - 1) * 3])
  local tk = tokens[i]
  if allowed == 1 then tk = tk - cost end
  redis.call('HSET', KEYS[i], 'tk', tostring(tk), 'ts', tostring(now))
  redis.call('PEXPIRE', KEYS[i], ttl)
  local rem = math.floor(tk)
  if min_remaining < 0 or rem < min_remaining then
    min_remaining = rem
    min_idx = i
  end
end
return {allowed, retry_ms, deny_idx, min_remaining, min_idx}
"""


class RedisBackend:
    def __init__(self, url: str, timeout_s: float = 0.3):
        import redis.asyncio as aioredis

        # Short timeouts on purpose: a store slower than this counts as an outage and RATE_LIMIT_FAIL_MODE applies,
        # instead of every API request waiting on a sick Redis.
        self._client = aioredis.Redis.from_url(url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s,
                                               health_check_interval=30)
        self._script = self._client.register_script(TOKEN_BUCKET_LUA)

    async def check(self, buckets: list[Bucket], cost: int = 1) -> Decision:
        args: list = [cost]
        for b in buckets:
            args += [b.limit.capacity, repr(b.limit.per_ms), b.limit.period_s * 1000 * 2]
        allowed, retry_ms, deny_idx, remaining, min_idx = await self._script(keys=[b.key for b in buckets], args=args)
        ref = buckets[(deny_idx or min_idx) - 1]
        return Decision(bool(allowed), max(1, math.ceil(int(retry_ms) / 1000)) if not allowed else 0,
                        ref.limit.capacity, max(0, int(remaining)), ref.scope)

    async def delete_prefix(self, prefix: str) -> None:
        async for k in self._client.scan_iter(match=f"{prefix}*", count=500):
            await self._client.delete(k)

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------------------------- local backend

class LocalBackend:
    """Same token-bucket semantics in process memory (fallback and single-instance mode)."""

    def __init__(self) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    async def check(self, buckets: list[Bucket], cost: int = 1) -> Decision:
        return self.check_sync(buckets, cost)

    def check_sync(self, buckets: list[Bucket], cost: int = 1) -> Decision:
        now = time.monotonic() * 1000
        with self._lock:
            tokens = []
            retry_ms, deny = 0.0, None
            for b in buckets:
                tk, ts = self._state.get(b.key, (float(b.limit.capacity), now))
                tk = min(b.limit.capacity, tk + max(0.0, now - ts) * b.limit.per_ms)
                tokens.append(tk)
                if tk < cost:
                    wait = (cost - tk) / b.limit.per_ms
                    if wait > retry_ms:
                        retry_ms, deny = wait, b
            allowed = deny is None
            rem, ref = None, buckets[0]
            for b, tk in zip(buckets, tokens):
                tk = tk - cost if allowed else tk
                self._state[b.key] = (tk, now)
                if rem is None or math.floor(tk) < rem:
                    rem, ref = math.floor(tk), b
            if len(self._state) > 100_000:  # bound memory under abuse
                for k in list(self._state)[:20_000]:
                    self._state.pop(k, None)
        ref = deny or ref
        return Decision(allowed, max(1, math.ceil(retry_ms / 1000)) if not allowed else 0, ref.limit.capacity,
                        max(0, int(rem or 0)), ref.scope)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


# ---------------------------------------------------------------------------------------------- policy

AUTH_PATHS = {"/api/v1/auth/login", "/api/v1/auth/register"}
EXEMPT_PATHS = {"/api/v1/health", "/api/v1/ready"}
AI_RE = re.compile(r"^/api/v1/(assessments/\d+/(chat|research)|system/integrations/[a-z_-]+/test)$")
PROCESSING_RE = re.compile(
    r"^/api/v1/(assessments/\d+/(documents|reconciliation|risk-assessment|recommendation|cam|start|ratios|financials/extract)"
    r"|documents/\d+/reprocess)$")


def _identity(request: Request) -> tuple[str | None, str | None]:
    """(user_id, org_id) from a valid session token, else (None, None). No database access."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.cookies.get(settings.COOKIE_NAME)
    if not token:
        return None, None
    try:
        from app.core.security import decode_access_token

        p = decode_access_token(token)
        return str(p.get("sub")), str(p.get("org"))
    except jwt.PyJWTError:
        return None, None


class RateLimiter:
    def __init__(self) -> None:
        self.local = LocalBackend()
        self.redis: RedisBackend | None = None
        self._breaker_until = 0.0
        self._last_warning = 0.0
        self.limits = self._load_limits()

    @staticmethod
    def _load_limits() -> dict[str, Limit]:
        s = settings
        return {k: parse_limit(v) for k, v in {
            "ip": s.RATE_LIMIT_IP or f"{s.RATE_LIMIT_PER_MINUTE}/minute",
            "auth": s.RATE_LIMIT_AUTH or f"{s.AUTH_RATE_LIMIT_PER_MINUTE}/minute",
            "user": s.RATE_LIMIT_USER, "org": s.RATE_LIMIT_ORG,
            "ai_user": s.RATE_LIMIT_AI_USER, "ai_org": s.RATE_LIMIT_AI_ORG,
            "processing_user": s.RATE_LIMIT_PROCESSING_USER, "processing_org": s.RATE_LIMIT_PROCESSING_ORG,
        }.items()}

    def configure(self) -> None:
        """Called at application startup (inside the serving event loop)."""
        self.limits = self._load_limits()
        if settings.RATE_LIMIT_BACKEND == "redis" and self.redis is None:
            self.redis = RedisBackend(settings.RATE_LIMIT_REDIS_URL or settings.REDIS_URL,
                                      timeout_s=settings.RATE_LIMIT_REDIS_TIMEOUT_MS / 1000)

    async def aclose(self) -> None:
        if self.redis is not None:
            await self.redis.close()
            self.redis = None

    def buckets_for(self, request: Request) -> list[Bucket] | None:
        path = request.url.path
        if not path.startswith("/api/") or path in EXEMPT_PATHS or request.method == "OPTIONS":
            return None
        p, L = settings.RATE_LIMIT_KEY_PREFIX, self.limits
        ip = client_ip(request) or "unknown"
        if path in AUTH_PATHS and request.method == "POST":
            return [Bucket(f"{p}:auth:ip:{ip}", L["auth"], "authentication attempts per IP")]
        uid, org = _identity(request)
        if uid is None:
            return [Bucket(f"{p}:ip:{ip}", L["ip"], "requests per IP")]
        buckets = [Bucket(f"{p}:user:{uid}", L["user"], "requests per user"),
                   Bucket(f"{p}:org:{org}", L["org"], "requests per organisation")]
        if request.method == "POST" and AI_RE.match(path):
            buckets += [Bucket(f"{p}:ai:user:{uid}", L["ai_user"], "AI requests per user"),
                        Bucket(f"{p}:ai:org:{org}", L["ai_org"], "AI requests per organisation")]
        elif request.method == "POST" and PROCESSING_RE.match(path):
            buckets += [Bucket(f"{p}:proc:user:{uid}", L["processing_user"], "processing requests per user"),
                        Bucket(f"{p}:proc:org:{org}", L["processing_org"], "processing requests per organisation")]
        return buckets

    async def check(self, buckets: list[Bucket]) -> Decision | None:
        if self.redis is not None and time.monotonic() >= self._breaker_until:
            try:
                return await self.redis.check(buckets)
            except Exception as exc:  # noqa: BLE001 - any Redis failure triggers the documented fallback
                self._breaker_until = time.monotonic() + settings.RATE_LIMIT_BREAKER_SECONDS
                if time.monotonic() - self._last_warning > 30:
                    self._last_warning = time.monotonic()
                    log.warning("rate-limit store unavailable (%s); fail mode=%s", type(exc).__name__,
                                settings.RATE_LIMIT_FAIL_MODE)
        elif self.redis is None and settings.RATE_LIMIT_BACKEND == "memory":
            return await self.local.check(buckets)
        mode = settings.RATE_LIMIT_FAIL_MODE
        if mode == "open":
            return None
        if mode == "closed":
            return Decision(False, settings.RATE_LIMIT_BREAKER_SECONDS, 0, 0, "rate-limit store unavailable", True)
        d = await self.local.check(buckets)
        d.degraded = True
        return d

    def reset(self) -> None:
        self.local.reset()
        self._breaker_until = 0.0


limiter = RateLimiter()


def _too_many(d: Decision) -> JSONResponse:
    if d.degraded and d.scope == "rate-limit store unavailable":
        return JSONResponse(status_code=503, headers={"retry-after": str(d.retry_after_s)}, content={"error": {
            "code": "rate_limit_unavailable", "message": "Request admission is temporarily unavailable. Please retry.",
            "details": {"retry_after_seconds": d.retry_after_s}}})
    return JSONResponse(
        status_code=429,
        headers={"retry-after": str(d.retry_after_s), "x-ratelimit-limit": str(d.limit), "x-ratelimit-remaining": "0"},
        content={"error": {"code": "rate_limited", "message": f"Too many requests ({d.scope}). Please retry later.",
                           "details": {"retry_after_seconds": d.retry_after_s, "limit": d.limit, "scope": d.scope}}},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)
        buckets = limiter.buckets_for(request)
        if not buckets:
            return await call_next(request)
        d = await limiter.check(buckets)
        if d is not None and not d.allowed:
            return _too_many(d)
        response = await call_next(request)
        if d is not None:
            response.headers["x-ratelimit-limit"] = str(d.limit)
            response.headers["x-ratelimit-remaining"] = str(d.remaining)
        return response
