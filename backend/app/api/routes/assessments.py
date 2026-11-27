"""Credit assessments (credit applications): CRUD, workflow status and final result."""
from __future__ import annotations

import secrets
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.database import get_db, utcnow
from app.core.deps import (
    CurrentUser,
    client_ip,
    get_application_for_org,
    get_company_for_org,
    require_analyst,
    require_any,
)
from app.core.errors import PreconditionFailed, ValidationFailed
from app.models import (
    CAMReport,
    CreditApplication,
    Membership,
    ProcessingJob,
    Reconciliation,
    ReviewDecision,
    UploadedDocument,
    User,
)
from app.models.application import APPLICATION_STATUSES
from app.schemas.requests import ApplicationIn, ApplicationUpdateIn
from app.services import audit, jobs
from app.services.analysis import latest_assessment, latest_reconciliations, latest_recommendation

router = APIRouter(prefix="/assessments", tags=["assessments"])

LOCKED = ("approved", "rejected")
WORKFLOW_STEPS = ["Select borrower", "Loan request", "Upload documents", "Process documents", "Review extraction",
                  "Run reconciliation", "Credit assessment", "Review recommendation", "Generate CAM"]


def _reference(db: Session) -> str:
    while True:
        ref = f"IC-{date.today().year}-{secrets.token_hex(3).upper()}"
        if not db.scalar(select(CreditApplication.id).where(CreditApplication.reference == ref)):
            return ref


def _workflow(db: Session, a: CreditApplication) -> dict:
    docs = db.scalars(select(UploadedDocument).where(UploadedDocument.application_id == a.id)).all()
    counts = {s: sum(1 for d in docs if d.processing_status == s) for s in ("queued", "processing", "completed", "failed")}
    recon_run = db.scalar(select(func.max(Reconciliation.run_number)).where(Reconciliation.application_id == a.id))
    asmt = latest_assessment(db, a.id)
    rec = latest_recommendation(db, a.id)
    cam = db.scalar(select(CAMReport).where(CAMReport.application_id == a.id).order_by(CAMReport.version.desc()))
    decision = db.scalar(select(ReviewDecision).where(ReviewDecision.application_id == a.id).order_by(ReviewDecision.id.desc()))
    done = [True, True, bool(docs), bool(docs) and not counts["queued"] and not counts["processing"],
            counts["completed"] > 0 and a.current_step >= 5, bool(recon_run), bool(asmt), bool(decision),
            bool(cam and cam.status == "completed")]
    return {"steps": [{"index": i + 1, "name": n, "done": d} for i, (n, d) in enumerate(zip(WORKFLOW_STEPS, done))],
            "documents": {"total": len(docs), **counts},
            "reconciliation_run": recon_run, "assessment_version": asmt.version if asmt else None,
            "recommendation": {"id": rec.id, "outcome": rec.outcome} if rec else None,
            "latest_cam": S.cam(cam) if cam else None,
            "review": {"decision": decision.decision, "at": decision.created_at.isoformat()} if decision else None}


@router.get("")
def list_assessments(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db),
                     status: str | None = None, company_id: int | None = None,
                     q: str | None = Query(default=None, max_length=100),
                     limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    from app.models import Company

    query = select(CreditApplication).join(Company).where(CreditApplication.organization_id == current.org_id)
    if status:
        statuses = [s for s in status.split(",") if s in APPLICATION_STATUSES]
        query = query.where(CreditApplication.status.in_(statuses))
    if company_id:
        query = query.where(CreditApplication.company_id == company_id)
    if q:
        query = query.where(Company.name.ilike(f"%{q}%") | CreditApplication.reference.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(CreditApplication.updated_at.desc()).limit(limit).offset(offset)).all()
    items = []
    for a in rows:
        asmt = latest_assessment(db, a.id)
        rec = latest_recommendation(db, a.id)
        items.append(S.application(a, extra={
            "risk_score": asmt.overall_score if asmt else None, "risk_category": asmt.risk_category if asmt else None,
            "confidence_level": asmt.confidence_level if asmt else None,
            "recommendation_outcome": rec.outcome if rec else None,
            "recommended_amount": rec.recommended_amount if rec else None}))
    return {"items": items, "total": total}


