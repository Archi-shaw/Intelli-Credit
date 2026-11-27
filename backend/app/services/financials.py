"""Consolidated financial statements and ratio calculation from extracted (and analyst-corrected) metrics.

For each period and metric the highest-confidence value wins; materially different values
from other sources are recorded as conflicts instead of being silently overwritten.
Missing metrics stay missing — nothing is defaulted to zero.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ai_engine.extraction.financial_extractor import metric_display_name
from ai_engine.risk_model.ratios import RatioResult, compute_ratios, derive_metrics
from app.core.database import lock_application
from app.models import ExtractedFinancialMetric, FinancialRatio, FinancialStatement, UploadedDocument

log = logging.getLogger("intellicredit.financials")

# Statement metrics only come from statement-type documents (bank/GST figures are reconciled separately).
STATEMENT_CATEGORIES = {"financial_statements", "annual_reports", "income_tax_returns", "other"}
CONFLICT_TOLERANCE = 0.01


def _metric_rows(db: Session, application_id: int) -> list[tuple[ExtractedFinancialMetric, str]]:
    return db.execute(
        select(ExtractedFinancialMetric, UploadedDocument.category)
        .join(UploadedDocument, UploadedDocument.id == ExtractedFinancialMetric.document_id)
        .where(ExtractedFinancialMetric.application_id == application_id)
    ).all()


def consolidate_statements(db: Session, application_id: int) -> list[FinancialStatement]:
    # Serialised per application: concurrent consolidations (several documents finishing at once) would otherwise
    # each delete only committed rows and both insert, duplicating every period.
    lock_application(db, application_id)
    db.flush()
    groups: dict[str, dict[str, list[ExtractedFinancialMetric]]] = defaultdict(lambda: defaultdict(list))
    period_ends: dict[str, date | None] = {}
    for m, category in _metric_rows(db, application_id):
        if category not in STATEMENT_CATEGORIES or not m.period_label or m.effective_value is None:
            continue
        groups[m.period_label][m.metric_key].append(m)
        if m.period_end:
            period_ends[m.period_label] = m.period_end
    db.execute(delete(FinancialStatement).where(FinancialStatement.application_id == application_id))
    out = []
    for period, metrics in groups.items():
        items, conflicts, docs = {}, [], set()
        for key, cands in metrics.items():
            cands.sort(key=lambda x: (x.verification_status == "verified", x.corrected_value is not None, x.confidence), reverse=True)
            best = cands[0]
            items[key] = {"value": best.effective_value, "metric_id": best.id, "document_id": best.document_id,
                          "page_number": best.page_number, "confidence": best.confidence,
                          "verified": best.verification_status == "verified", "label": metric_display_name(key)}
            docs.add(best.document_id)
            for other in cands[1:]:
                ov = other.effective_value
                if ov is not None and abs(ov - best.effective_value) > abs(best.effective_value) * CONFLICT_TOLERANCE:
                    conflicts.append({"metric": key, "selected": best.effective_value, "selected_metric_id": best.id,
                                      "alternative": ov, "alternative_metric_id": other.id,
                                      "alternative_document_id": other.document_id, "alternative_page": other.page_number})
        st = FinancialStatement(application_id=application_id, period_label=period, period_end=period_ends.get(period),
                                currency="INR", line_items=items, source_document_ids=sorted(d for d in docs if d),
                                conflicts=conflicts)
        db.add(st)
        out.append(st)
    db.flush()
    return out


def statements_by_period(db: Session, application_id: int) -> list[FinancialStatement]:
    """One statement per period, oldest first.

    Duplicated periods can only exist in databases written before consolidation was serialised; the copies are
    identical, so the newest row is used and the duplicate is never mistaken for the prior year.
    """
    rows = db.scalars(select(FinancialStatement).where(FinancialStatement.application_id == application_id)
                      .order_by(FinancialStatement.id)).all()
    by_period = {s.period_label: s for s in rows}
    if len(by_period) != len(rows):
        log.warning("duplicate consolidated statements ignored",
                    extra={"ctx": {"application_id": application_id, "rows": len(rows), "periods": len(by_period)}})
    return sorted(by_period.values(), key=lambda s: s.period_end or date.min)


def period_metrics(stmt: FinancialStatement | None) -> dict[str, dict]:
    if stmt is None:
        return {}
    return {k: {"value": v.get("value"), "document_id": v.get("document_id"), "page_number": v.get("page_number"),
                "metric_id": v.get("metric_id"), "confidence": v.get("confidence")}
            for k, v in (stmt.line_items or {}).items()}


def latest_and_prior(db: Session, application_id: int):
    stmts = statements_by_period(db, application_id)
    latest = stmts[-1] if stmts else None
    prior = stmts[-2] if len(stmts) > 1 else None
    return latest, prior, stmts


def compute_application_ratios(db: Session, application_id: int, thresholds: dict, *,
                               assessment_id: int | None = None, persist: bool = True) -> tuple[dict, dict | None, list[RatioResult], str | None]:
    if persist:
        lock_application(db, application_id)  # delete + re-insert of the persisted ratio set
    latest, prior, _ = latest_and_prior(db, application_id)
    cur = derive_metrics(period_metrics(latest))
    pri = derive_metrics(period_metrics(prior)) if prior else None
    period = latest.period_label if latest else None
    ratios = compute_ratios(cur, pri, period=period, thresholds=thresholds) if latest else []
    if persist:
        q = delete(FinancialRatio).where(FinancialRatio.application_id == application_id)
        q = q.where(FinancialRatio.assessment_id == assessment_id) if assessment_id else q.where(FinancialRatio.assessment_id.is_(None))
        db.execute(q)
        for r in ratios:
            db.add(FinancialRatio(application_id=application_id, assessment_id=assessment_id, period_label=r.period,
                                  ratio_key=r.key, name=r.name, category=r.category, formula=r.formula,
                                  inputs=r.inputs, value=r.value, unit=r.unit, status=r.status,
                                  interpretation=r.interpretation, sources=r.sources))
        db.flush()
    return cur, pri, ratios, period


def trend_series(db: Session, application_id: int, keys: list[str]) -> list[dict]:
    """Per-period values of the requested metrics — only periods/values actually extracted."""
    out = []
    for st in statements_by_period(db, application_id):
        vals = derive_metrics(period_metrics(st))
        row = {"period": st.period_label}
        for k in keys:
            it = vals.get(k)
            row[k] = it.get("value") if it else None
        out.append(row)
    return out
