"""Regression tests for the deterministic AI-engine core (no database required)."""
from datetime import date, timedelta

from ai_engine.decision_engine.engine import DecisionInputs, decide
from ai_engine.extraction.financial_extractor import PageContent, extract_document
from ai_engine.extraction.units import detect_unit, header_periods, parse_number
from ai_engine.nlp.observations import interpret_observation
from ai_engine.reconciliation.engine import ReconInputs, run_reconciliation
from ai_engine.risk_model.policy import get_default_policy, validate_policy
from ai_engine.risk_model.ratios import compute_ratios, derive_metrics
from ai_engine.risk_model.scorecard import AssessmentInputs, run_scorecard

PL_TEXT = """Statement of Profit and Loss for the year ended 31 March 2024
(All amounts in ₹ Lakhs, unless otherwise stated)
Particulars                                Note     FY 2023-24     FY 2022-23
I. Revenue from operations                  18      12,450.60      10,880.25
Finance costs                               22         310.20         288.40
Profit for the year                                  1,280.30       1,044.65
"""


def m(v):
    return {"value": v, "document_id": 1, "page_number": 1, "metric_id": None, "confidence": 0.9}


HEALTHY = dict(
    revenue_from_operations=1245060000, pat=128030000, pbt=172040000, finance_costs=31020000, depreciation=40210000,
    tax_expense=44010000, total_assets=1126040000, current_assets=548020000, current_liabilities=305030000,
    inventory=185000000, trade_receivables=224050000, long_term_borrowings=190000000, short_term_borrowings=115000000,
    shareholders_equity=596000000, operating_cash_flow=150000000,
)


def build_inputs(current_values: dict, *, collateral_value: float = 4e8, research_mode: str = "synthetic"):
    policy = get_default_policy()
    cur = derive_metrics({k: m(v) for k, v in current_values.items()})
    ratios = {r.key: r for r in compute_ratios(cur, None, period="FY2023-24", thresholds=policy["thresholds"])}
    docs = [{"category": c, "extraction_status": "extracted"} for c in ("financial_statements", "bank_statements", "gst_returns")]
    app = {"requested_amount": 1.5e8, "tenure_months": 60, "loan_type": "term_loan",
           "collateral": [{"type": "property", "value": collateral_value}] if collateral_value else [],
           "guarantors": [], "existing_borrowings": []}
    inp = AssessmentInputs(
        company={"name": "Test Co", "industry": "Auto Components"}, application=app, latest_period="FY2023-24",
        latest_period_end=date(2024, 3, 31), current=cur, prior=None, ratios=ratios,
        documents=docs if current_values else [], metric_confidences=[0.9] * len(current_values),
        verified_metric_share=0.0, signals=[], ratings=[], reconciliations=[], research_mode=research_mode,
        research_findings=[], observations=[], shareholding={}, as_of=date(2024, 11, 1),
    )
    return inp, policy, cur


def run_decision(inp, policy, cur):
    res = run_scorecard(inp, policy)
    dec = decide(DecisionInputs(
        overall_score=res.overall_score, risk_category=res.risk_category, confidence_score=res.confidence["score"],
        confidence_level=res.confidence["level"], red_flags=res.red_flags, factors=res.factors,
        missing_information=res.missing_information, current=cur, application=inp.application,
    ), policy)
    return res, dec


def test_number_unit_and_period_parsing():
    assert parse_number("1,23,456.70") == 123456.7
    assert parse_number("(4,500)") == -4500.0
    assert parse_number("n/a") is None
    assert detect_unit("(₹ in Lakhs)").multiplier == 1e5
    assert [p for p, _ in header_periods("Particulars Note FY 2023-24 FY 2022-23")] == ["FY2023-24", "FY2022-23"]


def test_extractor_maps_columns_and_skips_note_refs():
    res = extract_document([PageContent(1, PL_TEXT)], "financial_statements")
    got = {(x.metric_key, x.period_label): x for x in res.metrics}
    assert got[("revenue_from_operations", "FY2023-24")].value == 1245060000.0
    assert got[("revenue_from_operations", "FY2022-23")].value == 1088025000.0
    assert got[("pat", "FY2023-24")].value == 128030000.0
    assert all(x.confidence >= 0.8 for x in res.metrics)


