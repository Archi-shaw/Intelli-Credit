"""Financial analysis: normalised metrics, statements, reconciliation, ratios, risk and recommendation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as S
from app.core.database import get_db
from app.core.deps import (
    CurrentUser,
    client_ip,
    get_application_for_org,
    get_reconciliation_for_org,
    require_analyst,
    require_any,
    require_contributor,
)
from app.core.errors import NotFoundError, PreconditionFailed
from app.models import (
    BankTransaction,
    ExtractedFinancialMetric,
    FinancialRatio,
    GSTRecord,
    ReviewComment,
    RiskAssessment,
    UploadedDocument,
)
from app.schemas.requests import ReconReviewIn
from app.services import analysis, audit
from app.services.financials import compute_application_ratios, consolidate_statements, statements_by_period, trend_series

router = APIRouter(prefix="/assessments/{application_id}", tags=["analysis"])
CLOSED = ("approved", "rejected")

TREND_KEYS = ["revenue_from_operations", "ebitda", "pat", "finance_costs", "operating_cash_flow", "total_debt",
              "shareholders_equity", "current_assets", "current_liabilities", "inventory", "trade_receivables",
              "trade_payables", "total_assets"]


def _open(a):
    if a.status in CLOSED:
        raise PreconditionFailed("This assessment has a final review decision; analysis can no longer be re-run.")


@router.get("/metrics")
def list_metrics(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = db.execute(select(ExtractedFinancialMetric, UploadedDocument.original_filename, UploadedDocument.category)
                      .join(UploadedDocument, UploadedDocument.id == ExtractedFinancialMetric.document_id)
                      .where(ExtractedFinancialMetric.application_id == a.id)
                      .order_by(ExtractedFinancialMetric.metric_key, ExtractedFinancialMetric.period_label)).all()
    return {"items": [{**S.metric(m), "document_name": name, "document_category": cat} for m, name, cat in rows]}


@router.post("/financials/extract")
def rebuild_financials(application_id: int, request: Request, current: CurrentUser = Depends(require_analyst),
                       db: Session = Depends(get_db)):
    """Re-consolidate statements from the extracted (and analyst-corrected) metrics."""
    a = get_application_for_org(db, application_id, current)
    _open(a)
    stmts = consolidate_statements(db, a.id)
    a.current_step = max(a.current_step, 5)
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="financials.consolidated",
                 summary=f"Consolidated {len(stmts)} financial period(s)", entity_type="application", entity_id=a.id,
                 application_id=a.id, ip_address=client_ip(request))
    db.commit()
    return {"periods": [S.statement(s) for s in stmts]}


@router.get("/financials")
def financials(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    stmts = statements_by_period(db, a.id)
    ratios = db.scalars(select(FinancialRatio).where(FinancialRatio.application_id == a.id)
                        .order_by(FinancialRatio.assessment_id.desc().nulls_last(), FinancialRatio.id)).all()
    if ratios:
        newest = ratios[0].assessment_id
        ratios = [r for r in ratios if r.assessment_id == newest]
    gst = db.scalars(select(GSTRecord).where(GSTRecord.application_id == a.id).order_by(GSTRecord.period)).all()
    txns = db.scalars(select(BankTransaction).where(BankTransaction.application_id == a.id)).all()
    monthly: dict[str, dict] = {}
    for t in txns:
        k = t.txn_date.strftime("%Y-%m")
        m = monthly.setdefault(k, {"month": k, "credits": 0.0, "debits": 0.0, "business_credits": 0.0, "count": 0})
        m["credits"] += t.credit or 0
        m["debits"] += t.debit or 0
        m["count"] += 1
        if t.credit and t.category == "business_receipt":
            m["business_credits"] += t.credit
    gst_monthly: dict[str, dict] = {}
    for g in gst:
        row = gst_monthly.setdefault(g.period, {"month": g.period})
        row[g.return_type] = g.taxable_value
    return {
        "periods": [S.statement(s) for s in stmts],
        "trend": trend_series(db, a.id, TREND_KEYS) if len(stmts) else [],
        "ratios": [S.ratio(r) for r in ratios],
        "bank_monthly": sorted(monthly.values(), key=lambda x: x["month"]),
        "gst_monthly": sorted(gst_monthly.values(), key=lambda x: x["month"]),
        "counts": {"bank_transactions": len(txns), "gst_records": len(gst), "periods": len(stmts)},
    }


@router.post("/reconciliation")
def run_reconciliation(application_id: int, current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    _open(a)
    rows = analysis.run_reconciliation_for(db, a, current.id)
    db.commit()
    names = {d.id: d.original_filename for d in a.documents}
    return {"items": [S.reconciliation(r, names) for r in rows]}


@router.get("/reconciliation")
def get_reconciliation(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rows = analysis.latest_reconciliations(db, a.id)
    names = {d.id: d.original_filename for d in a.documents}
    comments = db.scalars(select(ReviewComment).where(ReviewComment.application_id == a.id,
                                                      ReviewComment.entity_type == "reconciliation")).all()
    by_entity: dict[int, int] = {}
    for c in comments:
        by_entity[c.entity_id] = by_entity.get(c.entity_id, 0) + 1
    summary = {s: sum(1 for r in rows if r.status == s) for s in
               ("consistent", "potential_discrepancy", "unusual_pattern", "requires_verification", "insufficient_data")}
    return {"run_number": rows[0].run_number if rows else None, "summary": summary,
            "items": [{**S.reconciliation(r, names), "comment_count": by_entity.get(r.id, 0)} for r in rows]}


@router.patch("/reconciliation/{recon_id}")
def review_finding(application_id: int, recon_id: int, body: ReconReviewIn, request: Request,
                   current: CurrentUser = Depends(require_contributor), db: Session = Depends(get_db)):
    r = get_reconciliation_for_org(db, recon_id, current)
    if r.application_id != application_id:
        raise NotFoundError("Reconciliation finding not found.")
    old = r.review_status
    r.review_status = body.review_status
    r.reviewed_by_id = current.id
    if body.comment:
        db.add(ReviewComment(application_id=r.application_id, author_id=current.id, section="reconciliation",
                             entity_type="reconciliation", entity_id=r.id, body=body.comment))
    audit.record(db, organization_id=current.org_id, user_id=current.id, action="reconciliation.reviewed",
                 summary=f"Finding '{r.metric}': {old} → {body.review_status}", entity_type="reconciliation", entity_id=r.id,
                 application_id=r.application_id, ip_address=client_ip(request))
    db.commit()
    return S.reconciliation(r)


@router.post("/ratios")
def calc_ratios(application_id: int, current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    policy, _ = analysis.active_policy(db, current.org_id)
    _, _, ratios, period = compute_application_ratios(db, a.id, policy["thresholds"], persist=True)
    db.commit()
    return {"period": period, "items": [r.to_dict() for r in ratios]}


@router.get("/ratios")
def get_ratios(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    policy, _ = analysis.active_policy(db, current.org_id)
    _, _, ratios, period = compute_application_ratios(db, a.id, policy["thresholds"], persist=False)
    return {"period": period, "items": [r.to_dict() for r in ratios]}


@router.post("/risk-assessment", status_code=201)
def run_risk(application_id: int, current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    """Generate the risk score, explanations and the policy-driven recommendation (new versions)."""
    a = get_application_for_org(db, application_id, current)
    _open(a)
    asmt, rec = analysis.run_assessment(db, a, current.id)
    db.commit()
    db.refresh(asmt)
    return {"assessment": S.assessment(asmt), "recommendation": S.recommendation(rec)}


@router.get("/risk-assessment")
def get_risk(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db),
             version: int | None = None):
    a = get_application_for_org(db, application_id, current)
    if version:
        asmt = db.scalar(select(RiskAssessment).where(RiskAssessment.application_id == a.id, RiskAssessment.version == version))
    else:
        asmt = analysis.latest_assessment(db, a.id)
    versions = db.scalars(select(RiskAssessment.version).where(RiskAssessment.application_id == a.id)
                          .order_by(RiskAssessment.version)).all()
    return {"assessment": S.assessment(asmt) if asmt else None, "versions": versions}


@router.post("/recommendation", status_code=201)
def generate_recommendation(application_id: int, current: CurrentUser = Depends(require_analyst), db: Session = Depends(get_db)):
    """Recommendations are always derived from a fresh assessment so they never drift from the evidence."""
    return run_risk(application_id, current, db)


@router.get("/recommendation")
def get_recommendation(application_id: int, current: CurrentUser = Depends(require_any), db: Session = Depends(get_db)):
    a = get_application_for_org(db, application_id, current)
    rec = analysis.latest_recommendation(db, a.id)
    return {"recommendation": S.recommendation(rec) if rec else None}
