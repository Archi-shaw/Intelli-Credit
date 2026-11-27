"""Challenger feature definitions — one implementation shared by training and live assessments.

Ratio features are produced by the production ratio engine (``compute_ratios``) from statement metrics, so the
model sees exactly the numbers the scorecard sees. Missing inputs stay NaN (never zero); LightGBM handles them
natively and the logistic baseline imputes with explicit missing-indicator columns.

All features describe the borrower *as of the application* (latest financial period, bank and GST data before the
application). Nothing observed after the outcome window is used, so there is no target leakage by construction.
"""
from __future__ import annotations

import math

from ai_engine.risk_model.ratios import compute_ratios, derive_metrics

# name, description, monotone direction for default risk (+1 more risk as value rises, -1 less risk, 0 free)
FEATURES: list[tuple[str, str, int]] = [
    ("log_revenue", "log10 of revenue from operations (business size, ₹)", 0),
    ("revenue_growth", "Year-on-year revenue growth, % (NaN without a prior period)", 0),
    ("ebitda_margin", "EBITDA / revenue, %", -1),
    ("net_profit_margin", "Profit after tax / revenue, %", -1),
    ("debt_to_equity", "Total debt / shareholders' equity, x (NaN when equity <= 0)", 1),
    ("net_worth_negative", "1 if shareholders' equity <= 0, else 0", 1),
    ("debt_to_revenue", "Total debt / revenue, x", 1),
    ("current_ratio", "Current assets / current liabilities, x", -1),
    ("interest_coverage", "EBIT / finance costs, x", -1),
    ("ocf_margin", "Operating cash flow / revenue, %", -1),
    ("ocf_to_debt", "Operating cash flow / total debt, x", -1),
    ("working_capital_to_revenue", "(Current assets - current liabilities) / revenue, %", -1),
    ("receivable_days", "Trade receivables / revenue x 365, days", 1),
    ("gst_bank_variance_pct", "|Bank business receipts - GST turnover| / GST turnover, % (NaN if either source missing)", 1),
    ("circular_transactions_flag", "1 if a round-trip (circular) transaction pattern was detected, 0 if screened clean", 1),
    ("cheque_returns", "Cheque / mandate returns in the bank statements (count)", 1),
]
FEATURE_NAMES = [f for f, _, _ in FEATURES]
MONOTONE = [m for _, _, m in FEATURES]
NAN = float("nan")


def _v(m: dict, k: str) -> float | None:
    it = m.get(k)
    return None if it is None or it.get("value") is None else float(it["value"])


def _div(a: float | None, b: float | None, scale: float = 1.0) -> float:
    if a is None or b is None or b == 0:
        return NAN
    return a / b * scale


def compute_features(current: dict[str, dict], prior: dict[str, dict] | None, recon: dict | None) -> dict[str, float]:
    """current/prior: metric_key -> {"value": ...}; recon: {"gst_bank_variance_pct", "circular", "cheque_returns"}."""
    cur = derive_metrics(dict(current))
    pri = derive_metrics(dict(prior)) if prior else None
    ratios = {r.key: r.value for r in compute_ratios(cur, pri)}
    rev, debt, eq = _v(cur, "revenue_from_operations"), _v(cur, "total_debt"), _v(cur, "shareholders_equity")
    ca, cl, ocf = _v(cur, "current_assets"), _v(cur, "current_liabilities"), _v(cur, "operating_cash_flow")
    recon = recon or {}

    def r(key):
        v = ratios.get(key)
        return NAN if v is None else float(v)

    feats = {
        "log_revenue": math.log10(rev) if rev and rev > 0 else NAN,
        "revenue_growth": r("revenue_growth"),
        "ebitda_margin": r("ebitda_margin"),
        "net_profit_margin": r("net_profit_margin"),
        "debt_to_equity": r("debt_to_equity"),
        "net_worth_negative": NAN if eq is None else float(eq <= 0),
        "debt_to_revenue": _div(debt, rev),
        "current_ratio": r("current_ratio"),
        "interest_coverage": r("interest_coverage"),
        "ocf_margin": _div(ocf, rev, 100.0),
        "ocf_to_debt": r("ocf_to_debt"),
        "working_capital_to_revenue": _div(None if ca is None or cl is None else ca - cl, rev, 100.0),
        "receivable_days": r("receivable_days"),
        "gst_bank_variance_pct": _num(recon.get("gst_bank_variance_pct")),
        "circular_transactions_flag": _num(recon.get("circular")),
        "cheque_returns": _num(recon.get("cheque_returns")),
    }
    return {k: feats[k] for k in FEATURE_NAMES}


def _num(v) -> float:
    if v is None:
        return NAN
    try:
        f = float(v)
    except (TypeError, ValueError):
        return NAN
    return f if math.isfinite(f) else NAN


def recon_signals(recon_items: list[dict], bank_statements_present: bool, cheque_returns: int | None) -> dict:
    """Map persisted reconciliation results to the challenger's reconciliation features (NaN when not assessable)."""
    by = {r["check_type"]: r for r in recon_items}
    gst = by.get("gst_vs_bank")
    variance = abs(gst["difference_pct"]) if gst and gst.get("difference_pct") is not None and gst["status"] != "insufficient_data" else None
    circ = by.get("circular_transactions")
    circular = (1.0 if circ and circ["status"] == "unusual_pattern" else 0.0) if bank_statements_present else None
    return {"gst_bank_variance_pct": variance, "circular": circular,
            "cheque_returns": cheque_returns if bank_statements_present else None}