def test_ratio_safety():
    cur = {"current_assets": m(100.0), "current_liabilities": m(0.0), "total_debt": m(50.0), "shareholders_equity": m(-10.0)}
    r = {x.key: x for x in compute_ratios(cur)}
    assert r["current_ratio"].status == "zero_denominator" and r["current_ratio"].value is None
    assert r["debt_to_equity"].status == "negative_denominator"
    assert r["net_profit_margin"].status == "missing_inputs" and r["net_profit_margin"].value is None


def test_missing_evidence_is_not_favourable():
    inp, policy, cur = build_inputs({}, collateral_value=0, research_mode="not_run")
    res, dec = run_decision(inp, policy, cur)
    liquidity = next(d for d in res.dimensions if d["key"] == "liquidity_cash_flow")
    assert liquidity["data_status"] == "insufficient" and liquidity["score"] == policy["missing_dimension_score"]
    assert res.confidence["level"] == "Low"
    assert dec.outcome == "insufficient_information" and dec.recommended_amount == 0


def test_recommendation_never_exceeds_request_and_is_deterministic():
    inp, policy, cur = build_inputs(HEALTHY)
    _, d1 = run_decision(inp, policy, cur)
    _, d2 = run_decision(inp, policy, cur)
    assert 0 < d1.recommended_amount <= d1.requested_amount
    assert d1.indicative_rate == d1.base_rate + d1.risk_premium + sum(a["value"] for a in d1.rate_adjustments)
    assert d1.to_dict() == d2.to_dict()


def test_negative_net_worth_forces_rejection():
    vals = dict(HEALTHY, shareholders_equity=-5e7)
    inp, policy, cur = build_inputs(vals)
    res, dec = run_decision(inp, policy, cur)
    assert any(f["title"] == "Negative net worth" and f["severity"] == "critical" for f in res.red_flags)
    assert dec.outcome == "reject" and dec.recommended_amount == 0


def test_gst_vs_bank_discrepancy_and_circular_pattern():
    gst = [{"return_type": "GSTR3B", "period": f"2023-{mo:02d}", "taxable_value": 1e7, "total_tax": 1.8e6, "document_id": 1}
           for mo in range(4, 10)]
    txns = [{"txn_date": date(2023, mo, 10), "credit": 6e6, "debit": None, "category": "business_receipt",
             "counterparty": "CUSTOMER ONE", "description": "NEFT CR", "document_id": 2} for mo in range(4, 10)]
    for day in (5, 20):
        txns.append({"txn_date": date(2023, 7, day), "credit": 3e6, "debit": None, "category": "business_receipt",
                     "counterparty": "ZEN TRADING", "description": "RTGS CR ZEN TRADING", "document_id": 2})
        txns.append({"txn_date": date(2023, 7, day) + timedelta(days=2), "credit": None, "debit": 3e6, "category": "payment",
                     "counterparty": "ZEN TRADING", "description": "RTGS DR ZEN TRADING", "document_id": 2})
    items = run_reconciliation(ReconInputs(gst_rows=gst, bank_txns=txns, statements={}, declared_borrowings=[], sanction_metrics=[]))
    by_type = {}
    for it in items:
        by_type.setdefault(it.check_type, it)
    assert by_type["gst_vs_bank"].status == "potential_discrepancy" and by_type["gst_vs_bank"].severity == "high"
    assert by_type["circular_transactions"].status == "unusual_pattern"
    assert "fraud" not in " ".join(i.description.lower() for i in items)


def test_observation_interpretation_capacity():
    it = interpret_observation("factory_inspection", "factory_utilization", "Site visit",
                               "Factory found operating at 40% capacity.", None, None)
    assert it.numeric_value == 40 and it.severity == "medium" and it.risk_impact == "negative"
    assert "partial weight" in it.interpretation


def test_default_policy_is_valid():
    assert validate_policy(get_default_policy()) == []
