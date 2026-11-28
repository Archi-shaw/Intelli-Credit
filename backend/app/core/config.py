"""Environment-driven application settings.

Every secret and external integration is configured through environment variables
(or a local ``.env`` file). Missing credentials never prevent the app from starting;
the affected integration simply reports itself as "not configured".
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    APP_NAME: str = "Intelli-Credit"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    SECRET_KEY: str = Field(default="change-me-in-production-please-use-a-long-random-string")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8
    ALLOW_REGISTRATION: bool = False
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    COOKIE_SECURE: bool = False
    COOKIE_NAME: str = "ic_session"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"
    # --- Rate limiting (see app/core/ratelimit.py) ---
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_BACKEND: Literal["redis", "postgres", "memory"] = "redis"  # redis/postgres = shared across instances
    RATE_LIMIT_REDIS_URL: str = ""  # defaults to REDIS_URL
    RATE_LIMIT_KEY_PREFIX: str = "ic:rl"
    RATE_LIMIT_FAIL_MODE: Literal["local", "open", "closed"] = "local"  # behaviour while Redis is unreachable
    RATE_LIMIT_BREAKER_SECONDS: int = 5
    RATE_LIMIT_REDIS_TIMEOUT_MS: int = 300  # slower store replies count as an outage (fail mode applies)
    # Limits as "N/period" (period: second, minute, hour, day; e.g. "20/10minute").
    RATE_LIMIT_IP: str = ""  # unauthenticated requests per IP (default: RATE_LIMIT_PER_MINUTE/minute)
    RATE_LIMIT_AUTH: str = ""  # login/registration per IP (default: AUTH_RATE_LIMIT_PER_MINUTE/minute)
    RATE_LIMIT_USER: str = "600/minute"
    RATE_LIMIT_ORG: str = "3000/minute"
    RATE_LIMIT_AI_USER: str = "20/minute"
    RATE_LIMIT_AI_ORG: str = "100/minute"
    RATE_LIMIT_PROCESSING_USER: str = "60/minute"
    RATE_LIMIT_PROCESSING_ORG: str = "300/minute"
    RATE_LIMIT_PER_MINUTE: int = 300  # legacy default for RATE_LIMIT_IP
    # Reverse proxies whose X-Forwarded-For header is trusted (the Next.js frontend proxies /api).
    TRUSTED_PROXIES: str = "127.0.0.1/32,::1/128"
    # Header set (and overwritten) by the hosting platform with the client address, e.g. "x-real-ip" on Vercel.
    # When set, it replaces X-Forwarded-For parsing. Never set this behind a proxy that passes the header through.
    CLIENT_IP_HEADER: str = ""
    AUTH_RATE_LIMIT_PER_MINUTE: int = 20  # legacy default for RATE_LIMIT_AUTH

    # --- Database / cache ---
    DATABASE_URL: str = "postgresql+psycopg://intelli:intelli_dev_pw@localhost:5433/intellicredit"
    # Owner/superuser connection used only for migrations and role management. Defaults to DATABASE_URL.
    MIGRATION_DATABASE_URL: str = ""
    # Non-superuser role the API/worker should connect as so PostgreSQL row-level security applies.
    DB_APP_ROLE: str = "intellicredit_app"
    APP_DB_PASSWORD: str = ""  # used only by `python -m app.cli create-app-role`
    REDIS_URL: str = "redis://localhost:6380/0"
    # "queue" = SQLAlchemy connection pool (long-running servers); "null" = one connection per session, without
    # server-side prepared statements (serverless functions behind a transaction-mode pooler such as Neon's).
    DB_POOL: Literal["queue", "null"] = "queue"

    # --- Background jobs: "celery" (Redis worker), "thread" (in-process pool), "sync" (inline, tests),
    #     "request" (serverless: jobs stay queued in PostgreSQL until an authenticated request runs them) ---
    JOB_BACKEND: Literal["celery", "thread", "sync", "request"] = "thread"
    JOB_MAX_ATTEMPTS: int = 3
    JOB_THREAD_WORKERS: int = 2
    JOB_STALE_MINUTES: int = 30  # running jobs older than this are re-queued (and, with "request", re-claimable)

    # --- Storage ---
    # "local" = files under STORAGE_DIR; "database" = bytes in PostgreSQL (serverless hosts with no persistent disk).
    STORAGE_BACKEND: Literal["local", "database"] = "local"
    STORAGE_DIR: str = str(BACKEND_DIR / "storage")
    MAX_UPLOAD_MB: int = 25

    # --- LLM provider ---
    LLM_PROVIDER: Literal["anthropic", "none"] = "none"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "claude-opus-5-5"
    LLM_EFFORT: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    LLM_TIMEOUT_SECONDS: float = 90.0

    # --- Embeddings ---
    EMBEDDING_PROVIDER: Literal["local-hash", "voyage", "sentence-transformers"] = "local-hash"
    EMBEDDING_MODEL: str = "hash-384"
    EMBEDDING_API_KEY: str = ""

    # --- Research / search provider ---
    SEARCH_PROVIDER: Literal["none", "tavily", "brave", "serpapi"] = "none"
    SEARCH_API_KEY: str = ""
    SEARCH_MAX_RESULTS: int = 6
    # Per-provider keys (each can be connection-tested independently; SEARCH_API_KEY is used for the active
    # SEARCH_PROVIDER when its specific key is blank).
    TAVILY_API_KEY: str = ""
    BRAVE_API_KEY: str = ""
    SERPAPI_API_KEY: str = ""

    # --- OCR ---
    OCR_PROVIDER: Literal["auto", "tesseract", "rapidocr", "none"] = "auto"
    OCR_LANG: str = "eng"
    # Threads for OCR inference; 0 = derive from the container/function CPU quota. 1 on Vercel Hobby (1 vCPU).
    OCR_THREADS: int = 0

    # --- Databricks (optional authorised dataset connector) ---
    DATABRICKS_HOST: str = ""
    DATABRICKS_TOKEN: str = ""
    DATABRICKS_WAREHOUSE_ID: str = ""

    # --- ML challenger (experimental; never used for decisions while trained on synthetic labels) ---
    CHALLENGER_ENABLED: bool = True
    CHALLENGER_MODEL_DIR: str = str(BACKEND_DIR / "models" / "challenger")
    CHALLENGER_MODEL_VERSION: str = ""  # blank = the version named in <CHALLENGER_MODEL_DIR>/LATEST

    # --- Demo ---
    DEMO_MODE: bool = True
    SYNTHETIC_DATA_DIR: str = str(REPO_DIR / "data" / "synthetic")

    @field_validator("STORAGE_DIR", "SYNTHETIC_DATA_DIR", "CHALLENGER_MODEL_DIR", mode="before")
    @classmethod
    def _resolve_dir(cls, v, info):
        """Blank means the default location; relative paths are anchored at the repository root."""
        defaults = {"STORAGE_DIR": BACKEND_DIR / "storage", "SYNTHETIC_DATA_DIR": REPO_DIR / "data" / "synthetic",
                    "CHALLENGER_MODEL_DIR": BACKEND_DIR / "models" / "challenger"}
        if v is None or not str(v).strip():
            return str(defaults[info.field_name])
        p = Path(str(v).strip()).expanduser()
        return str(p if p.is_absolute() else (REPO_DIR / p).resolve())

    @field_validator("DATABASE_URL", "MIGRATION_DATABASE_URL")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Accept plain postgres URLs and route them through psycopg 3.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    def production_problems(self) -> list[str]:
        """Settings that must not be left at development defaults in production."""
        problems = []
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY.startswith("change-me") or len(self.SECRET_KEY) < 32:
                problems.append("SECRET_KEY must be set to a random string of at least 32 characters.")
            if not self.COOKIE_SECURE:
                problems.append("COOKIE_SECURE must be true in production (HTTPS only cookies).")
            if "intelli_dev_pw" in self.DATABASE_URL:
                problems.append("DATABASE_URL still uses the development password.")
        if os.environ.get("VERCEL"):  # set by the Vercel runtime: no persistent disk, no background threads
            if self.STORAGE_BACKEND != "database":
                problems.append("STORAGE_BACKEND must be 'database' on Vercel (the function filesystem is ephemeral).")
            if self.JOB_BACKEND != "request":
                problems.append("JOB_BACKEND must be 'request' on Vercel (work outside a request can be frozen or lost).")
            if self.RATE_LIMIT_BACKEND == "redis" and not (self.RATE_LIMIT_REDIS_URL or "localhost" not in self.REDIS_URL):
                problems.append("RATE_LIMIT_BACKEND must be 'postgres' on Vercel unless a Redis URL is configured.")
        return problems

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def migration_url(self) -> str:
        return self.MIGRATION_DATABASE_URL or self.DATABASE_URL

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
