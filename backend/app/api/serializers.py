"""Model → JSON shaping for API responses. Storage paths, hashes of passwords and tokens are never exposed."""
from __future__ import annotations

from datetime import date, datetime

from ai_engine.decision_engine.engine import OUTCOME_LABELS
from app.models import (
    AuditLog,
    BankTransaction,
    CAMReport,
    ChatMessage,
    Company,
    CreditApplication,
    DocumentSignal,
    ExtractedFinancialMetric,
    FinancialRatio,
    FinancialStatement,
    GSTRecord,
    LoanRecommendation,
    Notification,
    PrimaryObservation,
    ProcessingJob,
    Reconciliation,
    ResearchFinding,
    ResearchQuery,
    ReviewComment,
    ReviewDecision,
    RiskAssessment,
    UploadedDocument,
    User,
)


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def user(u: User, role: str | None = None) -> dict:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "title": u.title, "is_active": u.is_active,
            "role": role, "last_login_at": _iso(u.last_login_at), "created_at": _iso(u.created_at)}


def company(c: Company, *, stats: dict | None = None) -> dict:
    out = {"id": c.id, "name": c.name, "cin": c.cin, "pan": c.pan, "gstin": c.gstin, "industry": c.industry,
           "business_type": c.business_type, "incorporation_date": _iso(c.incorporation_date),
           "registered_address": c.registered_address, "headquarters": c.headquarters,
           "annual_turnover": c.annual_turnover, "website": c.website, "contact_person": c.contact_person,
           "contact_email": c.contact_email, "contact_phone": c.contact_phone,
           "business_description": c.business_description, "subsidiaries": c.subsidiaries,
           "is_synthetic": c.is_synthetic, "demo_scenario": c.demo_scenario,
           "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at)}
    if stats is not None:
        out.update(stats)
    return out


def company_detail(c: Company) -> dict:
    d = company(c)
    d["directors"] = [{"id": x.id, "name": x.name, "din": x.din, "designation": x.designation,
                       "appointment_date": _iso(x.appointment_date), "is_independent": x.is_independent} for x in c.directors]
    d["promoters"] = [{"id": x.id, "name": x.name, "pan": x.pan, "shareholding_pct": x.shareholding_pct,
                       "experience_years": x.experience_years, "background": x.background} for x in c.promoters]
    d["shareholdings"] = [{"id": x.id, "holder_name": x.holder_name, "holder_type": x.holder_type,
                           "percentage": x.percentage, "pledged_pct": x.pledged_pct} for x in c.shareholdings]
    return d


def application(a: CreditApplication, *, extra: dict | None = None) -> dict:
    out = {"id": a.id, "reference": a.reference, "company_id": a.company_id, "company_name": a.company.name,
           "company_is_synthetic": a.company.is_synthetic, "loan_type": a.loan_type,
           "requested_amount": a.requested_amount, "tenure_months": a.tenure_months, "purpose": a.purpose,
           "repayment_frequency": a.repayment_frequency, "moratorium_months": a.moratorium_months,
           "existing_borrowings": a.existing_borrowings, "collateral": a.collateral, "guarantors": a.guarantors,
           "application_date": _iso(a.application_date), "status": a.status, "current_step": a.current_step,
           "submitted_at": _iso(a.submitted_at), "decided_at": _iso(a.decided_at), "created_by_id": a.created_by_id,
           "assigned_reviewer_id": a.assigned_reviewer_id, "created_at": _iso(a.created_at), "updated_at": _iso(a.updated_at)}
    if extra:
        out.update(extra)
    return out


def document(d: UploadedDocument, *, metric_count: int | None = None, avg_confidence: float | None = None) -> dict:
    return {"id": d.id, "application_id": d.application_id, "category": d.category, "suggested_category": d.suggested_category,
            "classification_confidence": d.classification_confidence, "original_filename": d.original_filename,
            "content_type": d.content_type, "file_kind": d.file_kind, "file_size": d.file_size, "sha256": d.sha256,
            "page_count": d.page_count, "is_scanned": d.is_scanned, "ocr_used": d.ocr_used,
            "ocr_confidence": d.ocr_confidence, "processing_status": d.processing_status,
            "extraction_status": d.extraction_status, "validation_status": d.validation_status,
            "processing_progress": d.processing_progress, "error_message": d.error_message, "warnings": d.warnings,
            "detected_unit": d.detected_unit, "detected_currency": d.detected_currency,
            "detected_periods": d.detected_periods, "is_synthetic": d.is_synthetic, "uploaded_by_id": d.uploaded_by_id,
            "processed_at": _iso(d.processed_at), "created_at": _iso(d.created_at),
            "metric_count": metric_count, "average_confidence": avg_confidence}


def metric(m: ExtractedFinancialMetric) -> dict:
    return {"id": m.id, "document_id": m.document_id, "metric_key": m.metric_key, "label": m.label,
            "period_label": m.period_label, "period_end": _iso(m.period_end), "value": m.value,
            "effective_value": m.effective_value, "text_value": m.text_value, "raw_text": m.raw_text, "unit": m.unit,
            "unit_multiplier": m.unit_multiplier, "currency": m.currency, "page_number": m.page_number,
            "confidence": m.confidence, "extraction_method": m.extraction_method, "source_excerpt": m.source_excerpt,
            "flags": m.flags, "verification_status": m.verification_status, "corrected_value": m.corrected_value,
            "correction_note": m.correction_note, "corrected_at": _iso(m.corrected_at)}


def signal(s: DocumentSignal) -> dict:
    return {"id": s.id, "document_id": s.document_id, "signal_key": s.signal_key, "label": s.label, "area": s.area,
            "severity": s.severity, "excerpt": s.excerpt, "page_number": s.page_number, "amount": s.amount,
            "is_dismissed": s.is_dismissed}


def statement(s: FinancialStatement) -> dict:
    return {"id": s.id, "period_label": s.period_label, "period_end": _iso(s.period_end), "currency": s.currency,
            "line_items": s.line_items, "source_document_ids": s.source_document_ids, "conflicts": s.conflicts}


def ratio(r: FinancialRatio) -> dict:
    return {"id": r.id, "period": r.period_label, "key": r.ratio_key, "name": r.name, "category": r.category,
            "formula": r.formula, "inputs": r.inputs, "value": r.value, "unit": r.unit, "status": r.status,
            "interpretation": r.interpretation, "sources": r.sources}


def bank_txn(t: BankTransaction) -> dict:
    return {"id": t.id, "document_id": t.document_id, "txn_date": _iso(t.txn_date), "description": t.description,
            "debit": t.debit, "credit": t.credit, "balance": t.balance, "counterparty": t.counterparty,
            "category": t.category, "row_ref": t.row_ref, "is_flagged": t.is_flagged, "flag_reason": t.flag_reason}


def gst_row(g: GSTRecord) -> dict:
    return {"id": g.id, "document_id": g.document_id, "return_type": g.return_type, "period": g.period, "gstin": g.gstin,
            "taxable_value": g.taxable_value, "total_tax": g.total_tax, "itc_claimed": g.itc_claimed,
            "itc_available": g.itc_available, "filing_date": _iso(g.filing_date), "row_ref": g.row_ref}


def reconciliation(r: Reconciliation, doc_names: dict[int, str] | None = None) -> dict:
    names = doc_names or {}
    return {"id": r.id, "run_number": r.run_number, "check_type": r.check_type, "metric": r.metric, "period": r.period,
            "source_a_label": r.source_a_label, "source_a_value": r.source_a_value, "source_b_label": r.source_b_label,
            "source_b_value": r.source_b_value, "difference": r.difference, "difference_pct": r.difference_pct,
            "severity": r.severity, "status": r.status, "description": r.description,
            "risk_implication": r.risk_implication, "confidence": r.confidence,
            "suggested_investigation": r.suggested_investigation,
            "source_documents": [{"id": i, "name": names.get(i)} for i in (r.source_document_ids or [])],
            "details": r.details, "review_status": r.review_status, "reviewed_by_id": r.reviewed_by_id,
            "created_at": _iso(r.created_at)}


def assessment(a: RiskAssessment) -> dict:
    return {"id": a.id, "application_id": a.application_id, "version": a.version, "overall_score": a.overall_score,
            "risk_category": a.risk_category, "confidence_score": a.confidence_score, "confidence_level": a.confidence_level,
            "data_completeness": a.data_completeness, "document_coverage": a.document_coverage,
            "missing_information": a.missing_information, "red_flags": a.red_flags, "five_cs": a.five_cs,
            "risk_categories": a.risk_categories, "confidence_breakdown": a.confidence_breakdown,
            "ratios": a.ratios_snapshot, "summary": a.summary, "model_label": a.model_label,
            "dimensions": [{"key": d.key, "name": d.name, "weight": d.weight, "score": d.score,
                            "weighted_score": d.weighted_score, "data_status": d.data_status, "factors": d.factors,
                            "explanation": d.explanation} for d in a.dimensions],
            "created_by_id": a.created_by_id, "created_at": _iso(a.created_at)}


def recommendation(r: LoanRecommendation) -> dict:
    return {"id": r.id, "application_id": r.application_id, "assessment_id": r.assessment_id, "version": r.version,
            "outcome": r.outcome, "outcome_label": OUTCOME_LABELS.get(r.outcome, r.outcome), "risk_category": r.risk_category,
            "requested_amount": r.requested_amount, "recommended_amount": r.recommended_amount,
            "amount_constraints": r.amount_constraints, "base_rate": r.base_rate, "risk_premium": r.risk_premium,
            "rate_adjustments": r.rate_adjustments, "indicative_rate": r.indicative_rate,
            "requested_tenure_months": r.requested_tenure_months, "recommended_tenure_months": r.recommended_tenure_months,
            "tenure_rationale": r.tenure_rationale, "repayment_capacity": r.repayment_capacity,
            "collateral_requirements": r.collateral_requirements, "repayment_considerations": r.repayment_considerations,
            "conditions_precedent": r.conditions_precedent, "positive_factors": r.positive_factors,
            "negative_factors": r.negative_factors, "critical_warnings": r.critical_warnings, "explanation": r.explanation,
            "narrative": r.narrative, "narrative_method": r.narrative_method,
            "supporting_documents": r.supporting_documents, "research_references": r.research_references,
            "created_at": _iso(r.created_at),
            "disclaimer": "Decision-support output. Subject to review and approval by an authorised credit officer."}


def research_query(q: ResearchQuery) -> dict:
    return {"id": q.id, "run_number": q.run_number, "query_text": q.query_text, "category": q.category, "entity": q.entity,
            "provider": q.provider, "status": q.status, "result_count": q.result_count, "message": q.message,
            "executed_at": _iso(q.executed_at)}


def research_finding(f: ResearchFinding) -> dict:
    return {"id": f.id, "query_id": f.query_id, "title": f.title, "source_name": f.source_name, "source_url": f.source_url,
            "source_tier": f.source_tier, "published_at": _iso(f.published_at), "retrieved_at": _iso(f.retrieved_at),
            "entity": f.entity, "category": f.category, "severity": f.severity, "sentiment": f.sentiment,
            "summary": f.summary, "summary_method": f.summary_method, "evidence_excerpt": f.evidence_excerpt,
            "verification_status": f.verification_status, "relevance_score": f.relevance_score,
            "is_synthetic": f.is_synthetic, "include_in_assessment": f.include_in_assessment,
            "reviewer_note": f.reviewer_note}


def observation(o: PrimaryObservation) -> dict:
    return {"id": o.id, "category": o.category, "sub_category": o.sub_category, "title": o.title, "notes": o.notes,
            "rating": o.rating, "numeric_value": o.numeric_value, "numeric_unit": o.numeric_unit,
            "observed_on": _iso(o.observed_on), "author_id": o.author_id, "severity": o.severity,
            "severity_source": o.severity_source, "risk_impact": o.risk_impact, "interpretation": o.interpretation,
            "signals": o.signals, "reviewer_status": o.reviewer_status, "reviewer_comment": o.reviewer_comment,
            "reviewed_at": _iso(o.reviewed_at), "is_synthetic": o.is_synthetic, "created_at": _iso(o.created_at)}


def chat_message(m: ChatMessage) -> dict:
    return {"id": m.id, "session_id": m.session_id, "role": m.role, "content": m.content, "citations": m.citations,
            "method": m.method, "grounded": m.grounded, "created_at": _iso(m.created_at)}


def cam(r: CAMReport, *, include_content: bool = False) -> dict:
    out = {"id": r.id, "application_id": r.application_id, "version": r.version, "status": r.status,
           "assessment_id": r.assessment_id, "recommendation_id": r.recommendation_id, "error": r.error,
           "has_pdf": bool(r.pdf_path), "has_docx": bool(r.docx_path), "generated_by_id": r.generated_by_id,
           "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at)}
    if include_content:
        out["content"] = r.content
    return out


def comment(c: ReviewComment, names: dict[int, str]) -> dict:
    return {"id": c.id, "section": c.section, "entity_type": c.entity_type, "entity_id": c.entity_id, "body": c.body,
            "author_id": c.author_id, "author_name": names.get(c.author_id), "created_at": _iso(c.created_at)}


def decision(d: ReviewDecision, names: dict[int, str]) -> dict:
    return {"id": d.id, "recommendation_id": d.recommendation_id, "decision": d.decision, "reviewer_id": d.reviewer_id,
            "reviewer_name": names.get(d.reviewer_id), "final_amount": d.final_amount, "final_rate": d.final_rate,
            "final_tenure_months": d.final_tenure_months, "comments": d.comments,
            "overrides_recommendation": d.overrides_recommendation, "created_at": _iso(d.created_at)}


def audit_log(a: AuditLog, names: dict[int, str]) -> dict:
    return {"id": a.id, "action": a.action, "summary": a.summary, "entity_type": a.entity_type, "entity_id": a.entity_id,
            "application_id": a.application_id, "user_id": a.user_id, "user_name": names.get(a.user_id),
            "details": a.details, "ip_address": a.ip_address, "created_at": _iso(a.created_at)}


def notification(n: Notification) -> dict:
    return {"id": n.id, "title": n.title, "message": n.message, "level": n.level, "link": n.link, "is_read": n.is_read,
            "created_at": _iso(n.created_at)}


def job(j: ProcessingJob) -> dict:
    return {"id": j.id, "job_type": j.job_type, "status": j.status, "progress": j.progress, "message": j.message,
            "error": j.error, "attempts": j.attempts, "application_id": j.application_id, "document_id": j.document_id,
            "result": j.result, "created_at": _iso(j.created_at), "started_at": _iso(j.started_at),
            "finished_at": _iso(j.finished_at)}
