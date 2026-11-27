"""External research and primary due-diligence observations; RAG document assistant."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_engine.nlp.observations import interpret_observation
from app.api import serializers as S
from app.core.database import get_db, utcnow
from app.core.deps import CurrentUser, client_ip, get_application_for_org, require_analyst, require_any, require_contributor
from app.core.errors import NotFoundError
from app.models import ChatMessage, ChatSession, PrimaryObservation, ResearchFinding, ResearchQuery
from app.schemas.requests import ChatIn, FindingReviewIn, ObservationIn, ObservationReviewIn
from app.services import assistant, audit, diligence
from app.services.analysis import active_policy

router = APIRouter(tags=["due-diligence"])


@router.post("/assessments/{application_id}/research")
def run_research(application_id: int, current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    out = diligence.run_research_for(db, a, current.id)
    db.commit()
    return out


@router.get("/assessments/{application_id}/research")
def get_research(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    queries = db.scalars(select(ResearchQuery).where(ResearchQuery.application_id == a.id)
                         .order_by(ResearchQuery.run_number.desc(), ResearchQuery.id)).all()
    latest_run = queries[0].run_number if queries else None
    findings = db.scalars(select(ResearchFinding).where(ResearchFinding.application_id == a.id)
                          .order_by(ResearchFinding.id.desc())).all()
    return {"latest_run": latest_run, "queries": [S.research_query(q) for q in queries if q.run_number == latest_run],
            "findings": [S.research_finding(f) for f in findings]}


@router.patch("/research-findings/{finding_id}")
def review_finding(finding_id: int, body: FindingReviewIn, request: Request, current: CurrentUser = Depends(require_contributor),
                   db: Session = Depends(get_db)):
    f = db.get(ResearchFinding, finding_id)
    if not f:
        raise NotFoundError("Finding not found.")
    a = get_application_for_org(db, f.application_id, current)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(f, k, v)
    f.reviewed_by_id = current.id
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="research.reviewed",
                 summary=f"Research finding reviewed: {f.title[:100]} → {f.verification_status}", entity_type="research_finding",
                 entity_id=f.id, application_id=a.id, ip_address=client_ip(request))
    db.commit()
    return S.research_finding(f)


@router.post("/assessments/{application_id}/observations/interpret")
def interpret(application_id: int, body: ObservationIn, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    """Preview how an observation would be interpreted, without saving it."""
    get_application_for_org(db, application_id, current)
    policy, _ = active_policy(db, current.org_id)
    it = interpret_observation(body.category, body.sub_category, body.title, body.notes, body.rating, body.numeric_value,
                               policy.get("thresholds"))
    return {"severity": body.severity_override or it.severity, "risk_impact": it.risk_impact,
            "interpretation": it.interpretation, "numeric_value": it.numeric_value, "signals": it.signals}


@router.post("/assessments/{application_id}/observations", status_code=201)
def add_observation(application_id: int, body: ObservationIn, current: CurrentUser = Depends(require_analyst),
                    db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    policy, _ = active_policy(db, current.org_id)
    o = diligence.add_observation(db, a, user_id=current.id, category=body.category, sub_category=body.sub_category,
                                  title=body.title, notes=body.notes, rating=body.rating,
                                  observed_on=body.observed_on or date.today(), numeric_value=body.numeric_value,
                                  severity_override=body.severity_override, thresholds=policy.get("thresholds"))
    db.commit()
    return S.observation(o)


@router.get("/assessments/{application_id}/observations")
def list_observations(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = db.scalars(select(PrimaryObservation).where(PrimaryObservation.application_id == a.id)
                      .order_by(PrimaryObservation.observed_on.desc())).all()
    return {"items": [S.observation(o) for o in rows]}


@router.patch("/observations/{observation_id}")
def review_observation(observation_id: int, body: ObservationReviewIn, request: Request,
                       current: CurrentUser = Depends(require_contributor), db: Session = Depends(get_db)):
    o = db.get(PrimaryObservation, observation_id)
    if not o:
        raise NotFoundError("Observation not found.")
    a = get_application_for_org(db, o.application_id, current)
    o.reviewer_status = body.reviewer_status
    o.reviewer_comment = body.reviewer_comment
    o.reviewed_by_id = current.id
    o.reviewed_at = utcnow()
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="observation.reviewed",
                 summary=f"Observation '{o.title[:80]}' marked {body.reviewer_status}", entity_type="observation",
                 entity_id=o.id, application_id=a.id, ip_address=client_ip(request))
    db.commit()
    return S.observation(o)


@router.post("/assessments/{application_id}/chat")
def chat(application_id: int, body: ChatIn, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    sess, q, ans = assistant.ask(db, a, current.id, body.question, body.session_id)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="assistant.question",
                 summary=f"Assistant question ({ans.method}, {'grounded' if ans.grounded else 'unverified'})",
                 entity_type="chat_session", entity_id=sess.id, application_id=a.id,
                 details={"citations": len(ans.citations)})
    db.commit()
    return {"session_id": sess.id, "question": S.chat_message(q), "answer": S.chat_message(ans)}


@router.get("/assessments/{application_id}/chat/sessions")
def chat_sessions(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = db.scalars(select(ChatSession).where(ChatSession.application_id == a.id, ChatSession.user_id == current.id)
                      .order_by(ChatSession.id.desc())).all()
    return {"items": [{"id": s.id, "title": s.title, "created_at": s.created_at.isoformat()} for s in rows]}


@router.get("/chat/sessions/{session_id}")
def chat_session(session_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    s = db.get(ChatSession, session_id)
    if not s or s.user_id != current.id:
        raise NotFoundError("Conversation not found.")
    get_application_for_org(db, s.application_id, current)
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.session_id == s.id).order_by(ChatMessage.id)).all()
    return {"id": s.id, "title": s.title, "application_id": s.application_id, "messages": [S.chat_message(m) for m in msgs]}
