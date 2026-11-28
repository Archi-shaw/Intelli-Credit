"""Infrastructure tables that replace a persistent disk and Redis on serverless hosts (Vercel).

* ``stored_files``        document and CAM bytes when ``STORAGE_BACKEND=database``
* ``rate_limit_buckets``  token buckets when ``RATE_LIMIT_BACKEND=postgres``
* ``login_failures``      failed-login lockout when ``RATE_LIMIT_BACKEND=postgres``

They hold no tenant-visible records: files are only reachable through the organisation-scoped document and CAM
rows that reference their random paths (as on disk), and rate-limit keys are opaque.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, utcnow


class StoredFile(Base):
    __tablename__ = "stored_files"

    path: Mapped[str] = mapped_column(String(500), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class LoginFailure(Base):
    __tablename__ = "login_failures"
    __table_args__ = (Index("ix_login_failures_key_failed_at", "key", "failed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of (e-mail, IP)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
