"""Password hashing (bcrypt), JWT session tokens and a small login throttle."""
from __future__ import annotations

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
    """In-memory failed-login limiter keyed by (email, ip). Good enough for a single API process."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = recent
        return recent

    def is_locked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.time())) >= self.max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()
