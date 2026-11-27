"""Alembic environment. Uses the owner connection (MIGRATION_DATABASE_URL, falling back to DATABASE_URL)."""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (register all tables)
from app.core.config import settings
from app.core.database import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=settings.migration_url, target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.migration_url, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
