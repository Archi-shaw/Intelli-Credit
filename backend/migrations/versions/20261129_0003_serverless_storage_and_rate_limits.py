"""Tables for serverless hosting: database-backed file storage, rate-limit buckets and login lockout.

Used only when STORAGE_BACKEND=database / RATE_LIMIT_BACKEND=postgres (e.g. on Vercel); harmless otherwise.
The application role receives access through the default privileges granted by `app.cli create-app-role`.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("path", sa.String(500), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_limit_buckets_expires_at", "rate_limit_buckets", ["expires_at"])
    op.create_table(
        "login_failures",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_failures_key_failed_at", "login_failures", ["key", "failed_at"])
    # Runnable-job lookups for the request-driven runner (oldest queued job of an application).
    op.create_index("ix_processing_jobs_application_id_status", "processing_jobs", ["application_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_application_id_status", table_name="processing_jobs")
    op.drop_index("ix_login_failures_key_failed_at", table_name="login_failures")
    op.drop_table("login_failures")
    op.drop_index("ix_rate_limit_buckets_expires_at", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    op.drop_table("stored_files")
