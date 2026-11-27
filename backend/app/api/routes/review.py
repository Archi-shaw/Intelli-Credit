"""Human review: analyst notes, approval/rejection decisions and the per-assessment audit trail.

Review decisions and audit entries are append-only: there are no update or delete endpoints, and
PostgreSQL triggers (see migrations) reject UPDATE/DELETE on those tables.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.database import get_db, utcnow
from app.core.deps import CurrentUser, client_ip, get_application_for_org, require_any, require_contributor, require_reviewer
from app.core.errors import ForbiddenError, PreconditionFailed, ValidationFailed
from app.models import AuditLog, ReviewComment, ReviewDecision, User
from app.schemas.requests import CommentIn, DecisionIn
from app.services import audit
from app.services.analysis import latest_recommendation

router = APIRouter(prefix="/assessments/{application_id}", tags=["review"])


def _names(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    return {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


@router.get("/comments")
def list_comments(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db),
                  entity_type: str | None = None, entity_id: int | None = None):
    a = get_application_for_org(db, application_id, current)
    q = select(ReviewComment).where(ReviewComment.application_id == a.id)
    if entity_type:
        q = q.where(ReviewComment.entity_type == entity_type)
    if entity_id:
        q = q.where(ReviewComment.entity_id == entity_id)
    rows = db.scalars(q.order_by(ReviewComment.id)).all()
    names = _names(db, [c.author_id for c in rows])
    return {"items": [S.comment(c, names) for c in rows]}


@router.post("/comments", status_code=201)
def add_comment(application_id: int, body: CommentIn, request: Request, current: CurrentUser = Depends(require_contributor),
                db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    if (body.entity_type is None) != (body.entity_id is None):
        raise ValidationFailed("entity_type and entity_id must be provided together.")
    c = ReviewComment(application_id=a.id, author_id=current.id, section=body.section, entity_type=body.entity_type,
                      entity_id=body.entity_id, body=body.body)
    db.add(c)
    db.flush()
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="comment.added",
                 summary=f"Note added ({body.section}{f', {body.entity_type} #{body.entity_id}' if body.entity_type else ''})",
                 entity_type="comment", entity_id=c.id, application_id=a.id, ip_address=client_ip(request))
    db.commit()
    return S.comment(c, {current.id: current.user.full_name})


@router.get("/decisions")
def list_decisions(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = db.scalars(select(ReviewDecision).where(ReviewDecision.application_id == a.id).order_by(ReviewDecision.id)).all()
    names = _names(db, [d.reviewer_id for d in rows])
    return {"items": [S.decision(d, names) for d in rows]}


@router.post("/decision", status_code=201)
def decide(application_id: int, body: DecisionIn, request: Request, current: CurrentUser = Depends(require_reviewer),
           db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    if a.status in ("approved", "rejected"):
        raise PreconditionFailed("A final decision has already been recorded for this assessment.")
    rec = latest_recommendation(db, a.id)
    if rec is None:
        raise PreconditionFailed("Generate a credit assessment and recommendation before recording a review decision.")
    if a.created_by_id == current.id:
        raise ForbiddenError("Segregation of duties: you cannot review an assessment you prepared.", code="self_review")
    final_amount = body.final_amount if body.final_amount is not None else (rec.recommended_amount if body.decision == "approved" else None)
    if body.decision == "approved":
        if not final_amount or final_amount <= 0:
            raise ValidationFailed("An approval requires a positive final amount.")
        if final_amount > float(a.requested_amount):
            raise ValidationFailed("The approved amount cannot exceed the requested amount.")
    overrides = (body.decision == "approved" and rec.outcome in ("reject", "insufficient_information")) or \
                (body.decision == "rejected" and rec.outcome in ("approve",)) or \
                (final_amount is not None and body.decision == "approved" and abs(final_amount - rec.recommended_amount) > 1)
    d = ReviewDecision(application_id=a.id, recommendation_id=rec.id, reviewer_id=current.id, decision=body.decision,
                       final_amount=final_amount if body.decision == "approved" else None,
                       final_rate=(body.final_rate if body.final_rate is not None else rec.indicative_rate) if body.decision == "approved" else None,
                       final_tenure_months=(body.final_tenure_months or rec.recommended_tenure_months) if body.decision == "approved" else None,
                       comments=body.comments, overrides_recommendation=bool(overrides))
    db.add(d)
    a.status = {"approved": "approved", "rejected": "rejected", "returned": "under_analysis"}[body.decision]
    if body.decision != "returned":
        a.decided_at = utcnow()
    db.flush()
    audit.record(db, organization_id=current.org_id, user_id=current.id, action=f"review.{body.decision}",
                 summary=f"{a.reference}: {body.decision}" + (" (overrides recommendation)" if overrides else ""),
                 entity_type="review_decision", entity_id=d.id, application_id=a.id, ip_address=client_ip(request),
                 details={"recommendation_outcome": rec.outcome, "final_amount": d.final_amount, "justification": body.comments[:500]})
    if a.created_by_id:
        audit.notify(db, organization_id=current.org_id, user_id=a.created_by_id, title=f"Assessment {body.decision}",
                     message=f"{a.reference} — {a.company.name} was {body.decision} by {current.user.full_name}.",
                     level="success" if body.decision == "approved" else "warning", link=f"/assessments/{a.id}?tab=review")
    db.commit()
    return S.decision(d, {current.id: current.user.full_name})


@router.get("/audit")
def assessment_audit(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = db.scalars(select(AuditLog).where(AuditLog.organization_id == current.org_id, AuditLog.application_id == a.id)
                      .order_by(AuditLog.id.desc()).limit(500)).all()
    names = _names(db, [r.user_id for r in rows])
    return {"items": [S.audit_log(r, names) for r in rows]}
