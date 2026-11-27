"""Distributed rate limiting: atomic Redis token buckets shared by multiple API instances.

Needs a Redis server (TEST_REDIS_URL, default: the local development Redis on :6380, logical DB 15).
The multi-instance tests start two real uvicorn processes that share Redis and the test database.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import redis

from app.core.ratelimit import Bucket, LocalBackend, RateLimiter, RedisBackend, parse_limit
from tests.conftest import PASSWORD

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")
BACKEND_DIR = str(Path(__file__).resolve().parents[1])


def _redis_available() -> bool:
    try:
        return redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1).ping()
    except redis.RedisError:
        return False


needs_redis = pytest.mark.skipif(not _redis_available(), reason=f"Redis not reachable at {REDIS_URL}")


def _prefix() -> str:
    return f"ic:rltest:{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------------------------- unit level

def test_parse_limit():
    assert parse_limit("300/minute").capacity == 300 and parse_limit("300/minute").period_s == 60
    assert parse_limit("20/10minute").period_s == 600
    assert parse_limit("5/second").per_ms == pytest.approx(0.005)
    for bad in ("", "x/minute", "0/minute", "10/fortnight"):
        with pytest.raises(ValueError):
            parse_limit(bad)


def test_local_backend_is_all_or_nothing():
    lb = LocalBackend()
    small, big = Bucket("k:small", parse_limit("2/minute"), "small"), Bucket("k:big", parse_limit("100/minute"), "big")
    assert lb.check_sync([small, big]).allowed and lb.check_sync([small, big]).allowed
    denied = lb.check_sync([small, big])
    assert not denied.allowed and denied.scope == "small" and denied.retry_after_s >= 1
    # The denied request must not have consumed from the other bucket: 98 tokens remain, not 97.
    assert lb.check_sync([big]).remaining == 97


@needs_redis
def test_redis_token_bucket_is_atomic_under_concurrency():
    async def run():
        rb = RedisBackend(REDIS_URL, timeout_s=5)  # tests atomicity, not latency
        b = Bucket(f"{_prefix()}:atomic", parse_limit("20/minute"), "test")
        try:
            results = await asyncio.gather(*[rb.check([b]) for _ in range(80)])
        finally:
            await rb.close()
        return results

    results = asyncio.run(run())
    assert sum(r.allowed for r in results) == 20  # never over-admits
    denied = [r for r in results if not r.allowed]
    assert len(denied) == 60 and all(r.retry_after_s >= 1 for r in denied)


@needs_redis
def test_redis_multi_bucket_all_or_nothing():
    async def run():
        rb = RedisBackend(REDIS_URL, timeout_s=5)  # tests atomicity, not latency
        p = _prefix()
        small, big = Bucket(f"{p}:s", parse_limit("1/minute"), "small"), Bucket(f"{p}:b", parse_limit("10/minute"), "big")
        try:
            first = await rb.check([small, big])
            second = await rb.check([small, big])
            only_big = await rb.check([big])
        finally:
            await rb.close()
        return first, second, only_big

    first, second, only_big = asyncio.run(run())
    assert first.allowed and not second.allowed and second.scope == "small"
    assert only_big.allowed and only_big.remaining == 8  # 10 - first - this one; the denied call took nothing


@pytest.mark.parametrize("mode,expect_allowed", [("open", True), ("closed", False), ("local", True)])
def test_redis_outage_fallback_modes(monkeypatch, mode, expect_allowed):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens here
    monkeypatch.setattr(settings, "RATE_LIMIT_FAIL_MODE", mode)

    async def run():
        lim = RateLimiter()
        lim.configure()
        b = [Bucket(f"{_prefix()}:outage", parse_limit("2/minute"), "test")]
        try:
            return [await lim.check(b) for _ in range(3)]
        finally:
            await lim.aclose()

    t = time.perf_counter()
    d1, d2, d3 = asyncio.run(run())
    assert time.perf_counter() - t < 3, "circuit breaker must avoid repeated connection timeouts"
    if mode == "open":
        assert d1 is None and d3 is None
    elif mode == "closed":
        assert not d1.allowed and d1.degraded
    else:  # local: the same limits keep being enforced per process
        assert d1.allowed and d2.allowed and not d3.allowed and d3.degraded


@needs_redis
def test_login_lockout_is_shared_between_instances(monkeypatch):
    from app.core.config import settings
    from app.core.security import LoginThrottle

    monkeypatch.setattr(settings, "RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setattr(settings, "RATE_LIMIT_REDIS_URL", REDIS_URL)
    monkeypatch.setattr(settings, "RATE_LIMIT_KEY_PREFIX", _prefix())
    instance_a, instance_b = LoginThrottle(max_failures=5), LoginThrottle(max_failures=5)
    key = f"victim{uuid.uuid4().hex[:6]}@example.com|203.0.113.5"
    for i in range(5):
        (instance_a if i % 2 else instance_b).record_failure(key)
    assert instance_a.is_locked(key) and instance_b.is_locked(key)
    assert not instance_a._failures, "state must live in Redis, not in process memory"
    raw = redis.Redis.from_url(REDIS_URL)
    assert not any(b"victim" in k for k in raw.keys(f"{settings.RATE_LIMIT_KEY_PREFIX}:lt:*")), "keys are hashed"
    instance_b.reset(key)
    assert not instance_a.is_locked(key)


# ---------------------------------------------------------------------------------------------- two real API instances

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def two_instances(db_clean):
    prefix = _prefix()
    env = {**os.environ, "RATE_LIMIT_ENABLED": "true", "RATE_LIMIT_BACKEND": "redis", "RATE_LIMIT_REDIS_URL": REDIS_URL,
           "RATE_LIMIT_KEY_PREFIX": prefix, "RATE_LIMIT_REDIS_TIMEOUT_MS": "3000", "RATE_LIMIT_USER": "10/minute", "RATE_LIMIT_ORG": "1000/minute",
           "RATE_LIMIT_IP": "8/minute", "RATE_LIMIT_AUTH": "4/minute", "RATE_LIMIT_AI_USER": "3/minute",
           "JOB_BACKEND": "sync", "ENVIRONMENT": "development", "LOG_LEVEL": "WARNING"}
    procs, urls = [], []
    for _ in range(2):
        port = _free_port()
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
                                       "--app-dir", BACKEND_DIR], env=env, cwd=BACKEND_DIR,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        urls.append(f"http://127.0.0.1:{port}")
    try:
        for url in urls:
            for _ in range(150):
                try:
                    if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.2)
            else:
                pytest.fail(f"API instance {url} did not start")
        yield urls, prefix
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)
        r = redis.Redis.from_url(REDIS_URL)
        for k in r.scan_iter(match=f"{prefix}*"):
            r.delete(k)


def _ip(n: int) -> dict:
    return {"x-forwarded-for": f"198.51.100.{n}"}  # the instances trust 127.0.0.1 as a proxy


@needs_redis
def test_limits_are_shared_across_two_api_instances(two_instances):
    (a, b), _ = two_instances
    # Registration goes through instance A (consumes 1 of the 4 auth attempts for this IP).
    r = httpx.post(f"{a}/api/v1/auth/register", headers=_ip(1), json={
        "email": "rl@instances.example", "full_name": "Rate Limit", "password": PASSWORD, "organization_name": "RL Org"})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]

    # Per-user limit (10/minute) enforced across both instances.
    codes = []
    for i in range(14):
        url = a if i % 2 == 0 else b
        codes.append(httpx.get(f"{url}/api/v1/auth/me", headers={"authorization": f"Bearer {token}", **_ip(2)}).status_code)
    assert codes.count(200) == 10 and codes.count(429) == 4, codes
    limited = httpx.get(f"{b}/api/v1/auth/me", headers={"authorization": f"Bearer {token}", **_ip(2)})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
    assert limited.json()["error"]["code"] == "rate_limited" and limited.json()["error"]["details"]["scope"] == "requests per user"

    # Unauthenticated per-IP limit (8/minute) shared across instances; a different IP is unaffected.
    anon = [httpx.get(f"{(a, b)[i % 2]}/api/v1/borrowers", headers=_ip(3)).status_code for i in range(11)]
    assert anon.count(401) == 8 and anon.count(429) == 3, anon
    assert httpx.get(f"{b}/api/v1/borrowers", headers=_ip(4)).status_code == 401

    # Authentication endpoints: stricter per-IP limit (4/minute) across instances.
    logins = [httpx.post(f"{(a, b)[i % 2]}/api/v1/auth/login", headers=_ip(5),
                         json={"email": "nobody@instances.example", "password": "wrong-password-1"}).status_code for i in range(6)]
    assert logins.count(401) == 4 and logins.count(429) == 2, logins

    # Expensive AI endpoints have their own tighter per-user bucket (3/minute) on top of the user limit.
    # (A fresh user, so the general per-user bucket exhausted above does not mask the AI-specific limit.)
    token2 = httpx.post(f"{b}/api/v1/auth/register", headers=_ip(6), json={
        "email": "ai@instances.example", "full_name": "AI User", "password": PASSWORD,
        "organization_name": "AI Org"}).json()["access_token"]
    ai = [httpx.post(f"{(a, b)[i % 2]}/api/v1/assessments/999999/chat", headers={"authorization": f"Bearer {token2}", **_ip(7)},
                     json={"question": "What was the revenue?"}).status_code for i in range(5)]
    assert ai.count(404) == 3 and ai.count(429) == 2, ai  # 404: the assessment does not exist in this org


@needs_redis
def test_concurrent_requests_across_instances_never_over_admit(two_instances):
    (a, b), _ = two_instances
    r = httpx.post(f"{a}/api/v1/auth/register", headers=_ip(20), json={
        "email": "burst@instances.example", "full_name": "Burst", "password": PASSWORD, "organization_name": "Burst Org"})
    token = r.json()["access_token"]
    headers = {"authorization": f"Bearer {token}", **_ip(21)}

    def hit(i):
        return httpx.get(f"{(a, b)[i % 2]}/api/v1/auth/me", headers=headers, timeout=10).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(hit, range(40)))
    assert codes.count(200) == 10 and codes.count(429) == 30, codes
