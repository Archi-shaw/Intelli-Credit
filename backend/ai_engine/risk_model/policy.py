"""Default configurable scoring & lending policy.

IMPORTANT: these are *prototype policy* parameters chosen for demonstration. They are not an
official banking, RBI, or credit-rating-agency standard and have not been statistically calibrated.
Administrators can version and edit them through the Settings → Scoring Policy screen.
"""
from __future__ import annotations

import copy

DIMENSIONS = [
    ("financial_health", "Financial Health"),
    ("liquidity_cash_flow", "Liquidity and Cash Flow"),
    ("leverage_debt_servicing", "Leverage and Debt Servicing"),
    ("profitability_performance", "Profitability and Performance"),
    ("governance_management", "Governance and Management"),
    ("legal_regulatory", "Legal and Regulatory Risk"),
    ("industry_business", "Industry and Business Risk"),
    ("operational_due_diligence", "Operational Due Diligence"),
    ("reconciliation_compliance", "Reconciliation and Compliance"),
]

RISK_CATEGORIES = [
    ("financial", "Financial Risk"),
    ("liquidity", "Liquidity Risk"),
    ("leverage", "Leverage Risk"),
    ("profitability", "Profitability Risk"),
    ("cash_flow", "Cash Flow Risk"),
    ("governance", "Governance Risk"),
    ("management", "Management Risk"),
    ("legal", "Legal and Litigation Risk"),
    ("industry", "Industry and Sector Risk"),
    ("operational", "Operational Risk"),
    ("compliance", "Compliance Risk"),
    ("data_quality", "Documentation and Data Quality Risk"),
]

