"""Consolidated statements, ratios, bank transactions, GST records and reconciliation results."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, TimestampMixin


class FinancialStatement(Base, TimestampMixin):
    """One consolidated reporting period built from the extracted (and corrected) metrics."""

    __tablename__ = "financial_statements"
    __table_args__ = (Index("ix_statement_application_period", "application_id", "period_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    period_label: Mapped[str] = mapped_column(String(20), nullable=False)
    period_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="INR")
    line_items: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    source_document_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    conflicts: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)


class FinancialRatio(Base, TimestampMixin):
    __tablename__ = "financial_ratios"
    __table_args__ = (Index("ix_ratio_application_key", "application_id", "ratio_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[int | None] = mapped_column(ForeignKey("risk_assessments.id", ondelete="CASCADE"))
    period_label: Mapped[str | None] = mapped_column(String(20))
    ratio_key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    formula: Mapped[str] = mapped_column(String(255), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(10), nullable=False, default="x")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)


class BankTransaction(Base, TimestampMixin):
    __tablename__ = "bank_transactions"
    __table_args__ = (Index("ix_bank_txn_application_date", "application_id", "txn_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_documents.id", ondelete="CASCADE"))
    account_ref: Mapped[str | None] = mapped_column(String(60))
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    debit: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    credit: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    balance: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    counterparty: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    row_ref: Mapped[str | None] = mapped_column(String(40))
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flag_reason: Mapped[str | None] = mapped_column(String(255))


class GSTRecord(Base, TimestampMixin):
    __tablename__ = "gst_records"
    __table_args__ = (Index("ix_gst_application_period", "application_id", "return_type", "period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_documents.id", ondelete="CASCADE"))
    gstin: Mapped[str | None] = mapped_column(String(15))
    return_type: Mapped[str] = mapped_column(String(10), nullable=False)  # GSTR1, GSTR3B, GSTR2A, GSTR2B
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    taxable_value: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    igst: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    cgst: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    sgst: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    cess: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    total_tax: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    itc_claimed: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    itc_available: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    filing_date: Mapped[date | None] = mapped_column(Date)
    row_ref: Mapped[str | None] = mapped_column(String(40))


class Reconciliation(Base, TimestampMixin):
    __tablename__ = "reconciliations"
    __table_args__ = (Index("ix_recon_application_run", "application_id", "run_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    metric: Mapped[str] = mapped_column(String(160), nullable=False)
    period: Mapped[str | None] = mapped_column(String(40))
    source_a_label: Mapped[str | None] = mapped_column(String(160))
    source_a_value: Mapped[float | None] = mapped_column(Float)
    source_b_label: Mapped[str | None] = mapped_column(String(160))
    source_b_value: Mapped[float | None] = mapped_column(Float)
    difference: Mapped[float | None] = mapped_column(Float)
    difference_pct: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_implication: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    suggested_investigation: Mapped[str | None] = mapped_column(Text)
    source_document_ids: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    # Analyst workflow: open -> under_review -> explained | escalated. Never "fraud".
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