@router.post("", status_code=201)
def create_assessment(body: ApplicationIn, request: Request, current: CurrentUser = Depends(require_analyst),
                      db: Session = Depends(get_db)):
    company = get_company_for_org(db, body.company_id, current)
    a = CreditApplication(
        reference=_reference(db), organization_id=current.org_id, company_id=company.id, loan_type=body.loan_type,
        requested_amount=body.requested_amount, tenure_months=body.tenure_months, purpose=body.purpose,
        repayment_frequency=body.repayment_frequency, moratorium_months=body.moratorium_months,
        existing_borrowings=[b.model_dump(mode="json") for b in body.existing_borrowings],
        collateral=[c.model_dump(mode="json") for c in body.collateral],
        guarantors=[g.model_dump(mode="json") for g in body.guarantors],
        application_date=body.application_date or date.today(), status="documents_pending", current_step=3,
        created_by_id=current.id,
    )
    db.add(a)
    db.flush()
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="assessment.created",
                 summary=f"Created assessment {a.reference} for {company.name}: {body.loan_type} ₹{body.requested_amount:,.0f}",
                 entity_type="application", entity_id=a.id, application_id=a.id, ip_address=client_ip(request))
    db.commit()
    return S.application(a)


@router.get("/{application_id}")
def get_assessment(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    return S.application(a, extra={"workflow": _workflow(db, a)})


@router.patch("/{application_id}")
def update_assessment(application_id: int, body: ApplicationUpdateIn, request: Request,
                      current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    if a.status in LOCKED:
        raise PreconditionFailed("This assessment has a final review decision and can no longer be edited.")
    changes = body.model_dump(exclude_unset=True, mode="json")
    if "assigned_reviewer_id" in changes and changes["assigned_reviewer_id"] is not None:
        role = db.scalar(select(Membership.role).where(Membership.user_id == changes["assigned_reviewer_id"],
                                                       Membership.organization_id == current.org_id))
        if role not in ("reviewer", "admin"):
            raise ValidationFailed("The assigned reviewer must be a reviewer or administrator in this organisation.")
    for k, v in changes.items():
        setattr(a, k, v)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="assessment.updated",
                 summary=f"Updated {a.reference}: {', '.join(sorted(changes))}", entity_type="application",
                 entity_id=a.id, application_id=a.id, ip_address=client_ip(request))
    db.commit()
    return S.application(a)


@router.post("/{application_id}/start")
def start_processing(application_id: int, request: Request, current: CurrentUser = Depends(require_analyst),
                     db: Session = Depends(get_db)):
    """Ensure every uploaded document has a processing job (re-queues failed ones)."""
    a = get_application_for_org(db, application_id, current)
    docs = db.scalars(select(UploadedDocument).where(UploadedDocument.application_id == a.id)).all()
    if not docs:
        raise PreconditionFailed("Upload at least one document before starting processing.")
    queued = []
    from app.services.documents import reprocess

    for d in docs:
        active = db.scalar(select(ProcessingJob.id).where(ProcessingJob.document_id == d.id,
                                                          ProcessingJob.status.in_(("queued", "running", "retrying"))))
        if d.processing_status in ("failed",) or (d.processing_status == "queued" and not active):
            queued.append(reprocess(db, d, current.id))
    if a.status in ("draft", "documents_pending") or queued:
        a.status = "processing" if any(d.processing_status != "completed" for d in docs) else "under_analysis"
    a.current_step = max(a.current_step, 4)
    a.submitted_at = a.submitted_at or utcnow()
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="assessment.started",
                 summary=f"Started processing for {a.reference} ({len(queued)} job(s) queued)", entity_type="application",
                 entity_id=a.id, application_id=a.id, ip_address=client_ip(request))
    db.commit()
    for j in queued:
        jobs.dispatch(j.id)
    return {"queued_jobs": [j.id for j in queued], "status": a.status}


@router.get("/{application_id}/status")
def assessment_status(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    active_jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.application_id == a.id)
                             .order_by(ProcessingJob.id.desc()).limit(20)).all()
    return {"status": a.status, "current_step": a.current_step, "workflow": _workflow(db, a),
            "jobs": [S.job(j) for j in active_jobs]}


@router.get("/{application_id}/final")
def final_assessment(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    asmt = latest_assessment(db, a.id)
    rec = latest_recommendation(db, a.id)
    decisions = db.scalars(select(ReviewDecision).where(ReviewDecision.application_id == a.id).order_by(ReviewDecision.id)).all()
    names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({d.reviewer_id for d in decisions if d.reviewer_id}))).all()}
    recon = latest_reconciliations(db, a.id)
    return {"application": S.application(a), "assessment": S.assessment(asmt) if asmt else None,
            "recommendation": S.recommendation(rec) if rec else None,
            "reconciliation_summary": {"checks": len(recon),
                                       "attention": sum(1 for r in recon if r.status not in ("consistent", "insufficient_data"))},
            "review_decisions": [S.decision(d, names) for d in decisions],
            "final_status": a.status, "is_final": a.status in LOCKED}