DEFAULT_POLICY: dict = {
    "label": "Intelli-Credit Prototype Policy Scorecard",
    "disclaimer": (
        "Prototype policy thresholds for demonstration only. Not an official banking, RBI or rating-agency "
        "standard, and not a statistically validated probability of default."
    ),
    "weights": {
        "financial_health": 25,
        "liquidity_cash_flow": 15,
        "leverage_debt_servicing": 15,
        "profitability_performance": 10,
        "governance_management": 10,
        "legal_regulatory": 10,
        "industry_business": 5,
        "operational_due_diligence": 5,
        "reconciliation_compliance": 5,
    },
    "rating_bands": [
        {"min": 80, "label": "Low Risk"},
        {"min": 65, "label": "Moderate Risk"},
        {"min": 50, "label": "Elevated Risk"},
        {"min": 35, "label": "High Risk"},
        {"min": 0, "label": "Very High Risk"},
    ],
    # Piecewise-linear scoring curves: [[input value, score 0-100], ...]
    "factor_curves": {
        "current_ratio": [[0.5, 5], [0.8, 25], [1.0, 45], [1.2, 60], [1.33, 70], [1.5, 80], [2.0, 92], [3.0, 95]],
        "quick_ratio": [[0.3, 10], [0.5, 30], [0.8, 55], [1.0, 75], [1.5, 92]],
        "debt_to_equity": [[0.25, 95], [0.5, 90], [1.0, 78], [1.5, 65], [2.0, 52], [3.0, 30], [4.0, 15], [6.0, 5]],
        "debt_to_assets": [[0.1, 95], [0.3, 82], [0.5, 60], [0.65, 40], [0.8, 15]],
        "debt_to_ebitda": [[1.0, 95], [2.0, 85], [3.0, 70], [4.0, 55], [5.0, 40], [6.0, 25], [8.0, 10]],
        "interest_coverage": [[0.5, 5], [1.0, 20], [1.5, 40], [2.0, 55], [3.0, 72], [5.0, 88], [8.0, 96]],
        "net_profit_margin": [[-10, 0], [0, 25], [3, 50], [6, 70], [10, 85], [15, 95]],
        "ebitda_margin": [[0, 10], [5, 35], [10, 60], [15, 78], [20, 90], [30, 96]],
        "roe": [[-10, 0], [0, 25], [8, 55], [15, 78], [25, 92]],
        "roa": [[-5, 0], [0, 25], [3, 50], [6, 72], [10, 88]],
        "revenue_growth": [[-20, 10], [-5, 35], [0, 50], [5, 62], [10, 75], [20, 88], [35, 92]],
        "ocf_to_debt": [[-0.2, 5], [0, 20], [0.1, 45], [0.2, 65], [0.35, 82], [0.5, 92]],
        "cash_conversion": [[-0.5, 5], [0, 20], [0.4, 45], [0.7, 70], [0.9, 85], [1.1, 92]],
        "receivable_days": [[30, 92], [60, 78], [90, 60], [120, 42], [180, 20], [270, 8]],
        "capacity_utilisation": [[20, 5], [40, 25], [55, 45], [65, 62], [75, 78], [85, 90], [95, 95]],
        "promoter_holding_pct": [[10, 30], [26, 50], [40, 65], [51, 80], [75, 90]],
        "promoter_pledge_pct": [[0, 95], [10, 75], [25, 50], [50, 20], [75, 5]],
        "contingent_to_networth": [[0, 95], [0.1, 85], [0.25, 65], [0.5, 40], [1.0, 15]],
        "rpt_to_revenue": [[0, 95], [0.05, 85], [0.1, 70], [0.2, 50], [0.35, 25], [0.5, 10]],
        "security_coverage": [[0, 10], [0.5, 30], [1.0, 60], [1.25, 75], [1.5, 88], [2.0, 95]],
    },
    "thresholds": {
        "current_ratio_min": 1.2,
        "quick_ratio_min": 0.8,
        "interest_coverage_min": 2.0,
        "debt_to_equity_max": 3.0,
        "dscr_min": 1.25,
        "capacity_utilisation_min": 60,
        "receivable_days_max": 120,
        "ebitda_margin_min": 8,
        "stale_financials_months": 18,
        "promoter_pledge_max": 50,
    },
    "credit_rating_scores": {
        "AAA": 98, "AA+": 95, "AA": 93, "AA-": 90, "A+": 86, "A": 83, "A-": 79, "BBB+": 72, "BBB": 67, "BBB-": 62,
        "BB+": 50, "BB": 45, "BB-": 40, "B+": 33, "B": 28, "B-": 22, "C+": 14, "C": 10, "C-": 6, "D": 2,
    },
    # Score contributed by a qualitative evidence item of a given severity.
    "severity_scores": {"critical": 5, "high": 25, "medium": 50, "low": 70, "info": 85, "positive": 92},
    "qualitative_baseline": 80,
    "missing_dimension_score": 40,
    "verification_weights": {
        "observations": {"verified": 1.0, "pending": 0.5, "disputed": 0.0},
        "research": {"verified": 1.0, "unverified": 0.6, "synthetic": 0.6, "disputed": 0.0},
        "document_signals": 0.8,
    },
    "reconciliation_deductions": {"critical": 50, "high": 35, "medium": 18, "low": 6, "info": 0},
    "reconciliation_baseline": 88,
    "industry_risk": {
        "default": "medium",
        "levels": {"low": 82, "medium": 64, "high": 44, "very_high": 25},
        "map": {
            "Pharmaceuticals": "low", "FMCG": "low", "IT Services": "low", "Healthcare": "low",
            "Auto Components": "medium", "Engineering": "medium", "Industrial Manufacturing": "medium",
            "Chemicals": "medium", "Agro Processing": "medium", "Food Processing": "medium", "Logistics": "medium",
            "Renewable Energy": "medium", "Electronics": "medium", "Packaging": "medium",
            "Textiles": "high", "Construction": "high", "Steel": "high", "Hospitality": "high", "Power": "high",
            "Telecom": "high", "Trading": "high", "Aviation": "very_high", "Real Estate": "very_high",
            "Gems and Jewellery": "very_high",
        },
    },
    "confidence": {
        "required_documents": ["financial_statements", "bank_statements", "gst_returns"],
        "recommended_documents": [
            "income_tax_returns", "annual_reports", "credit_rating_reports", "sanction_letters", "shareholding_disclosures",
        ],
        "weights": {"documents": 35, "completeness": 30, "extraction": 15, "research_diligence": 10, "recency": 10},
        "levels": [{"min": 75, "label": "High"}, {"min": 50, "label": "Medium"}, {"min": 0, "label": "Low"}],
    },
    "decision": {
        "base_rate": 9.0,
        "base_rate_label": "Illustrative base lending rate (prototype assumption, not a published benchmark)",
        "risk_premiums": {"Low Risk": 1.0, "Moderate Risk": 2.0, "Elevated Risk": 3.25, "High Risk": 5.0, "Very High Risk": None},
        "adjustments": {
            "unsecured_premium": 0.75,
            "low_confidence_premium": 0.5,
            "long_tenure_threshold_months": 84,
            "long_tenure_premium": 0.25,
            "strong_collateral_coverage": 1.5,
            "strong_collateral_discount": -0.25,
        },
        "min_dscr": 1.25,
        "max_post_loan_debt_to_equity": 3.0,
        "collateral_haircuts": {
            "property": 0.65, "plant_machinery": 0.6, "fixed_deposit": 0.9, "receivables": 0.75, "inventory": 0.7,
            "shares": 0.5, "vehicle": 0.6, "other": 0.5,
        },
        "min_security_coverage": {"Low Risk": 1.0, "Moderate Risk": 1.25, "Elevated Risk": 1.5, "High Risk": 2.0, "Very High Risk": None},
        "default_residual_tenure_months": 60,
        "unsecured_networth_cap": 0.25,
        "working_capital_turnover_cap": 0.25,
        "working_capital_loan_types": ["working_capital", "cash_credit", "overdraft"],
        "amount_multipliers": {"Low Risk": 1.0, "Moderate Risk": 0.9, "Elevated Risk": 0.75, "High Risk": 0.5, "Very High Risk": 0.0},
        "max_tenure_months": {"Low Risk": 120, "Moderate Risk": 96, "Elevated Risk": 72, "High Risk": 48, "Very High Risk": 0},
        "approve_min_score": 65,
        "conditional_min_score": 50,
        "min_confidence_for_decision": 45,
        "critical_flags_force_rejection": True,
        "rounding": 100000,
    },
}


