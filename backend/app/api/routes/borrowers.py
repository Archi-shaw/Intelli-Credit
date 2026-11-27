"""Borrower (company) management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_company_for_org, require_analyst, require_any
from app.core.errors import ConflictError
from app.models import Company, CreditApplication, Director, Promoter
from app.schemas.requests import CompanyIn, CompanyUpdateIn
from app.services import audit
from app.services.financials import statements_by_period

router = APIRouter(prefix="/borrowers", tags=["borrowers"])


def _check_unique(db: Session, org_id: int, body, exclude_id: int | None = None) -> None:
    for field in ("cin", "pan", "gstin"):
        v = getattr(body, field, None)
        if not v:
            continue
        q = select(Company.id).where(Company.organization_id == org_id, getattr(Company, field) == v)
        if exclude_id:
            q = q.where(Company.id != exclude_id)
        if db.scalar(q):
            raise ConflictError(f"A borrower with this {field.upper()} already exists in your organisation.",
                                code=f"duplicate_{field}")


@router.get("")
def list_borrowers(current: CurrentUser = Depends(require_any), db: Session = Depends(get_db),
                   q: str | None = Query(default=None, max_length=100), industry: str | None = Query(default=None, max_length=120),
                   synthetic: bool | None = None, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    query = select(Company).where(Company.organization_id == current.org_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Company.name.ilike(like), Company.pan.ilike(like), Company.gstin.ilike(like),
                                Company.cin.ilike(like), Company.industry.ilike(like)))
    if industry:
        query = query.where(Company.industry == industry)
    if synthetic is not None:
        query = query.where(Company.is_synthetic.is_(synthetic))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Company.updated_at.desc()).limit(limit).offset(offset)).all()
    stats = {}
    if rows:
        for cid, n, latest in db.execute(select(CreditApplication.company_id, func.count(CreditApplication.id),
                                                func.max(CreditApplication.updated_at))
                                         .where(CreditApplication.company_id.in_([c.id for c in rows]))
                                         .group_by(CreditApplication.company_id)).all():
            stats[cid] = {"assessment_count": n, "last_activity": latest.isoformat() if latest else None}
    industries = db.scalars(select(Company.industry).where(Company.organization_id == current.org_id).distinct()).all()
    return {"items": [S.company(c, stats=stats.get(c.id, {"assessment_count": 0, "last_activity": None})) for c in rows],
            "total": total, "industries": sorted(industries)}


@router.post("", status_code=201)
def create_borrower(body: CompanyIn, request: Request, current: CurrentUser = Depends(require_analyst),
                    db: Session = Depends(get_db)):
    _check_unique(db, current.org_id, body)
    data = body.model_dump(exclude={"directors", "promoters"})
    c = Company(organization_id=current.org_id, created_by_id=current.id, **data)
    c.directors = [Director(**d.model_dump()) for d in body.directors]
    c.promoters = [Promoter(**p.model_dump()) for p in body.promoters]
    db.add(c)
    db.flush()
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="borrower.created",
                 summary=f"Created borrower {c.name}", entity_type="company", entity_id=c.id, ip_address=client_ip(request))
    db.commit()
    return S.company_detail(c)


@router.get("/{company_id}")
def get_borrower(company_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    c = get_company_for_org(db, company_id, current)
    apps = db.scalars(select(CreditApplication).where(CreditApplication.company_id == c.id)
                      .order_by(CreditApplication.created_at.desc())).all()
    out = S.company_detail(c)
    out["assessments"] = [S.application(a) for a in apps]
    return out


@router.patch("/{company_id}")
def update_borrower(company_id: int, body: CompanyUpdateIn, request: Request, current: CurrentUser = Depends(require_analyst),
                    db: Session = Depends(get_db)):
    c = get_company_for_org(db, company_id, current)
    _check_unique(db, current.org_id, body, exclude_id=c.id)
    changes = body.model_dump(exclude_unset=True, exclude={"directors", "promoters"})
    for k, v in changes.items():
        setattr(c, k, v)
    if body.directors is not None:
        c.directors = [Director(**d.model_dump()) for d in body.directors]
    if body.promoters is not None:
        c.promoters = [Promoter(**p.model_dump()) for p in body.promoters]
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="borrower.updated",
                 summary=f"Updated borrower {c.name}: {', '.join(sorted(changes)) or 'related parties'}",
                 entity_type="company", entity_id=c.id, ip_address=client_ip(request))
    db.commit()
    return S.company_detail(c)


@router.get("/{company_id}/financials")
def borrower_financials(company_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    """Consolidated statements from this borrower's assessments (latest assessment per period wins)."""
    c = get_company_for_org(db, company_id, current)
    apps = db.scalars(select(CreditApplication).where(CreditApplication.company_id == c.id)
                      .order_by(CreditApplication.created_at)).all()
    periods: dict[str, dict] = {}
    for a in apps:
        for st in statements_by_period(db, a.id):
            periods[st.period_label] = {**S.statement(st), "application_id": a.id, "application_reference": a.reference}
    return {"periods": sorted(periods.values(), key=lambda p: p["period_end"] or "")}
