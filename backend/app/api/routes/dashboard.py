"""Dashboard metrics computed live from the database (organisation-scoped)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.database import get_db
from app.core.deps import CurrentUser, require_any
from app.models import Company, CreditApplication, LoanRecommendation, Reconciliation, UploadedDocument
from app.services.analysis import latest_assessment, latest_recommendation

router = APIRouter(tags=["dashboard"])

ACTIVE = ("draft", "documents_pending", "processing", "under_analysis", "awaiting_review")


@router.get("/dashboard")
def dashboard(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    org = current.org_id
    apps = select(CreditApplication.id).where(CreditApplication.organization_id == org)
    # Potential discrepancies counted on each application's latest reconciliation run only.
    latest_runs = (select(Reconciliation.application_id, func.max(Reconciliation.run_number).label("run"))
                   .where(Reconciliation.application_id.in_(apps)).group_by(Reconciliation.application_id).subquery())
    discrepancies = db.scalar(select(func.count(Reconciliation.id)).join(
        latest_runs, (Reconciliation.application_id == latest_runs.c.application_id) & (Reconciliation.run_number == latest_runs.c.run))
        .where(Reconciliation.status.in_(("potential_discrepancy", "unusual_pattern", "requires_verification")),
               Reconciliation.review_status != "explained"))
    status_counts = dict(db.execute(select(CreditApplication.status, func.count(CreditApplication.id))
                                    .where(CreditApplication.organization_id == org).group_by(CreditApplication.status)).all())
    recent = db.scalars(select(CreditApplication).where(CreditApplication.organization_id == org)
                        .order_by(CreditApplication.updated_at.desc()).limit(8)).all()
    recent_items = []
    for a in recent:
        asmt, rec = latest_assessment(db, a.id), latest_recommendation(db, a.id)
        recent_items.append(S.application(a, extra={"risk_score": asmt.overall_score if asmt else None,
                                                    "risk_category": asmt.risk_category if asmt else None,
                                                    "recommendation_outcome": rec.outcome if rec else None}))
    latest_rec = (select(LoanRecommendation.application_id, func.max(LoanRecommendation.version).label("v"))
                  .where(LoanRecommendation.application_id.in_(apps)).group_by(LoanRecommendation.application_id).subquery())
    outcomes = dict(db.execute(
        select(LoanRecommendation.outcome, func.count(LoanRecommendation.id))
        .join(latest_rec, (LoanRecommendation.application_id == latest_rec.c.application_id)
              & (LoanRecommendation.version == latest_rec.c.v))
        .group_by(LoanRecommendation.outcome)).all())
    return {
        "totals": {
            "borrowers": db.scalar(select(func.count(Company.id)).where(Company.organization_id == org)),
            "active_assessments": sum(v for k, v in status_counts.items() if k in ACTIVE),
            "documents_pending": db.scalar(select(func.count(UploadedDocument.id)).where(
                UploadedDocument.organization_id == org, UploadedDocument.processing_status.in_(("queued", "processing")))),
            "documents_failed": db.scalar(select(func.count(UploadedDocument.id)).where(
                UploadedDocument.organization_id == org, UploadedDocument.processing_status == "failed")),
            "completed_assessments": status_counts.get("approved", 0) + status_counts.get("rejected", 0),
            "potential_discrepancies": discrepancies or 0,
            "awaiting_review": status_counts.get("awaiting_review", 0),
        },
        "status_breakdown": status_counts,
        "recent_assessments": recent_items,
        "recommendation_outcomes": outcomes,
    }
