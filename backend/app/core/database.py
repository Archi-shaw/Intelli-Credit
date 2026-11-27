"""SQLAlchemy engine, session factory, declarative base and portable column types."""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, MetaData, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# JSONB on PostgreSQL, plain JSON elsewhere (SQLite is used for fast unit tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class EmbeddingVector(TypeDecorator):
    """pgvector ``vector`` on PostgreSQL; JSON list fallback on other dialects.

    The column is declared without a fixed dimension so the embedding provider can be
    swapped; rows record the model that produced them and retrieval filters on it.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        values = [float(x) for x in value]
        if dialect.name == "postgresql":
            return values
        return values

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]


def _make_engine(url: str):
    # hide_parameters: SQL errors and logs never contain bound values (customer data, document text, hashes).
    kwargs: dict = {"pool_pre_ping": True, "future": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    else:
        kwargs.update(pool_size=10, max_overflow=20)
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _fk_pragma(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return eng


engine = _make_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


@event.listens_for(Session, "after_begin")
def _set_tenant_context(session: Session, transaction, connection) -> None:
    """Expose the caller's organisation to PostgreSQL row-level-security policies.

    ``set_config(..., true)`` is transaction-local, so the value can never leak to another request
    that later reuses the pooled connection. Sessions without an org see no tenant rows unless they
    are explicitly marked as trusted system sessions (background jobs, migrations, seeding).
    """
    if connection.dialect.name != "postgresql":
        return
    org_id = session.info.get("org_id")
    bypass = session.info.get("rls_bypass", False)
    connection.exec_driver_sql(
        "SELECT set_config('app.current_org_id', %(org)s, true), set_config('app.rls_bypass', %(bp)s, true)",
        {"org": str(org_id) if org_id else "", "bp": "on" if bypass else "off"},
    )


def apply_tenant(db: Session, org_id: int | None, *, bypass: bool = False) -> None:
    """Bind a session to an organisation, including the transaction that may already be open."""
    db.info["org_id"] = org_id
    db.info["rls_bypass"] = bypass
    if db.in_transaction() and db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.current_org_id', :org, true), set_config('app.rls_bypass', :bp, true)"),
                   {"org": str(org_id) if org_id else "", "bp": "on" if bypass else "off"})


def system_session() -> Session:
    db = SessionLocal()
    db.info["rls_bypass"] = True
    return db


# Key space for per-application advisory locks: pg_advisory_xact_lock(namespace, application_id).
_APPLICATION_LOCK_NAMESPACE = 0x1C0A


def lock_application(db: Session, application_id: int) -> None:
    """Serialise the writers of one application's derived data across threads, processes and API instances.

    Consolidated statements, persisted ratios, reconciliation run numbers and assessment versions are rebuilt
    from the application's committed inputs. Two transactions doing that at once (documents finishing in
    different workers, a request racing a job, a double submit) would each miss the other's uncommitted rows.
    This takes a PostgreSQL *transaction-scoped* advisory lock: a concurrent writer waits here until the holder
    commits or rolls back, and its following statements then see the holder's committed rows (READ COMMITTED).
    Call it before reading the data being rebuilt and before writing any row. Re-entrant within a transaction.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:ns, :app)"),
                   {"ns": _APPLICATION_LOCK_NAMESPACE, "app": int(application_id)})


def rebind_engine(url: str) -> None:
    """Point the global engine/session factory at another database (used by tests)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
