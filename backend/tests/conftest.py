"""Test configuration.

API tests run against a real PostgreSQL test database (created, migrated with Alembic and connected
through the non-superuser application role) so row-level security and triggers are exercised.

  TEST_DATABASE_ADMIN_URL  owner/superuser URL of the server (default: local dev container on :5433)
  TEST_DATABASE_NAME       database to (re)create for tests (default: intellicredit_test)

The pure AI-engine tests in test_ai_engine_core.py do not need a database.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql+psycopg://intelli:intelli_dev_pw@localhost:5433/intellicredit")
TEST_DB = os.environ.get("TEST_DATABASE_NAME", "intellicredit_test")
APP_ROLE = "intellicredit_test_app"
APP_PW = "test_" + secrets.token_hex(12)
_base = ADMIN_URL.rsplit("/", 1)[0]
_host = _base.split("@", 1)[1]
TMP = Path(tempfile.mkdtemp(prefix="ic-tests-"))

os.environ.update({
    "ENVIRONMENT": "test",
    "JOB_BACKEND": "sync",
    "MIGRATION_DATABASE_URL": f"{_base}/{TEST_DB}",
    "DATABASE_URL": f"postgresql+psycopg://{APP_ROLE}:{APP_PW}@{_host}/{TEST_DB}",
    "DB_APP_ROLE": APP_ROLE,
    "STORAGE_DIR": str(TMP / "storage"),
    "SYNTHETIC_DATA_DIR": str(TMP / "synthetic"),
    "ALLOW_REGISTRATION": "true",
    "LLM_PROVIDER": "none",
    "SEARCH_PROVIDER": "none",
    "EMBEDDING_PROVIDER": "local-hash",
    "SECRET_KEY": "test-secret-key-" + secrets.token_hex(16),
    "LOG_FORMAT": "text",
    "LOG_LEVEL": "WARNING",
    # The general suite runs without request limits; test_rate_limit.py enables them explicitly.
    "RATE_LIMIT_ENABLED": "false",
    "RATE_LIMIT_BACKEND": "memory",
})

_db_ready = False


def _setup_database() -> None:
    global _db_ready
    if _db_ready:
        return
    from sqlalchemy import create_engine, text

    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except Exception as exc:  # pragma: no cover - environment
        pytest.exit(f"PostgreSQL test server unavailable at {ADMIN_URL}: {exc}", returncode=2)
    finally:
        admin.dispose()
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(cfg, "head")
    from app.cli import create_app_role

    create_app_role(APP_PW)
    _db_ready = True


def _truncate() -> None:
    from sqlalchemy import create_engine, text

    eng = create_engine(os.environ["MIGRATION_DATABASE_URL"], future=True)
    with eng.begin() as c:
        tables = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")).scalars().all()
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
    eng.dispose()


@pytest.fixture
def db_clean():
    _setup_database()
    _truncate()
    from app.core.ratelimit import limiter
    from app.core.security import login_throttle

    limiter.reset()
    login_throttle._failures.clear()
    yield


@pytest.fixture
def client(db_clean):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


PASSWORD = "Str0ngPassw0rd!"


class Api:
    """Small helper around TestClient with bearer auth per user."""

    def __init__(self, client, token: str, data: dict):
        self.c, self.token, self.data = client, token, data

    def _h(self, extra=None):
        return {"authorization": f"Bearer {self.token}", **(extra or {})}

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(kw.pop("headers", None)), **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(kw.pop("headers", None)), **kw)


def register(client, email: str, org: str) -> Api:
    r = client.post("/api/v1/auth/register", json={"email": email, "full_name": "Test Admin", "password": PASSWORD,
                                                   "organization_name": org})
    assert r.status_code == 201, r.text
    client.cookies.clear()
    return Api(client, r.json()["access_token"], r.json())


def login(client, email: str, password: str = PASSWORD) -> Api:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return Api(client, r.json()["access_token"], r.json())


@pytest.fixture
def org_a(client):
    """Organisation A with admin, analyst and reviewer."""
    admin = register(client, "admin@alpha.example", "Alpha Bank")
    for email, role in (("analyst@alpha.example", "analyst"), ("reviewer@alpha.example", "reviewer")):
        r = admin.post("/api/v1/users", json={"email": email, "full_name": role.title(), "password": PASSWORD, "role": role})
        assert r.status_code == 201, r.text
    return {"admin": admin, "analyst": login(client, "analyst@alpha.example"), "reviewer": login(client, "reviewer@alpha.example")}


@pytest.fixture
def org_b(client):
    return {"admin": register(client, "admin@beta.example", "Beta Bank")}


@pytest.fixture(scope="session")
def synthetic_files():
    from app.seed.synthetic import generate

    return {k: {cat: p for p, cat in v["files"]} | {"case": v["case"]} for k, v in generate(TMP / "synthetic").items()}


BORROWER = {"name": "Test Fabrication Pvt Ltd", "industry": "Engineering", "business_type": "Private Limited",
            "pan": "AAACT1234Q", "gstin": "27AAACT1234Q1Z9", "incorporation_date": "2012-04-01"}
APPLICATION = {"loan_type": "term_loan", "requested_amount": 30000000, "tenure_months": 60,
               "purpose": "Capacity expansion through new machinery",
               "collateral": [{"type": "property", "description": "Factory", "value": 65000000}]}


def make_borrower(api: Api, **over) -> dict:
    r = api.post("/api/v1/borrowers", json={**BORROWER, **over})
    assert r.status_code == 201, r.text
    return r.json()


def make_assessment(api: Api, company_id: int, **over) -> dict:
    r = api.post("/api/v1/assessments", json={**APPLICATION, "company_id": company_id, **over})
    assert r.status_code == 201, r.text
    return r.json()


def upload(api: Api, app_id: int, path: Path, category: str = "auto"):
    with open(path, "rb") as fh:
        return api.post(f"/api/v1/assessments/{app_id}/documents", files={"file": (path.name, fh.read())},
                        data={"category": category})
