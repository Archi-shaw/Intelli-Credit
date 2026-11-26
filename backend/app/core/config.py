"""Environment-driven application settings.

Every secret and external integration is configured through environment variables
(or a local ``.env`` file). Missing credentials never prevent the app from starting;
the affected integration simply reports itself as "not configured".
"""
from __future__ import annotations

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
    RATE_LIMIT_PER_MINUTE: int = 240  # per client IP across the API
    AUTH_RATE_LIMIT_PER_MINUTE: int = 20  # login / registration attempts per IP

    # --- Database / cache ---
    DATABASE_URL: str = "postgresql+psycopg://intelli:intelli_dev_pw@localhost:5433/intellicredit"
    REDIS_URL: str = "redis://localhost:6380/0"

    # --- Background jobs: "celery" (Redis worker), "thread" (in-process pool), "sync" (inline, tests) ---
    JOB_BACKEND: Literal["celery", "thread", "sync"] = "thread"
    JOB_MAX_ATTEMPTS: int = 3
    JOB_THREAD_WORKERS: int = 2
    JOB_STALE_MINUTES: int = 30  # running jobs older than this are re-queued on startup

    # --- Storage ---
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

    # --- OCR ---
    OCR_PROVIDER: Literal["auto", "tesseract", "rapidocr", "none"] = "auto"
    OCR_LANG: str = "eng"

    # --- Databricks (optional authorised dataset connector) ---
    DATABRICKS_HOST: str = ""
    DATABRICKS_TOKEN: str = ""
    DATABRICKS_WAREHOUSE_ID: str = ""

    # --- Demo ---
    DEMO_MODE: bool = True
    SYNTHETIC_DATA_DIR: str = str(REPO_DIR / "data" / "synthetic")

    @field_validator("DATABASE_URL")
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
        return problems

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
