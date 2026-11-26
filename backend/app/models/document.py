"""Uploaded documents, page-level extractions, RAG chunks and extracted financial metrics."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, EmbeddingVector, JSONType, TimestampMixin

DOCUMENT_CATEGORIES = (
    "gst_returns",
    "income_tax_returns",
    "bank_statements",
    "financial_statements",
    "annual_reports",
    "board_minutes",
    "credit_rating_reports",
    "sanction_letters",
    "shareholding_disclosures",
    "legal_notices",
    "other",
)


class UploadedDocument(Base, TimestampMixin):
    __tablename__ = "uploaded_documents"
    __table_args__ = (UniqueConstraint("application_id", "sha256", name="uq_document_application_sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    suggested_category: Mapped[str | None] = mapped_column(String(40))
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120))
    file_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf, docx, xlsx, csv, image
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_scanned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    extraction_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    processing_progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    detected_unit: Mapped[str | None] = mapped_column(String(20))
    detected_currency: Mapped[str | None] = mapped_column(String(10))
    detected_periods: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    application: Mapped["CreditApplication"] = relationship(back_populates="documents")  # noqa: F821
    extractions: Mapped[list["DocumentExtraction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentExtraction.page_number"
    )


class DocumentExtraction(Base, TimestampMixin):
    """Raw text/tables recovered from one page (PDF/image), sheet (XLSX) or section (DOCX/CSV)."""

    __tablename__ = "document_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_label: Mapped[str | None] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # text, ocr, spreadsheet, docx, csv
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tables: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped[UploadedDocument] = relationship(back_populates="extractions")


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_chunk_application_model", "application_id", "embedding_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(EmbeddingVector(), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(80), nullable=False)


class ExtractedFinancialMetric(Base, TimestampMixin):
    __tablename__ = "extracted_financial_metrics"
    __table_args__ = (
        Index("ix_metric_application_key", "application_id", "metric_key"),
        Index("ix_metric_document", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_documents.id", ondelete="CASCADE"))
    metric_key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    period_label: Mapped[str | None] = mapped_column(String(20))
    period_end: Mapped[date | None] = mapped_column(Date)
    value: Mapped[float | None] = mapped_column(Numeric(24, 2, asdecimal=False))
    text_value: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_multiplier: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(10))
    page_number: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    extraction_method: Mapped[str] = mapped_column(String(30), nullable=False)
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    corrected_value: Mapped[float | None] = mapped_column(Numeric(24, 2, asdecimal=False))
    correction_note: Mapped[str | None] = mapped_column(Text)
    corrected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def effective_value(self) -> float | None:
        if self.verification_status == "rejected":
            return None
        if self.corrected_value is not None:
            return float(self.corrected_value)
        return float(self.value) if self.value is not None else None


class DocumentSignal(Base, TimestampMixin):
    """Qualitative risk signal detected in document text (audit qualification, NCLT petition, ...)."""

    __tablename__ = "document_signals"
    __table_args__ = (Index("ix_signal_application", "application_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int] = mapped_column(ForeignKey("uploaded_documents.id", ondelete="CASCADE"), nullable=False)
    signal_key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    area: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Numeric(24, 2, asdecimal=False))
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
