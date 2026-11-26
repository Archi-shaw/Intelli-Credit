"""Research queries, research findings and primary due-diligence observations."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, TimestampMixin

RESEARCH_CATEGORIES = (
    "corporate_news",
    "promoter_intelligence",
    "legal_litigation",
    "regulatory",
    "industry_outlook",
    "financial_distress",
    "credit_rating",
)

OBSERVATION_CATEGORIES = ("factory_inspection", "management_assessment", "business_environment")


class ResearchQuery(Base, TimestampMixin):
    __tablename__ = "research_queries"
    __table_args__ = (Index("ix_research_query_application", "application_id", "run_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    query_text: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # completed, no_results, failed, not_configured
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ResearchFinding(Base, TimestampMixin):
    __tablename__ = "research_findings"
    __table_args__ = (Index("ix_research_finding_application", "application_id", "category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    query_id: Mapped[int | None] = mapped_column(ForeignKey("research_queries.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="general")
    published_at: Mapped[date | None] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    sentiment: Mapped[str] = mapped_column(String(20), nullable=False, default="neutral")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    summary_method: Mapped[str] = mapped_column(String(20), nullable=False, default="extractive")
    evidence_excerpt: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    recency_score: Mapped[float | None] = mapped_column(Float)
    matched_keywords: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    include_in_assessment: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PrimaryObservation(Base, TimestampMixin):
    __tablename__ = "primary_observations"
    __table_args__ = (Index("ix_observation_application", "application_id", "category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    sub_category: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1 (poor) .. 5 (excellent)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    numeric_unit: Mapped[str | None] = mapped_column(String(20))
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    severity_source: Mapped[str] = mapped_column(String(20), nullable=False, default="system")
    risk_impact: Mapped[str] = mapped_column(String(20), nullable=False, default="neutral")
    interpretation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    signals: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    reviewer_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_comment: Mapped[str | None] = mapped_column(Text)
    attachment_path: Mapped[str | None] = mapped_column(String(500))
    attachment_name: Mapped[str | None] = mapped_column(String(255))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
