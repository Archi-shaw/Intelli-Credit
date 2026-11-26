"""Scoring policies, model versions, risk assessments, dimensions, recommendations and reviews."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, TimestampMixin


class ScoringPolicy(Base, TimestampMixin):
    __tablename__ = "scoring_policies"
    __table_args__ = (UniqueConstraint("organization_id", "version", name="uq_policy_org_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False)
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ModelVersion(Base, TimestampMixin):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version", name="uq_model_org_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_type: Mapped[str] = mapped_column(String(40), nullable=False)  # scorecard, logistic_regression, ...
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # active, trained, retired
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_trained: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_dataset: Mapped[str | None] = mapped_column(String(500))
    training_rows: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    metrics: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    feature_importance: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)


class RiskAssessment(Base, TimestampMixin):
    __tablename__ = "risk_assessments"
    __table_args__ = (UniqueConstraint("application_id", "version", name="uq_assessment_application_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_policy_id: Mapped[int | None] = mapped_column(ForeignKey("scoring_policies.id", ondelete="SET NULL"))
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id", ondelete="SET NULL"))
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_category: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(20), nullable=False)
    data_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    document_coverage: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    missing_information: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    red_flags: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    five_cs: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    risk_categories: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    confidence_breakdown: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    features: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    ratios_snapshot: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    challenger_output: Mapped[dict | None] = mapped_column(JSONType)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_label: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    dimensions: Mapped[list["RiskDimension"]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="RiskDimension.position"
    )


class RiskDimension(Base, TimestampMixin):
    __tablename__ = "risk_dimensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False)
    data_status: Mapped[str] = mapped_column(String(20), nullable=False)
    factors: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    assessment: Mapped[RiskAssessment] = relationship(back_populates="dimensions")


class LoanRecommendation(Base, TimestampMixin):
    __tablename__ = "loan_recommendations"
    __table_args__ = (UniqueConstraint("application_id", "version", name="uq_recommendation_application_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assessment_id: Mapped[int] = mapped_column(ForeignKey("risk_assessments.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    risk_category: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_amount: Mapped[float] = mapped_column(Numeric(20, 2, asdecimal=False), nullable=False)
    recommended_amount: Mapped[float] = mapped_column(Numeric(20, 2, asdecimal=False), nullable=False)
    amount_constraints: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    base_rate: Mapped[float | None] = mapped_column(Float)
    risk_premium: Mapped[float | None] = mapped_column(Float)
    rate_adjustments: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    indicative_rate: Mapped[float | None] = mapped_column(Float)
    requested_tenure_months: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_tenure_months: Mapped[int | None] = mapped_column(Integer)
    tenure_rationale: Mapped[str | None] = mapped_column(Text)
    repayment_capacity: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    collateral_requirements: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    repayment_considerations: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    conditions_precedent: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    positive_factors: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    negative_factors: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    critical_warnings: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    explanation: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    narrative: Mapped[str | None] = mapped_column(Text)
    narrative_method: Mapped[str] = mapped_column(String(20), nullable=False, default="deterministic")
    supporting_documents: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    research_references: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ReviewDecision(Base, TimestampMixin):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("credit_applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("loan_recommendations.id", ondelete="SET NULL"))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[str] = mapped_column(String(30), nullable=False)  # approved, rejected, returned
    final_amount: Mapped[float | None] = mapped_column(Numeric(20, 2, asdecimal=False))
    final_rate: Mapped[float | None] = mapped_column(Float)
    final_tenure_months: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[str] = mapped_column(Text, nullable=False)
    overrides_recommendation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
