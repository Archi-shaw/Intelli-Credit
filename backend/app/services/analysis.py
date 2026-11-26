"""Reconciliation, risk assessment and recommendation — orchestration and persistence.

The computations live in ``ai_engine`` (pure, deterministic, unit-tested). This module gathers
the persisted evidence for one application, runs the engines and stores versioned results.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_engine.decision_engine.engine import DecisionInputs, decide
from ai_engine.reconciliation.engine import ReconInputs, run_reconciliation
from ai_engine.risk_model.policy import get_default_policy, merge_policy
from ai_engine.risk_model.scorecard import AssessmentInputs, run_scorecard
from app.core.errors import PreconditionFailed
from app.models import (
    BankTransaction,
    CreditApplication,
    DocumentSignal,
    ExtractedFinancialMetric,
    GSTRecord,
    LoanRecommendation,
    PrimaryObservation,
    Reconciliation,
    ResearchFinding,
    ResearchQuery,
    RiskAssessment,
    RiskDimension,
    ScoringPolicy,
    UploadedDocument,
)
from app.services import audit
from app.services.financials import compute_application_ratios, latest_and_prior, statements_by_period


# ---------------------------------------------------------------- policy

def active_policy(db: Session, organization_id: int) -> tuple[dict, ScoringPolicy | None]:
    row = db.scalar(select(ScoringPolicy).where(ScoringPolicy.organization_id == organization_id,
                                                ScoringPolicy.is_active.is_(True)).order_by(ScoringPolicy.version.desc()))
    return (merge_policy(row.config) if row else get_default_policy()), row


# ---------------------------------------------------------------- reconciliation

def _sanction_metrics(db: Session, application_id: int) -> list[dict]:
    rows = db.execute(
        select(ExtractedFinancialMetric, UploadedDocument.category)
        .join(UploadedDocument, UploadedDocument.id == ExtractedFinancialMetric.document_id)
        .where(ExtractedFinancialMetric.application_id == application_id,
               UploadedDocument.category.in_(("sanction_letters", "credit_rating_reports")))
    ).all()
    return [{"metric_key": m.metric_key, "value": m.effective_value, "text_value": m.text_value,
             "document_id": m.document_id, "page_number": m.page_number, "metric_id": m.id} for m, _ in rows]


def build_recon_inputs(db: Session, app_: CreditApplication) -> ReconInputs:
    gst = db.scalars(select(GSTRecord).where(GSTRecord.application_id == app_.id)).all()
    txns = db.scalars(select(BankTransaction).where(BankTransaction.application_id == app_.id)
                      .order_by(BankTransaction.txn_date)).all()
    statements = {}
    for st in statements_by_period(db, app_.id):
        d = {k: {"value": v.get("value"), "document_id": v.get("document_id"), "page_number": v.get("page_number")}
             for k, v in (st.line_items or {}).items()}
        d["_period_end"] = st.period_end
        statements[st.period_label] = d
    return ReconInputs(
        gst_rows=[{"return_type": g.return_type, "period": g.period, "taxable_value": g.taxable_value,
                   "total_tax": g.total_tax, "itc_claimed": g.itc_claimed, "itc_available": g.itc_available,
                   "filing_date": g.filing_date, "gstin": g.gstin, "document_id": g.document_id, "row_ref": g.row_ref}
                  for g in gst],
        bank_txns=[{"txn_date": t.txn_date, "description": t.description, "debit": t.debit, "credit": t.credit,
                    "balance": t.balance, "counterparty": t.counterparty, "category": t.category,
                    "document_id": t.document_id, "row_ref": t.row_ref} for t in txns],
        statements=statements,
        declared_borrowings=list(app_.existing_borrowings or []),
        sanction_metrics=_sanction_metrics(db, app_.id),
        application_date=app_.application_date,
    )


def run_reconciliation_for(db: Session, app_: CreditApplication, user_id: int | None) -> list[Reconciliation]:
    if not db.scalar(select(func.count(UploadedDocument.id)).where(UploadedDocument.application_id == app_.id,
                                                                   UploadedDocument.processing_status == "completed")):
        raise PreconditionFailed("Upload and process at least one document before running reconciliation.")
    policy, _ = active_policy(db, app_.organization_id)
    items = run_reconciliation(build_recon_inputs(db, app_), policy.get("reconciliation_tolerances"))
    run = (db.scalar(select(func.max(Reconciliation.run_number)).where(Reconciliation.application_id == app_.id)) or 0) + 1
    rows = []
    for it in items:
        r = Reconciliation(application_id=app_.id, run_number=run, check_type=it.check_type, metric=it.metric[:160],
                           period=it.period, source_a_label=it.source_a_label, source_a_value=it.source_a_value,
                           source_b_label=it.source_b_label, source_b_value=it.source_b_value,
                           difference=it.difference, difference_pct=it.difference_pct, severity=it.severity,
                           status=it.status, description=it.description, risk_implication=it.risk_implication,
                           confidence=it.confidence, suggested_investigation=it.suggested_investigation,
                           source_document_ids=it.source_document_ids, details=_jsonable(it.details))
        db.add(r)
        rows.append(r)
    app_.current_step = max(app_.current_step, 6)
    db.flush()
    flagged = sum(1 for r in rows if r.status != "consistent" and r.status != "insufficient_data")
    audit.record(db, organization_id=app_.organization_id, user_id=user_id, action="reconciliation.run",
                 summary=f"Reconciliation run #{run}: {len(rows)} checks, {flagged} item(s) need attention",
                 entity_type="application", entity_id=app_.id, application_id=app_.id, details={"run": run})
    return rows


def latest_reconciliations(db: Session, application_id: int) -> list[Reconciliation]:
    run = db.scalar(select(func.max(Reconciliation.run_number)).where(Reconciliation.application_id == application_id))
    if not run:
        return []
    return db.scalars(select(Reconciliation).where(Reconciliation.application_id == application_id,
                                                   Reconciliation.run_number == run).order_by(Reconciliation.id)).all()


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, date):
        return v.isoformat()
    return v


# ---------------------------------------------------------------- assessment

def _application_dict(app_: CreditApplication) -> dict:
    return {"requested_amount": float(app_.requested_amount), "tenure_months": app_.tenure_months,
            "loan_type": app_.loan_type, "collateral": app_.collateral or [], "guarantors": app_.guarantors or [],
            "existing_borrowings": app_.existing_borrowings or [], "moratorium_months": app_.moratorium_months,
            "purpose": app_.purpose}


def _research_mode(db: Session, app_id: int, findings: list[ResearchFinding]) -> str:
    if any(f.is_synthetic for f in findings):
        return "synthetic"
    statuses = set(db.scalars(select(ResearchQuery.status).where(ResearchQuery.application_id == app_id)).all())
    if "completed" in statuses or "no_results" in statuses:
        return "live"
    if "not_configured" in statuses:
        return "not_configured"
    return "not_run"


def _shareholding(db: Session, app_: CreditApplication) -> dict:
    out: dict = {"evidence": []}
    metrics = db.scalars(select(ExtractedFinancialMetric).where(
        ExtractedFinancialMetric.application_id == app_.id,
        ExtractedFinancialMetric.metric_key.in_(("promoter_holding_pct", "promoter_pledge_pct")))).all()
    for m in sorted(metrics, key=lambda x: x.confidence):
        if m.effective_value is not None:
            out[m.metric_key] = m.effective_value
            out["evidence"].append({"type": "metric", "metric": m.metric_key, "document_id": m.document_id,
                                    "page_number": m.page_number, "metric_id": m.id})
    if "promoter_holding_pct" not in out:
        promo = [s for s in app_.company.shareholdings if s.holder_type == "promoter"]
        if promo:
            out["promoter_holding_pct"] = round(sum(s.percentage for s in promo), 2)
            pledged = [s for s in promo if s.pledged_pct is not None]
            if pledged:
                out["promoter_pledge_pct"] = round(max(s.pledged_pct for s in pledged), 2)
            out["evidence"].append({"type": "shareholding", "company_id": app_.company_id})
    return out


def run_assessment(db: Session, app_: CreditApplication, user_id: int | None) -> tuple[RiskAssessment, LoanRecommendation]:
    docs = db.scalars(select(UploadedDocument).where(UploadedDocument.application_id == app_.id)).all()
    if not any(d.processing_status == "completed" for d in docs):
        raise PreconditionFailed("No processed documents — upload and process the borrower's documents first.")
    if any(d.processing_status in ("queued", "processing") for d in docs):
        raise PreconditionFailed("Documents are still being processed. Wait for processing to finish.")
    policy, policy_row = active_policy(db, app_.organization_id)
    version = (db.scalar(select(func.max(RiskAssessment.version)).where(RiskAssessment.application_id == app_.id)) or 0) + 1

    latest, _prior, _ = latest_and_prior(db, app_.id)
    cur, pri, ratio_list, period = compute_application_ratios(db, app_.id, policy["thresholds"], persist=False)
    stmt_metric_ids = [v.get("metric_id") for v in (latest.line_items or {}).values()] if latest else []
    stmt_metrics = db.scalars(select(ExtractedFinancialMetric).where(ExtractedFinancialMetric.id.in_(
        [i for i in stmt_metric_ids if i]))).all() if stmt_metric_ids else []
    signals = db.scalars(select(DocumentSignal).where(DocumentSignal.application_id == app_.id,
                                                      DocumentSignal.is_dismissed.is_(False))).all()
    ratings = db.scalars(select(ExtractedFinancialMetric).where(ExtractedFinancialMetric.application_id == app_.id,
                                                                ExtractedFinancialMetric.metric_key == "credit_rating")).all()
    recon = latest_reconciliations(db, app_.id)
    findings = db.scalars(select(ResearchFinding).where(ResearchFinding.application_id == app_.id)).all()
    obs = db.scalars(select(PrimaryObservation).where(PrimaryObservation.application_id == app_.id)).all()
    company = app_.company
    recon_dicts = [{"id": r.id, "check_type": r.check_type, "metric": r.metric, "status": r.status, "severity": r.severity,
                    "description": r.description, "confidence": r.confidence, "difference": r.difference,
                    "difference_pct": r.difference_pct} for r in recon]
    finding_dicts = [{"id": f.id, "title": f.title, "category": f.category, "severity": f.severity, "sentiment": f.sentiment,
                      "source_name": f.source_name, "source_url": f.source_url, "summary": f.summary,
                      "verification_status": f.verification_status, "include_in_assessment": f.include_in_assessment}
                     for f in findings]
    obs_dicts = [{"id": o.id, "category": o.category, "sub_category": o.sub_category, "title": o.title,
                  "severity": o.severity, "risk_impact": o.risk_impact, "reviewer_status": o.reviewer_status,
                  "interpretation": o.interpretation, "notes": o.notes, "numeric_value": o.numeric_value} for o in obs]
    inp = AssessmentInputs(
        company={"name": company.name, "industry": company.industry, "business_type": company.business_type,
                 "incorporation_date": company.incorporation_date},
        application=_application_dict(app_), latest_period=period,
        latest_period_end=latest.period_end if latest else None, current=cur, prior=pri,
        ratios={r.key: r for r in ratio_list},
        documents=[{"category": d.category, "extraction_status": d.extraction_status} for d in docs],
        metric_confidences=[m.confidence for m in stmt_metrics],
        verified_metric_share=(sum(1 for m in stmt_metrics if m.verification_status == "verified") / len(stmt_metrics)) if stmt_metrics else 0.0,
        signals=[{"area": s.area, "severity": s.severity, "label": s.label, "excerpt": s.excerpt,
                  "document_id": s.document_id, "page_number": s.page_number} for s in signals],
        ratings=[{"text_value": r.text_value, "document_id": r.document_id, "page_number": r.page_number} for r in ratings],
        reconciliations=recon_dicts, research_mode=_research_mode(db, app_.id, findings),
        research_findings=finding_dicts, observations=obs_dicts, shareholding=_shareholding(db, app_),
        as_of=date.today(),
    )
    res = run_scorecard(inp, policy)
    a = RiskAssessment(
        application_id=app_.id, version=version, scoring_policy_id=policy_row.id if policy_row else None,
        overall_score=res.overall_score, risk_category=res.risk_category, confidence_score=res.confidence["score"],
        confidence_level=res.confidence["level"], data_completeness=res.data_completeness,
        document_coverage=_jsonable(res.document_coverage), missing_information=_jsonable(res.missing_information),
        red_flags=_jsonable(res.red_flags), five_cs=_jsonable(res.five_cs), risk_categories=_jsonable(res.risk_categories),
        confidence_breakdown=_jsonable(res.confidence), features=_jsonable(res.features),
        ratios_snapshot=_jsonable([r.to_dict() for r in ratio_list]), summary=res.summary, model_label=res.model_label,
        created_by_id=user_id,
    )
    for d in res.dimensions:
        a.dimensions.append(RiskDimension(position=d["position"], key=d["key"], name=d["name"], weight=d["weight"],
                                          score=d["score"], weighted_score=d["weighted_score"],
                                          data_status=d["data_status"], factors=_jsonable(d["factors"]),
                                          explanation=d["explanation"]))
    db.add(a)
    db.flush()
    compute_application_ratios(db, app_.id, policy["thresholds"], assessment_id=a.id, persist=True)

    dec = decide(DecisionInputs(
        overall_score=res.overall_score, risk_category=res.risk_category, confidence_score=res.confidence["score"],
        confidence_level=res.confidence["level"], red_flags=res.red_flags, factors=res.factors,
        missing_information=res.missing_information, current=cur, application=inp.application,
        reconciliations=recon_dicts, research_findings=finding_dicts, observations=obs_dicts, latest_period=period,
    ), policy)
    if dec.recommended_amount > dec.requested_amount:  # defensive: the engine already caps this
        raise RuntimeError("Recommended amount exceeds request")
    rec_version = (db.scalar(select(func.max(LoanRecommendation.version)).where(LoanRecommendation.application_id == app_.id)) or 0) + 1
    rec = LoanRecommendation(
        application_id=app_.id, assessment_id=a.id, version=rec_version, outcome=dec.outcome, risk_category=res.risk_category,
        requested_amount=dec.requested_amount, recommended_amount=dec.recommended_amount,
        amount_constraints=_jsonable(dec.amount_constraints), base_rate=dec.base_rate, risk_premium=dec.risk_premium,
        rate_adjustments=_jsonable(dec.rate_adjustments), indicative_rate=dec.indicative_rate,
        requested_tenure_months=dec.requested_tenure_months, recommended_tenure_months=dec.recommended_tenure_months,
        tenure_rationale=dec.tenure_rationale, repayment_capacity=_jsonable(dec.repayment_capacity),
        collateral_requirements=_jsonable(dec.collateral_requirements),
        repayment_considerations=_jsonable(dec.repayment_considerations),
        conditions_precedent=_jsonable(dec.conditions_precedent), positive_factors=_jsonable(dec.positive_factors),
        negative_factors=_jsonable(dec.negative_factors), critical_warnings=_jsonable(dec.critical_warnings),
        explanation=_jsonable(dec.explanation), narrative=None, narrative_method="deterministic",
        supporting_documents=[{"document_id": d.id, "name": d.original_filename, "category": d.category} for d in docs],
        research_references=[{"finding_id": f.id, "title": f.title, "source_url": f.source_url,
                              "synthetic": f.is_synthetic} for f in findings],
        created_by_id=user_id,
    )
    db.add(rec)
    app_.status = "awaiting_review"
    app_.current_step = max(app_.current_step, 8)
    db.flush()
    audit.record(db, organization_id=app_.organization_id, user_id=user_id, action="assessment.generated",
                 summary=f"Risk assessment v{version}: score {res.overall_score:.1f} ({res.risk_category}), "
                         f"confidence {res.confidence['level']}; recommendation: {dec.outcome_label}",
                 entity_type="risk_assessment", entity_id=a.id, application_id=app_.id,
                 details={"recommended_amount": dec.recommended_amount, "outcome": dec.outcome})
    audit.notify_role(db, organization_id=app_.organization_id, roles=("admin", "reviewer"),
                      title="Recommendation awaiting review",
                      message=f"{app_.reference} — {company.name}: {dec.outcome_label}",
                      link=f"/assessments/{app_.id}?tab=recommendation", exclude_user_id=user_id)
    return a, rec


def latest_assessment(db: Session, application_id: int) -> RiskAssessment | None:
    return db.scalar(select(RiskAssessment).where(RiskAssessment.application_id == application_id)
                     .order_by(RiskAssessment.version.desc()))


def latest_recommendation(db: Session, application_id: int) -> LoanRecommendation | None:
    return db.scalar(select(LoanRecommendation).where(LoanRecommendation.application_id == application_id)
                     .order_by(LoanRecommendation.version.desc()))
