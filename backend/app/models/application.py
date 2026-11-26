"""Credit applications, comments and processing jobs."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, TimestampMixin

APPLICATION_STATUSES = (
    "draft",
    "documents_pending",
    "processing",
    "under_analysis",
    "awaiting_review",
    "recommended_approval",
    "recommended_rejection",
    "approved",
    "rejected",
)

LOAN_TYPES = (
    "term_loan",
    "working_capital",
    "cash_credit",
    "overdraft",
    "equipment_finance",
    "project_finance",
    "letter_of_credit",
    "bank_guarantee",
)


class CreditApplication(Base, TimestampMixin):
    __tablename__ = "credit_applications"
    __table_args__ = (Index("ix_application_org_status", "organization_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    loan_type: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_amount: Mapped[float] = mapped_column(Numeric(20, 2, asdecimal=False), nullable=False)
    tenure_months: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    repayment_period_months: Mapped[int | None] = mapped_column(Integer)
    repayment_frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    moratorium_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    existing_borrowings: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    collateral: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    guarantors: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    application_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    company: Mapped["Company"] = relationship(back_populates="applications")  # noqa: F821
    documents: Mapped[list["UploadedDocument"]] = relationship(  # noqa: F821
        back_populates="application", cascade="all, delete-orphan"
    )


class ReviewComment(Base, TimestampMixin):
    __tablename__ = "review_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    section: Mapped[str] = mapped_column(String(40), nullable=False, default="general")
    # Optional link to the record being discussed (e.g. a reconciliation finding or extracted metric).
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_job_application_type", "application_id", "job_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("credit_applications.id", ondelete="CASCADE"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_documents.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