def get_default_policy() -> dict:
    return copy.deepcopy(DEFAULT_POLICY)


def merge_policy(overrides: dict | None) -> dict:
    """Deep-merge stored policy config over the defaults so new keys always exist."""
    base = get_default_policy()

    def _merge(a: dict, b: dict):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                _merge(a[k], v)
            else:
                a[k] = v

    if overrides:
        _merge(base, overrides)
    return base


def validate_policy(cfg: dict) -> list[str]:
    errors: list[str] = []
    weights = cfg.get("weights", {})
    keys = {k for k, _ in DIMENSIONS}
    if set(weights) != keys:
        errors.append("Weights must be defined for exactly the nine scoring dimensions.")
    total = sum(float(v) for v in weights.values())
    if abs(total - 100) > 0.01:
        errors.append(f"Dimension weights must total 100 (currently {total:g}).")
    if any(float(v) < 0 for v in weights.values()):
        errors.append("Weights cannot be negative.")
    bands = cfg.get("rating_bands", [])
    mins = [b.get("min") for b in bands]
    if not bands or sorted(mins, reverse=True) != mins or mins[-1] != 0:
        errors.append("Rating bands must be in descending order of 'min' and end with a band at 0.")
    for name, curve in cfg.get("factor_curves", {}).items():
        xs = [p[0] for p in curve]
        if len(curve) < 2 or xs != sorted(xs):
            errors.append(f"Curve '{name}' needs at least two points with ascending input values.")
        if any(not (0 <= p[1] <= 100) for p in curve):
            errors.append(f"Curve '{name}' scores must be between 0 and 100.")
    d = cfg.get("decision", {})
    if d.get("base_rate") is None or not (0 < float(d["base_rate"]) < 30):
        errors.append("Base rate must be between 0 and 30%.")
    if not (0 < float(d.get("min_dscr", 0)) < 5):
        errors.append("Minimum DSCR must be between 0 and 5.")
    if float(d.get("conditional_min_score", 0)) > float(d.get("approve_min_score", 0)):
        errors.append("Conditional-approval minimum score cannot exceed the approval minimum score.")
    return errors


def band_for(score: float, policy: dict) -> str:
    for band in policy["rating_bands"]:
        if score >= band["min"]:
            return band["label"]
    return policy["rating_bands"][-1]["label"]


def curve_score(curve: list[list[float]], x: float) -> float:
    """Piecewise-linear interpolation, clamped at both ends."""
    pts = sorted(curve, key=lambda p: p[0])
    if x <= pts[0][0]:
        return float(pts[0][1])
    if x >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y1)
            return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(pts[-1][1])  # pragma: no cover
