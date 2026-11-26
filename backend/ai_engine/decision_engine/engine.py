"""Deterministic, policy-driven lending recommendation.

All amounts, rates and tenures are computed here in code from persisted assessment evidence.
An LLM is never asked to produce or alter these numbers. Rates are illustrative prototype
calculations — not bank offers or regulated lending terms.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

OUTCOME_LABELS = {
    "approve": "Recommend Approval",
    "conditional_approval": "Recommend Conditional Approval",
    "reject": "Recommend Rejection",
    "insufficient_information": "Insufficient Information — Manual Review Required",
}


@dataclass
class DecisionInputs:
    overall_score: float
    risk_category: str
    confidence_score: float
    confidence_level: str
    red_flags: list[dict]
    factors: list[dict]
    missing_information: list[dict]
    current: dict[str, dict]  # latest-period metrics
    application: dict  # requested_amount, tenure_months, loan_type, collateral, guarantors, existing_borrowings, moratorium_months
    reconciliations: list[dict] = field(default_factory=list)
    research_findings: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    latest_period: str | None = None


@dataclass
class DecisionResult:
    outcome: str
    outcome_label: str
    requested_amount: float
    recommended_amount: float
    amount_constraints: list[dict]
    base_rate: float | None
    risk_premium: float | None
    rate_adjustments: list[dict]
    indicative_rate: float | None
    requested_tenure_months: int
    recommended_tenure_months: int | None
    tenure_rationale: str
    repayment_capacity: dict
    collateral_requirements: list[dict]
    repayment_considerations: list[str]
    conditions_precedent: list[str]
    positive_factors: list[dict]
    negative_factors: list[dict]
    critical_warnings: list[str]
    explanation: dict

    def to_dict(self) -> dict:
        return asdict(self)


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def annuity_payment(principal: float, annual_rate_pct: float, months: int) -> float:
    if months <= 0:
        return principal
    r = annual_rate_pct / 1200.0
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def principal_from_payment(monthly_payment: float, annual_rate_pct: float, months: int) -> float:
    if months <= 0 or monthly_payment <= 0:
        return 0.0
    r = annual_rate_pct / 1200.0
    if r == 0:
        return monthly_payment * months
    return monthly_payment * (1 - (1 + r) ** -months) / r


def _v(cur: dict, key: str) -> float | None:
    it = cur.get(key)
    return None if it is None or it.get("value") is None else float(it["value"])


def _existing_debt_service(inp: DecisionInputs, policy: dict) -> tuple[float | None, list[str], bool]:
    """Annual debt service on existing borrowings and the basis used. Returns (amount, notes, assumed)."""
    d = policy["decision"]
    rows = inp.application.get("existing_borrowings") or []
    notes, total, assumed = [], 0.0, False
    if rows:
        for b in rows:
            lender = b.get("lender") or "lender"
            if b.get("annual_debt_service") not in (None, ""):
                amt = float(b["annual_debt_service"])
                notes.append(f"{lender}: declared annual debt service {_money(amt)}")
            elif b.get("outstanding_amount") not in (None, "") and b.get("interest_rate") not in (None, ""):
                out = float(b["outstanding_amount"])
                rate = float(b["interest_rate"])
                months = int(b.get("remaining_tenure_months") or 0)
                if b.get("facility_type") in d["working_capital_loan_types"]:
                    amt = out * rate / 100.0
                    notes.append(f"{lender}: revolving facility — interest only {_money(out)} × {rate:.2f}% = {_money(amt)}")
                else:
                    if months <= 0:
                        months = d["default_residual_tenure_months"]
                        assumed = True
                        notes.append(f"{lender}: residual tenure not declared — assumed {months} months (policy default)")
                    amt = annuity_payment(out, rate, months) * 12
                    notes.append(f"{lender}: {_money(out)} at {rate:.2f}% over {months} months ⇒ {_money(amt)} per year")
            elif b.get("outstanding_amount") not in (None, ""):
                out = float(b["outstanding_amount"])
                months = d["default_residual_tenure_months"]
                fc_rate = 10.0
                amt = annuity_payment(out, fc_rate, months) * 12
                assumed = True
                notes.append(f"{lender}: rate/tenure not declared — assumed {fc_rate:.1f}% over {months} months ⇒ {_money(amt)} per year")
            else:
                continue
            total += amt
        return total, notes, assumed
    fc = _v(inp.current, "finance_costs")
    ltd = _v(inp.current, "long_term_borrowings")
    if fc is not None:
        principal = (ltd or 0.0) / (d["default_residual_tenure_months"] / 12)
        notes.append(f"No declared borrowing schedule — finance costs {_money(fc)} + long-term borrowings {_money(ltd or 0)} "
                     f"amortised over {d['default_residual_tenure_months']} months (policy assumption)")
        return fc + principal, notes, True
    return None, ["Existing debt service could not be determined (no borrowing schedule or finance costs)."], True


def decide(inp: DecisionInputs, policy: dict) -> DecisionResult:
    d = policy["decision"]
    app = inp.application
    requested = float(app.get("requested_amount") or 0)
    req_tenure = int(app.get("tenure_months") or 0)
    loan_type = app.get("loan_type") or "term_loan"
    is_wc = loan_type in d["working_capital_loan_types"]
    band = inp.risk_category
    factors = {f["key"]: f for f in inp.factors}

    # ---------------- Interest rate ----------------
    base_rate = float(d["base_rate"])
    premium = d["risk_premiums"].get(band)
    adjustments: list[dict] = []
    collateral = app.get("collateral") or []
    coverage = factors.get("security_coverage", {}).get("value") or 0.0
    if premium is not None:
        adj = d["adjustments"]
        if not collateral:
            adjustments.append({"name": "Unsecured facility", "value": adj["unsecured_premium"],
                                "reason": "No collateral offered; higher loss-given-default."})
        elif coverage >= adj["strong_collateral_coverage"]:
            adjustments.append({"name": "Strong security coverage", "value": adj["strong_collateral_discount"],
                                "reason": f"Realisable security covers {coverage:.2f}x of the requested amount."})
        if inp.confidence_level == "Low":
            adjustments.append({"name": "Low assessment confidence", "value": adj["low_confidence_premium"],
                                "reason": "Evidence gaps increase uncertainty in the risk assessment."})
        if req_tenure > adj["long_tenure_threshold_months"] and not is_wc:
            adjustments.append({"name": "Long tenure", "value": adj["long_tenure_premium"],
                                "reason": f"Requested tenure of {req_tenure} months exceeds {adj['long_tenure_threshold_months']} months."})
        rate = round(base_rate + float(premium) + sum(a["value"] for a in adjustments), 2)
    else:
        rate = None

    # ---------------- Repayment capacity ----------------
    ebitda = _v(inp.current, "ebitda")
    ocf = _v(inp.current, "operating_cash_flow")
    tax = _v(inp.current, "tax_expense")
    if ebitda is not None:
        ebitda_after_tax = ebitda - (tax or 0.0)
        candidates = [("EBITDA − tax" if tax is not None else "EBITDA", ebitda_after_tax)]
        if ocf is not None:
            candidates.append(("Operating cash flow", ocf))
        basis, cash_available = min(candidates, key=lambda c: c[1])
    elif ocf is not None:
        basis, cash_available = "Operating cash flow", ocf
    else:
        basis, cash_available = None, None
    existing_service, service_notes, service_assumed = _existing_debt_service(inp, policy)
    min_dscr = float(d["min_dscr"])
    rc: dict = {"cash_available_for_debt_service": cash_available, "basis": basis, "existing_annual_debt_service": existing_service,
                "existing_service_notes": service_notes, "existing_service_assumed": service_assumed, "min_dscr": min_dscr,
                "period": inp.latest_period}

    constraints: list[dict] = [{"key": "requested", "name": "Requested amount", "value": requested, "binding": False,
                                "formula": "Applicant's request", "explanation": "The recommendation never exceeds the requested amount."}]
    repay_cap = None
    if cash_available is not None and existing_service is not None and rate is not None:
        headroom = cash_available / min_dscr - existing_service
        rc["max_new_annual_debt_service"] = round(headroom, 2)
        if is_wc:
            repay_cap = max(0.0, headroom / (rate / 100.0)) if headroom > 0 else 0.0
            formula = "(Cash available ÷ min DSCR − existing debt service) ÷ interest rate (revolving, interest-only)"
        else:
            tenure_for_calc = max(1, req_tenure - int(app.get("moratorium_months") or 0))
            repay_cap = principal_from_payment(headroom / 12.0, rate, tenure_for_calc) if headroom > 0 else 0.0
            formula = f"Annuity principal supported by (Cash available ÷ {min_dscr}x − existing service) over {tenure_for_calc} months at {rate:.2f}%"
        constraints.append({"key": "repayment_capacity", "name": "Repayment capacity (DSCR)", "value": round(repay_cap, 2),
                            "binding": False, "formula": formula,
                            "explanation": (f"{basis} of {_money(cash_available)} supports total annual debt service of "
                                            f"{_money(cash_available / min_dscr)} at {min_dscr}x DSCR; existing obligations use "
                                            f"{_money(existing_service)}, leaving {_money(max(headroom, 0))} per year for the new facility."),
                            "inputs": {"cash_available": cash_available, "existing_service": existing_service, "rate": rate}})
    else:
        why = []
        if cash_available is None:
            why.append("EBITDA and operating cash flow are unavailable")
        if existing_service is None:
            why.append("existing debt service is unknown")
        if rate is None:
            why.append("the risk band is outside pricing appetite")
        constraints.append({"key": "repayment_capacity", "name": "Repayment capacity (DSCR)", "value": None, "binding": False,
                            "formula": "Not computable", "explanation": "Repayment capacity could not be computed: " + "; ".join(why) + "."})

    equity = _v(inp.current, "shareholders_equity")
    total_debt = _v(inp.current, "total_debt")
    if equity is not None and total_debt is not None:
        lev_cap = max(0.0, equity * float(d["max_post_loan_debt_to_equity"]) - total_debt) if equity > 0 else 0.0
        constraints.append({"key": "leverage", "name": "Post-loan leverage cap", "value": round(lev_cap, 2), "binding": False,
                            "formula": f"Net worth × {d['max_post_loan_debt_to_equity']}x − existing debt",
                            "explanation": (f"Keeps post-loan debt-to-equity at or below {d['max_post_loan_debt_to_equity']}x "
                                            f"(net worth {_money(equity)}, existing debt {_money(total_debt)})."
                                            if equity > 0 else "Net worth is negative — no additional leverage capacity.")})
    haircuts = d["collateral_haircuts"]
    if collateral:
        realisable = sum(float(c.get("value") or 0) * haircuts.get(c.get("type") or "other", haircuts["other"])
                         * (0.5 if c.get("existing_charge") else 1.0) for c in collateral)
        req_cov = d["min_security_coverage"].get(band)
        sec_cap = realisable / req_cov if req_cov else 0.0
        constraints.append({"key": "security", "name": "Security coverage cap", "value": round(sec_cap, 2), "binding": False,
                            "formula": f"Realisable collateral ÷ required coverage ({req_cov}x for {band})" if req_cov else "Not lendable in this risk band",
                            "explanation": f"Realisable collateral after policy haircuts is {_money(realisable)}."})
    elif equity is not None:
        uns_cap = max(0.0, equity * float(d["unsecured_networth_cap"]))
        constraints.append({"key": "unsecured", "name": "Unsecured exposure cap", "value": round(uns_cap, 2), "binding": False,
                            "formula": f"Net worth × {d['unsecured_networth_cap']:.0%}",
                            "explanation": "No collateral offered — unsecured exposure is capped relative to net worth."})
    revenue = _v(inp.current, "revenue_from_operations")
    if is_wc and revenue is not None:
        wc_cap = revenue * float(d["working_capital_turnover_cap"])
        constraints.append({"key": "turnover", "name": "Working-capital turnover cap", "value": round(wc_cap, 2), "binding": False,
                            "formula": f"Revenue × {d['working_capital_turnover_cap']:.0%} (turnover method, prototype)",
                            "explanation": f"Working-capital limits are capped at {d['working_capital_turnover_cap']:.0%} of annual revenue ({_money(revenue)})."})

    numeric = [c for c in constraints if c["value"] is not None]
    binding = min(numeric, key=lambda c: c["value"])
    binding["binding"] = True
    pre_multiplier = binding["value"]
    multiplier = float(d["amount_multipliers"].get(band, 0.0))
    amount = pre_multiplier * multiplier
    rounding = float(d["rounding"]) or 1.0
    amount = math.floor(amount / rounding) * rounding
    amount = max(0.0, min(amount, requested))
    repay_known = any(c["key"] == "repayment_capacity" and c["value"] is not None for c in constraints)
    if not repay_known:
        amount = 0.0

    # ---------------- Tenure ----------------
    max_tenure = int(d["max_tenure_months"].get(band, 0) or 0)
    if is_wc:
        rec_tenure = 12 if max_tenure else None
        tenure_rationale = "Working-capital limits are sanctioned for 12 months, subject to annual review and renewal."
    elif max_tenure:
        rec_tenure = min(req_tenure, max_tenure)
        tenure_rationale = (f"Requested tenure of {req_tenure} months is within the {max_tenure}-month policy maximum for {band}."
                            if req_tenure <= max_tenure else
                            f"Tenure capped at the {max_tenure}-month policy maximum for {band} (requested {req_tenure} months).")
    else:
        rec_tenure = None
        tenure_rationale = f"No tenure recommended — {band} is outside the policy lending appetite."

    # ---------------- DSCR at recommendation ----------------
    if amount > 0 and rate is not None and cash_available is not None and existing_service is not None:
        new_service = amount * rate / 100.0 if is_wc else annuity_payment(amount, rate, max(1, (rec_tenure or req_tenure) - int(app.get("moratorium_months") or 0))) * 12
        rc["new_annual_debt_service"] = round(new_service, 2)
        rc["proposed_dscr"] = round(cash_available / (existing_service + new_service), 2) if existing_service + new_service > 0 else None
        if not is_wc:
            rc["indicative_emi"] = round(new_service / 12.0, 2)

    # ---------------- Outcome ----------------
    crit_flags = [f for f in inp.red_flags if f["severity"] == "critical"]
    high_flags = [f for f in inp.red_flags if f["severity"] == "high"]
    critical_missing = [m for m in inp.missing_information if m.get("importance") == "critical"]
    low_conf = inp.confidence_score < float(d["min_confidence_for_decision"])
    reasons: list[str] = []
    if crit_flags and d.get("critical_flags_force_rejection", True):
        outcome = "reject"
        reasons.append("critical red flag(s): " + "; ".join(f["title"] for f in crit_flags[:3]))
    elif low_conf or critical_missing or not repay_known:
        outcome = "insufficient_information"
        if low_conf:
            reasons.append(f"assessment confidence {inp.confidence_score:.0f}/100 is below the policy minimum of {d['min_confidence_for_decision']}")
        if critical_missing:
            reasons.append("missing critical evidence: " + ", ".join(m["item"].replace("_", " ") for m in critical_missing[:4]))
        if not repay_known:
            reasons.append("repayment capacity could not be computed")
    elif inp.overall_score < float(d["conditional_min_score"]) or premium is None:
        outcome = "reject"
        reasons.append(f"credit score {inp.overall_score:.1f} is below the conditional-approval threshold of {d['conditional_min_score']}")
    elif amount <= 0:
        outcome = "reject"
        reasons.append(f"no lending capacity remains after policy constraints (binding: {binding['name']})")
    else:
        unresolved_recon = [r for r in inp.reconciliations if r["severity"] in ("high", "critical")]
        if (inp.overall_score >= float(d["approve_min_score"]) and not high_flags and not unresolved_recon
                and amount >= 0.9 * requested and inp.confidence_level != "Low"):
            outcome = "approve"
            reasons.append(f"credit score {inp.overall_score:.1f} meets the approval threshold of {d['approve_min_score']} with no high-severity red flags")
        else:
            outcome = "conditional_approval"
            if inp.overall_score < float(d["approve_min_score"]):
                reasons.append(f"credit score {inp.overall_score:.1f} is below the full-approval threshold of {d['approve_min_score']}")
            if high_flags:
                reasons.append("high-severity issue(s) need resolution: " + "; ".join(f["title"] for f in high_flags[:3]))
            if unresolved_recon:
                reasons.append(f"{len(unresolved_recon)} high-severity reconciliation discrepancy(ies) require verification")
            if amount < 0.9 * requested:
                reasons.append(f"recommended amount is limited to {_money(amount)} of {_money(requested)} requested")
            if inp.confidence_level == "Low":
                reasons.append("assessment confidence is low")
    if outcome in ("reject", "insufficient_information"):
        final_amount = 0.0 if outcome == "reject" else amount
    else:
        final_amount = amount

    # ---------------- Factors, conditions & requirements ----------------
    scored = [f for f in inp.factors if f.get("score") is not None and f.get("dimension")]
    positives = [{"factor": f["name"], "score": f["score"], "detail": f["explanation"]}
                 for f in sorted(scored, key=lambda f: -f["score"]) if f["score"] >= 75][:6]
    negatives = [{"factor": f["name"], "score": f["score"], "detail": f["explanation"]}
                 for f in sorted(scored, key=lambda f: f["score"]) if f["score"] < 50][:6]
    warnings = [f"[{f['severity'].upper()}] {f['title']}: {f['detail']}" for f in inp.red_flags if f["severity"] in ("critical", "high")]
    if outcome == "insufficient_information":
        warnings.insert(0, "Insufficient information: " + "; ".join(reasons) + ". This is not an approval.")

    conditions = _conditions(inp, policy, final_amount, requested, rate, collateral, outcome)
    coll_reqs = _collateral_requirements(inp, policy, final_amount, collateral, band, is_wc)
    repay_notes = _repayment_notes(rc, rate, rec_tenure, is_wc, app, policy)

    explanation = _explain(outcome, inp, final_amount, requested, binding, multiplier, band, base_rate, premium, adjustments,
                           rate, positives, negatives, reasons, is_wc)
    return DecisionResult(
        outcome=outcome, outcome_label=OUTCOME_LABELS[outcome], requested_amount=requested,
        recommended_amount=final_amount, amount_constraints=constraints, base_rate=base_rate,
        risk_premium=float(premium) if premium is not None else None, rate_adjustments=adjustments,
        indicative_rate=rate, requested_tenure_months=req_tenure,
        recommended_tenure_months=rec_tenure if outcome in ("approve", "conditional_approval") else None,
        tenure_rationale=tenure_rationale, repayment_capacity=rc, collateral_requirements=coll_reqs,
        repayment_considerations=repay_notes, conditions_precedent=conditions, positive_factors=positives,
        negative_factors=negatives, critical_warnings=warnings, explanation=explanation,
    )


def _conditions(inp: DecisionInputs, policy: dict, amount: float, requested: float, rate, collateral, outcome) -> list[str]:
    th = policy["thresholds"]
    out: list[str] = []
    if outcome == "reject":
        return ["Not applicable — rejection recommended. A fresh proposal may be considered once the cited issues are resolved."]
    for m in inp.missing_information:
        if m["type"] == "document" and m["importance"] == "critical":
            out.append(f"Submission and review of {m['item'].replace('_', ' ')}.")
        if m["type"] == "recency":
            out.append("Submission of provisional/latest financial statements for the current year.")
    for r in inp.reconciliations:
        if r["severity"] in ("high", "medium") and r["status"] != "consistent":
            out.append(f"Satisfactory explanation and evidence for: {r['metric']}" +
                       (f" — {r['suggested_investigation']}" if r.get("suggested_investigation") else "") + ".")
    for f in inp.research_findings:
        if f["severity"] in ("high", "critical") and f.get("sentiment") == "adverse" and f.get("verification_status") != "disputed" \
                and f.get("include_in_assessment", True):
            out.append(f"Legal/independent verification of: {f['title'][:140]}.")
    for o in inp.observations:
        if o.get("risk_impact") == "negative" and o.get("reviewer_status") == "pending" and o["severity"] in ("high", "medium", "critical"):
            out.append(f"Independent verification of site/management observation: {o['title'][:140]}.")
    if collateral:
        out.append("Creation and perfection of charge over the offered security, with independent valuation by an approved valuer "
                   "and insurance with the lender's clause.")
    guarantors = inp.application.get("guarantors") or []
    if guarantors:
        out.append("Execution of guarantees by " + ", ".join(g.get("name", "guarantor") for g in guarantors) + ".")
    if amount < requested and amount > 0:
        out.append(f"Sanction limited to {_money(amount)} against the request of {_money(requested)}.")
    out.append(f"Financial covenants (tested annually): DSCR ≥ {policy['decision']['min_dscr']}x, debt-to-equity ≤ "
               f"{policy['decision']['max_post_loan_debt_to_equity']}x, current ratio ≥ {th['current_ratio_min']}x.")
    out.append("No further borrowings without the lender's prior consent; quarterly stock and receivables statements.")
    seen, dedup = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return dedup


def _collateral_requirements(inp, policy, amount, collateral, band, is_wc) -> list[dict]:
    d = policy["decision"]
    req_cov = d["min_security_coverage"].get(band)
    haircuts = d["collateral_haircuts"]
    out = [{"type": "primary", "description": ("Hypothecation of current assets (stocks and book debts)" if is_wc
                                               else "Hypothecation/mortgage of assets financed from the loan"),
            "status": "required"}]
    if req_cov and amount > 0:
        needed = amount * req_cov
        realisable = sum(float(c.get("value") or 0) * haircuts.get(c.get("type") or "other", haircuts["other"])
                         * (0.5 if c.get("existing_charge") else 1.0) for c in collateral)
        out.append({"type": "coverage", "description": f"Minimum realisable security of {_money(needed)} ({req_cov}x of recommended amount for {band}).",
                    "required_value": round(needed, 2), "offered_realisable_value": round(realisable, 2),
                    "shortfall": round(max(0.0, needed - realisable), 2),
                    "status": "met" if realisable >= needed else "shortfall"})
    for c in collateral:
        hc = haircuts.get(c.get("type") or "other", haircuts["other"])
        out.append({"type": "collateral", "description": f"{c.get('description') or c.get('type')}: declared value {_money(float(c.get('value') or 0))}, "
                    f"policy haircut {1 - hc:.0%}" + (" (existing charge — second charge only)" if c.get("existing_charge") else ""),
                    "status": "offered"})
    return out


def _repayment_notes(rc, rate, tenure, is_wc, app, policy) -> list[str]:
    notes = []
    if rc.get("proposed_dscr") is not None:
        notes.append(f"Projected DSCR at the recommended amount is {rc['proposed_dscr']:.2f}x (policy minimum {policy['decision']['min_dscr']}x), "
                     f"based on {rc['basis']} for {rc.get('period') or 'the latest period'}.")
    if rc.get("indicative_emi"):
        notes.append(f"Indicative EMI of {_money(rc['indicative_emi'])} per month over {tenure} months at {rate:.2f}% (illustrative).")
    if is_wc:
        notes.append("Revolving limit: interest serviced monthly; drawing power to be computed on stock and receivable statements.")
    if int(app.get("moratorium_months") or 0) > 0:
        notes.append(f"Requested moratorium of {app['moratorium_months']} month(s); interest to be serviced during moratorium.")
    if rc.get("existing_service_assumed"):
        notes.append("Existing debt service partly relies on policy assumptions (see notes); obtain lender-wise repayment schedules.")
    if rc.get("cash_available_for_debt_service") is not None and rc["cash_available_for_debt_service"] <= 0:
        notes.append("Cash available for debt service is negative — repayment depends on external support.")
    return notes


STRENGTH_PHRASES = {
    "revenue_growth": "revenue growth", "net_worth_position": "net worth", "profit_trend": "profitability",
    "external_rating": "external credit rating", "current_ratio": "liquidity", "quick_ratio": "liquidity",
    "ocf_to_debt": "operating cash flow", "cash_conversion": "cash conversion", "receivable_days": "collection efficiency",
    "debt_to_equity": "low leverage", "debt_to_assets": "low leverage", "debt_to_ebitda": "debt-to-earnings position",
    "interest_coverage": "interest coverage", "ebitda_margin": "operating margins", "net_profit_margin": "net margins",
    "roe": "return on equity", "roa": "return on assets", "management_assessment": "management assessment",
    "governance_signals": "governance record", "promoter_holding": "promoter commitment",
    "promoter_pledge": "unencumbered promoter shareholding", "litigation": "clean litigation screening",
    "regulatory": "regulatory record", "industry_risk": "sector profile", "capacity_utilisation": "capacity utilisation",
    "site_visit": "site-visit findings", "reconciliation": "consistent cross-source reconciliation",
}


def _explain(outcome, inp, amount, requested, binding, multiplier, band, base_rate, premium, adjustments, rate,
             positives, negatives, reasons, is_wc) -> dict:
    label = OUTCOME_LABELS[outcome]
    scored = sorted([f for f in inp.factors if f.get("score") is not None and f.get("dimension") and f["score"] >= 75],
                    key=lambda f: (-(f["score"] * f.get("weight", 1.0))))
    pos = STRENGTH_PHRASES.get(scored[0]["key"], scored[0]["name"].lower()) if scored else None
    negs = [n["factor"].lower() for n in negatives[:2]]
    flag_titles = [f["title"] for f in inp.red_flags if f["severity"] in ("critical", "high")][:2]
    because = "; ".join(reasons) if reasons else ""
    headline = label
    if outcome == "conditional_approval":
        headline = "Conditional approval recommended" + (f" despite strong {pos}" if pos else "") + (f" because {because}" if because else "") + "."
    elif outcome == "approve":
        headline = "Approval recommended because " + because + (f"; key strengths include {pos}" if pos else "") + "."
    elif outcome == "reject":
        headline = "Rejection recommended because " + because + "."
    else:
        headline = "Manual review required: " + because + "."
    if amount > 0:
        why_amount = (f"Recommended {_money(amount)} against {_money(requested)} requested. The binding constraint is "
                      f"'{binding['name']}' at {_money(binding['value'])}; a {multiplier:.0%} risk-band multiplier for {band} "
                      f"was applied and the result rounded down. " + binding.get("explanation", ""))
    else:
        why_amount = ("No amount is recommended because " + (because or "policy constraints leave no lending capacity") + ".")
    if rate is not None:
        adj_txt = ", ".join(f"{a['name']} {a['value']:+.2f}%" for a in adjustments) or "no adjustments"
        why_rate = (f"Indicative rate {rate:.2f}% = illustrative base rate {base_rate:.2f}% + {band} risk premium {premium:.2f}% "
                    f"({adj_txt}). This is a prototype calculation, not a bank offer.")
    else:
        why_rate = f"No rate is quoted because {band} is outside the configured pricing appetite."
    return {
        "headline": headline,
        "why_outcome": f"{label}: " + (because[:1].upper() + because[1:] + "." if because else "policy thresholds met."),
        "why_amount": why_amount,
        "why_rate": why_rate,
        "key_positives": [p["factor"] for p in positives[:3]],
        "key_negatives": negs,
        "critical_issues": flag_titles,
        "disclaimer": "AI-assisted recommendation generated from deterministic policy rules for review by an authorised credit professional.",
    }
