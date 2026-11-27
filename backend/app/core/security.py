"""Password hashing (bcrypt), JWT session tokens and a small login throttle."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; reject longer inputs explicitly at the schema level.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, organization_id: int, role: str) -> tuple[str, str, datetime]:
    jti = secrets.token_hex(16)
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "org": organization_id,
        "role": role,
        "jti": jti,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "iss": "intelli-credit",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM), jti, expires


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM], issuer="intelli-credit")


class LoginThrottle:
    """Failed-login limiter keyed by (email, ip).

    Uses Redis (shared by every API instance) when ``RATE_LIMIT_BACKEND=redis``; keys are SHA-256 hashed so no
    e-mail addresses are stored in Redis. Falls back to in-process tracking if Redis is unreachable.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._redis = None
        self._redis_down_until = 0.0

    # -- shared store ------------------------------------------------------------------------------
    def _client(self):
        if settings.RATE_LIMIT_BACKEND != "redis" or time.time() < self._redis_down_until:
            return None
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(settings.RATE_LIMIT_REDIS_URL or settings.REDIS_URL,
                                               socket_timeout=settings.RATE_LIMIT_REDIS_TIMEOUT_MS / 1000,
                                               socket_connect_timeout=settings.RATE_LIMIT_REDIS_TIMEOUT_MS / 1000)
        return self._redis

    def _rkey(self, key: str) -> str:
        return f"{settings.RATE_LIMIT_KEY_PREFIX}:lt:{hashlib.sha256(key.encode()).hexdigest()}"

    def _shared(self, op):
        client = self._client()
        if client is None:
            return None
        try:
            return op(client)
        except Exception:  # noqa: BLE001 - fall back to local tracking during a Redis outage
            self._redis_down_until = time.time() + settings.RATE_LIMIT_BREAKER_SECONDS
            return None

    # -- local fallback ----------------------------------------------------------------------------
    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = recent
        return recent

    def is_locked(self, key: str) -> bool:
        now = time.time()

        def op(r):
            k = self._rkey(key)
            pipe = r.pipeline(transaction=True)
            pipe.zremrangebyscore(k, "-inf", now - self.window)
            pipe.zcard(k)
            return pipe.execute()[1]

        count = self._shared(op)
        if count is not None:
            return int(count) >= self.max_failures
        with self._lock:
            return len(self._prune(key, now)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        now = time.time()

        def op(r):
            k = self._rkey(key)
            pipe = r.pipeline(transaction=True)
            pipe.zadd(k, {f"{now:.6f}:{secrets.token_hex(4)}": now})
            pipe.expire(k, self.window)
            pipe.execute()
            return True

        if self._shared(op) is None:
            with self._lock:
                self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        self._shared(lambda r: r.delete(self._rkey(key)))
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()
