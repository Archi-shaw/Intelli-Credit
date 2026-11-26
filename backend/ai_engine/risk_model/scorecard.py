"""Transparent policy scorecard (the active prototype credit model).

Pipeline: factors → nine weighted dimensions → overall 0-100 score → rating band.
Separately: twelve risk categories, the Five Cs, red flags, missing information and a confidence
indicator. Missing evidence is never treated as favourable: a dimension without evidence receives
a conservative policy score and lowers confidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from ai_engine.nlp.indian_terms import RATING_SCALE
from ai_engine.risk_model.policy import DIMENSIONS, RISK_CATEGORIES, band_for, curve_score
from ai_engine.risk_model.ratios import RatioResult

MODEL_NAME = "Intelli-Credit Prototype Policy Scorecard"
MODEL_VERSION = "1.0"


@dataclass
class AssessmentInputs:
    company: dict
    application: dict
    latest_period: str | None
    latest_period_end: date | None
    current: dict[str, dict]
    prior: dict[str, dict] | None
    ratios: dict[str, RatioResult]
    documents: list[dict]
    metric_confidences: list[float]
    verified_metric_share: float
    signals: list[dict]
    ratings: list[dict]
    reconciliations: list[dict]
    research_mode: str  # live | synthetic | not_configured | not_run
    research_findings: list[dict]
    observations: list[dict]
    shareholding: dict
    as_of: date


@dataclass
class Factor:
    key: str
    name: str
    dimension: str | None
    risk_category: str
    five_cs: list[str]
    score: float | None
    status: str  # ok | missing | derived
    display_value: str
    explanation: str
    weight: float = 1.0
    value: float | str | None = None
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["score"] is not None:
            d["score"] = round(d["score"], 1)
        return d


@dataclass
class ScorecardResult:
    overall_score: float
    risk_category: str
    dimensions: list[dict]
    risk_categories: dict
    five_cs: dict
    confidence: dict
    red_flags: list[dict]
    missing_information: list[dict]
    document_coverage: dict
    data_completeness: float
    summary: str
    factors: list[dict]
    model_label: str
    features: dict


def _fmt_ratio(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "days":
        return f"{v:.0f} days"
    return f"{v:.2f}x"


def _money(v: float | None) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def _metric_evidence(inp: AssessmentInputs, *keys: str) -> list[dict]:
    ev = []
    for k in keys:
        item = inp.current.get(k)
        if item and item.get("value") is not None:
            ev.append({"type": "metric", "metric": k, "value": item["value"], "document_id": item.get("document_id"),
                       "page_number": item.get("page_number"), "metric_id": item.get("metric_id"),
                       "derived_from": item.get("derived_from"), "period": inp.latest_period})
    return ev


class _Builder:
    def __init__(self, inp: AssessmentInputs, policy: dict):
        self.inp = inp
        self.p = policy
        self.curves = policy["factor_curves"]
        self.th = policy["thresholds"]
        self.factors: list[Factor] = []
        self.red_flags: list[dict] = []

    # ---- helpers --------------------------------------------------------------------------------
    def ratio_factor(self, ratio_key: str, curve_key: str, dimension: str, risk_cat: str, five_cs: list[str],
                     weight: float = 1.0, name: str | None = None):
        r = self.inp.ratios.get(ratio_key)
        nm = name or (r.name if r else ratio_key.replace("_", " ").title())
        if r is None or r.status == "missing_inputs":
            self.factors.append(Factor(ratio_key, nm, dimension, risk_cat, five_cs, None, "missing", "n/a",
                                       r.interpretation if r else "Not calculated — inputs not available.", weight))
            return None
        if r.status == "negative_denominator" and ratio_key in ("debt_to_equity", "roe", "debt_to_ebitda", "cash_conversion"):
            self.factors.append(Factor(ratio_key, nm, dimension, risk_cat, five_cs, 0.0, "ok", "negative base",
                                       r.interpretation, weight, None, _ratio_ev(r)))
            return 0.0
        if r.status != "ok" or r.value is None:
            self.factors.append(Factor(ratio_key, nm, dimension, risk_cat, five_cs, None, "missing", "n/a", r.interpretation, weight))
            return None
        score = curve_score(self.curves[curve_key], r.value)
        derived = any(self.inp.current.get(k, {}).get("derived_from") for k in (r.inputs or {}) if k in self.inp.current)
        self.factors.append(Factor(ratio_key, nm, dimension, risk_cat, five_cs, score, "derived" if derived else "ok",
                                   _fmt_ratio(r.value, r.unit), r.interpretation, weight, r.value, _ratio_ev(r)))
        return score

    def qualitative(self, key: str, name: str, dimension: str | None, risk_cat: str, five_cs: list[str],
                    items: list[dict], evidence_available: bool, missing_reason: str, weight: float = 1.0,
                    baseline: float | None = None, clean_text: str = "No adverse evidence was found."):
        """Baseline minus severity-weighted deductions; each item: {severity, weight, label, evidence}."""
        if not evidence_available and not items:
            self.factors.append(Factor(key, name, dimension, risk_cat, five_cs, None, "missing", "not assessed",
                                       missing_reason, weight))
            return None
        base = self.p["qualitative_baseline"] if baseline is None else baseline
        sev_scores = self.p["severity_scores"]
        score = base
        parts = []
        adverse = sorted([i for i in items if i["severity"] != "positive" and i["weight"] > 0],
                         key=lambda i: sev_scores.get(i["severity"], 85))
        for n, it in enumerate(adverse):
            target = sev_scores.get(it["severity"], 85)
            if target >= base:
                continue
            wt = it["weight"]
            ded = (base - target) * wt / (1 + 0.5 * n)
            score -= ded
            suffix = "" if wt >= 1 else f", weight {wt:.1f}"
            parts.append(f"−{ded:.0f} {it['label']} ({it['severity']}{suffix})")
        for it in [i for i in items if i["severity"] == "positive" and i["weight"] > 0]:
            score += 5 * it["weight"]
            parts.append(f"+{5 * it['weight']:.0f} {it['label']}")
        score = max(0.0, min(95.0, score))
        expl = f"Baseline {base:.0f}" + ("; " + "; ".join(parts) if parts else f" — {clean_text}")
        ev = [e for i in items for e in i.get("evidence", [])]
        self.factors.append(Factor(key, name, dimension, risk_cat, five_cs, score, "ok",
                                   f"{len(adverse)} adverse item(s)" if adverse else "no adverse items", expl, weight,
                                   len(adverse), ev))
        return score

    def flag(self, severity: str, title: str, detail: str, source: str, evidence: list[dict] | None = None):
        self.red_flags.append({"severity": severity, "title": title, "detail": detail, "source": source,
                               "evidence": evidence or []})


def _ratio_ev(r: RatioResult) -> list[dict]:
    return [{"type": "ratio", "ratio": r.key, "value": r.value, "formula": r.formula, "inputs": r.inputs,
             "period": r.period, "sources": r.sources}]


def _obs_weight(o: dict, policy: dict) -> float:
    return policy["verification_weights"]["observations"].get(o.get("reviewer_status", "pending"), 0.5)


def _research_weight(f: dict, policy: dict) -> float:
    if not f.get("include_in_assessment", True):
        return 0.0
    return policy["verification_weights"]["research"].get(f.get("verification_status", "unverified"), 0.6)


def _obs_items(obs: list[dict], policy: dict, *, sub_categories: set[str] | None = None, category: str | None = None):
    items = []
    for o in obs:
        if category and o["category"] != category:
            continue
        if sub_categories and o["sub_category"] not in sub_categories:
            continue
        sev = o["severity"]
        if o.get("risk_impact") == "positive":
            sev = "positive"
        elif o.get("risk_impact") == "neutral" and sev in ("info", "low"):
            continue
        items.append({"severity": sev, "weight": _obs_weight(o, policy), "label": o["title"][:80],
                      "evidence": [{"type": "observation", "observation_id": o["id"], "title": o["title"],
                                    "reviewer_status": o.get("reviewer_status")}]})
    return items


def _research_items(findings: list[dict], policy: dict, categories: set[str]):
    items = []
    for f in findings:
        if f["category"] not in categories:
            continue
        sev = "positive" if f.get("sentiment") == "positive" else f["severity"]
        if f.get("sentiment") == "neutral" and sev in ("info", "low"):
            continue
        items.append({"severity": sev, "weight": _research_weight(f, policy), "label": f["title"][:80],
                      "evidence": [{"type": "research", "finding_id": f["id"], "title": f["title"],
                                    "source_name": f.get("source_name"), "source_url": f.get("source_url"),
                                    "verification_status": f.get("verification_status")}]})
    return items


def _signal_items(signals: list[dict], policy: dict, areas: set[str]):
    w = policy["verification_weights"]["document_signals"]
    items = []
    for s in signals:
        if s["area"] not in areas:
            continue
        items.append({"severity": s["severity"], "weight": w, "label": s["label"],
                      "evidence": [{"type": "document_signal", "document_id": s.get("document_id"),
                                    "page_number": s.get("page_number"), "excerpt": s.get("excerpt")}]})
    return items


def run_scorecard(inp: AssessmentInputs, policy: dict) -> ScorecardResult:
    b = _Builder(inp, policy)
    th = policy["thresholds"]
    cur = inp.current

    def mval(k):
        it = cur.get(k)
        return None if it is None or it.get("value") is None else float(it["value"])

    # ---------------- Financial health ----------------
    b.ratio_factor("revenue_growth", "revenue_growth", "financial_health", "financial", ["capacity"], 1.0)
    eq = mval("shareholders_equity")
    if eq is None:
        b.factors.append(Factor("net_worth_position", "Net worth position", "financial_health", "financial", ["capital"],
                                None, "missing", "n/a", "Shareholders' equity was not extracted.", 1.2))
    else:
        prior_eq = None
        if inp.prior and inp.prior.get("shareholders_equity", {}).get("value") is not None:
            prior_eq = float(inp.prior["shareholders_equity"]["value"])
        if eq <= 0:
            score, expl = 0.0, f"Net worth is negative ({_money(eq)}) — accumulated losses exceed capital."
            b.flag("critical", "Negative net worth", expl, "financial_statements", _metric_evidence(inp, "shareholders_equity"))
        else:
            score, expl = 72.0, f"Positive net worth of {_money(eq)}."
            if prior_eq is not None and prior_eq > 0:
                g = (eq - prior_eq) / prior_eq * 100
                score += 13 if g > 5 else (0 if g > -5 else -17)
                expl += f" Net worth {'grew' if g >= 0 else 'declined'} {abs(g):.1f}% year on year."
        b.factors.append(Factor("net_worth_position", "Net worth position", "financial_health", "financial", ["capital"],
                                score, "ok", _money(eq), expl, 1.2, eq, _metric_evidence(inp, "shareholders_equity")))
    pat = mval("pat")
    if pat is None:
        b.factors.append(Factor("profit_trend", "Profit after tax trend", "financial_health", "financial", ["capacity"],
                                None, "missing", "n/a", "Profit after tax was not extracted.", 1.0))
    else:
        g = inp.ratios.get("pat_growth")
        if pat < 0:
            score, expl = 12.0, f"The company reported a loss of {_money(pat)}."
            b.flag("high", "Loss-making in latest period", expl, "financial_statements", _metric_evidence(inp, "pat"))
        else:
            score, expl = 70.0, f"Profit after tax of {_money(pat)}."
            if g and g.value is not None:
                score += 15 if g.value > 10 else (5 if g.value >= 0 else (-12 if g.value > -25 else -28))
                expl += f" PAT {'grew' if g.value >= 0 else 'declined'} {abs(g.value):.1f}% year on year."
        b.factors.append(Factor("profit_trend", "Profit after tax trend", "financial_health", "financial", ["capacity"],
                                score, "ok", _money(pat), expl, 1.0, pat, _metric_evidence(inp, "pat")))
    b.ratio_factor("contingent_to_networth", "contingent_to_networth", "financial_health", "financial", ["capital"], 0.6)

    # External credit rating (document)
    if inp.ratings:
        best = None
        for r in inp.ratings:
            grade = (r.get("text_value") or "").split(" ")[1] if len((r.get("text_value") or "").split(" ")) > 1 else None
            if grade and grade.upper() in RATING_SCALE:
                best = (grade.upper(), r)
                break
        if best:
            grade, r = best
            score = float(policy["credit_rating_scores"].get(grade, 50))
            outlook = "Negative" in (r.get("text_value") or "")
            if outlook:
                score -= 8
            expl = f"External long-term rating {r['text_value']}." + (" Negative outlook." if outlook else "")
            if RATING_SCALE.index(grade) >= RATING_SCALE.index("BB+"):
                b.flag("critical" if grade == "D" else "medium", "Sub-investment-grade external rating", expl,
                       "credit_rating_reports", [{"type": "document", "document_id": r.get("document_id")}])
            b.factors.append(Factor("external_rating", "External credit rating", "financial_health", "financial",
                                    ["character", "capacity"], score, "ok", r["text_value"], expl, 0.8, grade,
                                    [{"type": "document", "document_id": r.get("document_id"), "page_number": r.get("page_number")}]))
    else:
        b.factors.append(Factor("external_rating", "External credit rating", "financial_health", "financial",
                                ["character", "capacity"], None, "missing", "not available",
                                "No credit rating report was provided or no rating grade could be extracted.", 0.8))

    research_ran = inp.research_mode in ("live", "synthetic")
    distress_items = _research_items(inp.research_findings, policy, {"financial_distress", "credit_rating"}) + \
        _signal_items(inp.signals, policy, {"financial_distress", "credit_rating"})
    b.qualitative("external_distress", "External distress & rating signals", "financial_health", "financial",
                  ["character", "conditions"], distress_items, research_ran,
                  "External research was not performed; distress signals could not be screened.", 0.8)

    # ---------------- Liquidity & cash flow ----------------
    b.ratio_factor("current_ratio", "current_ratio", "liquidity_cash_flow", "liquidity", ["capacity"], 1.2)
    b.ratio_factor("quick_ratio", "quick_ratio", "liquidity_cash_flow", "liquidity", ["capacity"], 0.8)
    b.ratio_factor("ocf_to_debt", "ocf_to_debt", "liquidity_cash_flow", "cash_flow", ["capacity"], 1.0)
    b.ratio_factor("cash_conversion", "cash_conversion", "liquidity_cash_flow", "cash_flow", ["capacity"], 0.8)
    b.ratio_factor("receivable_days", "receivable_days", "liquidity_cash_flow", "liquidity", ["capacity"], 0.6)
    cr = inp.ratios.get("current_ratio")
    if cr and cr.value is not None and cr.value < 1.0:
        b.flag("high", "Current ratio below 1.0x", cr.interpretation, "financial_statements", _ratio_ev(cr))
    ocf = mval("operating_cash_flow")
    if ocf is not None and ocf < 0:
        b.flag("medium", "Negative operating cash flow", f"Operating cash flow of {_money(ocf)} in {inp.latest_period}.",
               "financial_statements", _metric_evidence(inp, "operating_cash_flow"))

    # ---------------- Leverage & debt servicing ----------------
    b.ratio_factor("debt_to_equity", "debt_to_equity", "leverage_debt_servicing", "leverage", ["capital"], 1.2)
    b.ratio_factor("debt_to_assets", "debt_to_assets", "leverage_debt_servicing", "leverage", ["capital"], 0.6)
    b.ratio_factor("debt_to_ebitda", "debt_to_ebitda", "leverage_debt_servicing", "leverage", ["capacity"], 1.0)
    b.ratio_factor("interest_coverage", "interest_coverage", "leverage_debt_servicing", "leverage", ["capacity"], 1.2)
    de = inp.ratios.get("debt_to_equity")
    if de and de.value is not None and de.value > th["debt_to_equity_max"]:
        b.flag("high", "Leverage above policy maximum", de.interpretation, "financial_statements", _ratio_ev(de))
    icr = inp.ratios.get("interest_coverage")
    if icr and icr.value is not None and icr.value < 1.0:
        b.flag("high", "Interest not covered by operating profit", icr.interpretation, "financial_statements", _ratio_ev(icr))

    # ---------------- Profitability ----------------
    b.ratio_factor("ebitda_margin", "ebitda_margin", "profitability_performance", "profitability", ["capacity"], 1.2)
    b.ratio_factor("net_profit_margin", "net_profit_margin", "profitability_performance", "profitability", ["capacity"], 1.0)
    b.ratio_factor("roe", "roe", "profitability_performance", "profitability", ["capital"], 0.8)
    b.ratio_factor("roa", "roa", "profitability_performance", "profitability", ["capacity"], 0.8)

    # ---------------- Governance & management ----------------
    mgmt_obs = [o for o in inp.observations if o["category"] == "management_assessment"]
    b.qualitative("management_assessment", "Management assessment (due diligence)", "governance_management", "management",
                  ["character"], _obs_items(mgmt_obs, policy), bool(mgmt_obs),
                  "No management assessment has been recorded by a credit officer.", 1.2,
                  clean_text="Recorded management observations raise no concerns.")
    gov_items = _signal_items(inp.signals, policy, {"governance"}) + _research_items(
        inp.research_findings, policy, {"promoter_intelligence", "corporate_news"})
    gov_evidence = research_ran or any(d["category"] in ("annual_reports", "board_minutes", "financial_statements")
                                       for d in inp.documents)
    b.qualitative("governance_signals", "Governance & promoter signals", "governance_management", "governance",
                  ["character"], gov_items, gov_evidence,
                  "Neither external research nor governance documents (annual report, board minutes) were available.", 1.0)
    php = inp.shareholding.get("promoter_holding_pct")
    if php is not None:
        sc = curve_score(policy["factor_curves"]["promoter_holding_pct"], php)
        b.factors.append(Factor("promoter_holding", "Promoter shareholding", "governance_management", "governance",
                                ["capital", "character"], sc, "ok", f"{php:.1f}%",
                                f"Promoters and promoter group hold {php:.1f}% of equity (skin in the game).", 0.6, php,
                                inp.shareholding.get("evidence", [])))
    else:
        b.factors.append(Factor("promoter_holding", "Promoter shareholding", "governance_management", "governance",
                                ["capital", "character"], None, "missing", "n/a",
                                "Shareholding pattern not provided.", 0.6))
    pledge = inp.shareholding.get("promoter_pledge_pct")
    if pledge is not None:
        sc = curve_score(policy["factor_curves"]["promoter_pledge_pct"], pledge)
        b.factors.append(Factor("promoter_pledge", "Promoter share pledge", "governance_management", "governance",
                                ["character"], sc, "ok", f"{pledge:.1f}%", f"{pledge:.1f}% of promoter shares are pledged.", 0.6, pledge))
        if pledge > th["promoter_pledge_max"]:
            b.flag("high", "High promoter share pledge", f"{pledge:.1f}% of promoter holding is pledged.", "shareholding_disclosures")
    b.ratio_factor("rpt_to_revenue", "rpt_to_revenue", "governance_management", "governance", ["character"], 0.6)

    # ---------------- Legal & regulatory ----------------
    legal_items = _research_items(inp.research_findings, policy, {"legal_litigation"}) + _signal_items(inp.signals, policy, {"legal"})
    has_legal_docs = any(d["category"] == "legal_notices" for d in inp.documents)
    b.qualitative("litigation", "Litigation & legal proceedings", "legal_regulatory", "legal", ["character"], legal_items,
                  research_ran or has_legal_docs,
                  "Litigation screening not performed (no research provider configured and no legal documents).", 1.2)
    reg_items = _research_items(inp.research_findings, policy, {"regulatory"}) + _signal_items(inp.signals, policy, {"regulatory", "compliance"})
    b.qualitative("regulatory", "Regulatory & tax compliance", "legal_regulatory", "compliance", ["character", "conditions"],
                  reg_items, research_ran, "Regulatory screening not performed (research provider not configured).", 0.8)

    # ---------------- Industry & business ----------------
    ind_cfg = policy["industry_risk"]
    industry = inp.company.get("industry") or ""
    level = ind_cfg["map"].get(industry, ind_cfg["default"])
    ind_score = float(ind_cfg["levels"][level])
    b.factors.append(Factor("industry_risk", "Sector risk classification", "industry_business", "industry", ["conditions"],
                            ind_score, "ok", f"{industry or 'Unspecified'} ({level.replace('_', ' ')})",
                            f"Policy classifies '{industry or 'unspecified'}' as {level.replace('_', ' ')} sector risk"
                            + ("" if industry in ind_cfg["map"] else " (default classification — sector not in policy table)") + ".",
                            1.0, level))
    outlook_items = _research_items(inp.research_findings, policy, {"industry_outlook"})
    b.qualitative("industry_outlook", "Industry outlook (research)", "industry_business", "industry", ["conditions"],
                  outlook_items, research_ran, "Industry research not performed.", 0.8,
                  clean_text="No adverse sector developments identified.")
    env_obs = [o for o in inp.observations if o["category"] == "business_environment"]
    b.qualitative("business_concentration", "Business concentration & environment", "industry_business", "industry",
                  ["conditions"], _obs_items(env_obs, policy), bool(env_obs),
                  "No business-environment observations (customer/supplier concentration) recorded.", 0.8)

    # ---------------- Operational due diligence ----------------
    util = [o for o in inp.observations if o["sub_category"] in ("factory_utilization", "capacity_utilisation")
            and o.get("numeric_value") is not None and o.get("reviewer_status") != "disputed"]
    if util:
        o = sorted(util, key=lambda x: (x.get("reviewer_status") == "verified", x["id"]))[-1]
        val = float(o["numeric_value"])
        raw = curve_score(policy["factor_curves"]["capacity_utilisation"], val)
        w = _obs_weight(o, policy)
        # Unverified observations move the score only partially away from neutral (50).
        score = 50 + (raw - 50) * w
        expl = (f"Factory operating at {val:.0f}% capacity per site visit "
                f"({'verified' if o['reviewer_status'] == 'verified' else 'pending verification — partial weight'}).")
        if val < th["capacity_utilisation_min"]:
            expl += f" Below the {th['capacity_utilisation_min']}% policy threshold — potential operational risk."
            if val < 45:
                b.flag("high" if o["reviewer_status"] == "verified" else "medium", "Low capacity utilisation", expl,
                       "primary_due_diligence", [{"type": "observation", "observation_id": o["id"]}])
        b.factors.append(Factor("capacity_utilisation", "Capacity utilisation", "operational_due_diligence", "operational",
                                ["capacity", "conditions"], score, "ok", f"{val:.0f}%", expl, 1.2, val,
                                [{"type": "observation", "observation_id": o["id"], "title": o["title"]}]))
    else:
        b.factors.append(Factor("capacity_utilisation", "Capacity utilisation", "operational_due_diligence", "operational",
                                ["capacity", "conditions"], None, "missing", "not recorded",
                                "No factory capacity-utilisation observation recorded.", 1.2))
    fac_obs = [o for o in inp.observations if o["category"] == "factory_inspection"
               and o["sub_category"] not in ("factory_utilization", "capacity_utilisation")]
    b.qualitative("site_visit", "Site visit findings", "operational_due_diligence", "operational", ["capacity"],
                  _obs_items(fac_obs, policy) + _signal_items(inp.signals, policy, {"operational"}), bool(fac_obs),
                  "No factory inspection observations recorded.", 1.0, clean_text="Site visit findings are satisfactory.")

    # ---------------- Reconciliation & compliance ----------------
    rec = inp.reconciliations
    usable = [r for r in rec if r["status"] != "insufficient_data"]
    if not usable:
        b.factors.append(Factor("reconciliation", "Cross-source reconciliation", "reconciliation_compliance", "compliance",
                                ["character", "capacity"], None, "missing", "not performed",
                                "Reconciliation has not been run or no comparable data was available.", 1.5))
    else:
        score = float(policy["reconciliation_baseline"])
        parts = []
        deductions = policy["reconciliation_deductions"]
        for r in sorted(usable, key=lambda r: deductions.get(r["severity"], 0), reverse=True):
            d = deductions.get(r["severity"], 0) * max(0.3, float(r.get("confidence") or 0.5))
            if d > 0:
                score -= d
                parts.append(f"−{d:.0f} {r['metric']} ({r['severity']})")
            if r["severity"] in ("high", "critical"):
                b.flag(r["severity"], r["metric"], r["description"], "reconciliation",
                       [{"type": "reconciliation", "reconciliation_id": r.get("id")}])
        score = max(0.0, min(95.0, score))
        expl = f"Baseline {policy['reconciliation_baseline']} across {len(usable)} completed check(s)" + (
            "; " + "; ".join(parts) if parts else " — all checks consistent.")
        b.factors.append(Factor("reconciliation", "Cross-source reconciliation", "reconciliation_compliance", "compliance",
                                ["character", "capacity"], score, "ok", f"{len(usable)} checks", expl, 1.5,
                                len(parts), [{"type": "reconciliation", "reconciliation_id": r.get("id"), "metric": r["metric"],
                                              "severity": r["severity"]} for r in usable if r["severity"] != "info"]))
    tax_items = _signal_items(inp.signals, policy, {"compliance"})
    if tax_items:
        b.qualitative("tax_disputes", "Tax demands & disputes (documents)", "reconciliation_compliance", "compliance",
                      ["character"], tax_items, True, "", 0.6)

    # ---------------- Critical qualitative red flags ----------------
    for s in inp.signals:
        if s["severity"] == "critical":
            b.flag("critical", s["label"], s.get("excerpt") or s["label"], "documents",
                   [{"type": "document_signal", "document_id": s.get("document_id"), "page_number": s.get("page_number")}])
    for f in inp.research_findings:
        if f["severity"] in ("critical", "high") and f.get("sentiment") == "adverse" and _research_weight(f, policy) > 0:
            b.flag(f["severity"], f["title"][:120], (f.get("summary") or "")[:300], "research",
                   [{"type": "research", "finding_id": f["id"], "source_url": f.get("source_url"),
                     "verification_status": f.get("verification_status")}])
    for o in inp.observations:
        if o["severity"] in ("critical", "high") and o.get("risk_impact") == "negative" and o.get("reviewer_status") != "disputed" \
                and o["sub_category"] not in ("factory_utilization", "capacity_utilisation"):
            b.flag(o["severity"] if o.get("reviewer_status") == "verified" else "medium", o["title"][:120],
                   o.get("interpretation") or o.get("notes", "")[:300], "primary_due_diligence",
                   [{"type": "observation", "observation_id": o["id"]}])

    # ---------------- Aggregate dimensions ----------------
    weights = policy["weights"]
    dims = []
    total = 0.0
    for pos, (dkey, dname) in enumerate(DIMENSIONS):
        fs = [f for f in b.factors if f.dimension == dkey]
        avail = [f for f in fs if f.score is not None]
        if avail:
            score = sum(f.score * f.weight for f in avail) / sum(f.weight for f in avail)
            status = "complete" if len(avail) == len(fs) else "partial"
        else:
            score = float(policy["missing_dimension_score"])
            status = "insufficient"
        w = float(weights[dkey])
        weighted = score * w / 100.0
        total += weighted
        dims.append({"key": dkey, "name": dname, "position": pos, "weight": w, "score": round(score, 1),
                     "weighted_score": round(weighted, 2), "data_status": status,
                     "factors": [f.to_dict() for f in fs],
                     "explanation": _dimension_explanation(dname, score, fs, status, policy)})
    overall = round(total, 1)
    band = band_for(overall, policy)

    # ---------------- Risk categories ----------------
    risk_categories = {}
    for rkey, rname in RISK_CATEGORIES:
        if rkey == "data_quality":
            continue
        fs = [f for f in b.factors if f.risk_category == rkey and f.score is not None]
        all_fs = [f for f in b.factors if f.risk_category == rkey]
        risk_categories[rkey] = {
            "name": rname,
            "score": round(sum(f.score * f.weight for f in fs) / sum(f.weight for f in fs), 1) if fs else None,
            "factors": [f.key for f in all_fs], "assessed_factors": len(fs), "total_factors": len(all_fs),
        }

    # ---------------- Collateral (facility-level; feeds Five Cs and decision engine) ----------------
    collateral_factors = _collateral_factors(inp, policy)
    b.factors.extend(collateral_factors)

    # ---------------- Confidence & coverage ----------------
    confidence, coverage, completeness, missing = _confidence(inp, policy, b.factors, dims)
    dq_score = confidence["components"]["extraction"]["score"] * 0.5 + confidence["components"]["documents"]["score"] * 0.5
    risk_categories["data_quality"] = {"name": "Documentation and Data Quality Risk", "score": round(dq_score, 1),
                                       "factors": ["document_coverage", "extraction_confidence", "verification"],
                                       "assessed_factors": 3, "total_factors": 3}
    if confidence["score"] < policy["decision"]["min_confidence_for_decision"]:
        b.flag("medium", "Low assessment confidence",
               f"Confidence {confidence['score']:.0f}/100 is below the policy minimum of {policy['decision']['min_confidence_for_decision']} for an automated recommendation.",
               "data_quality")

    five_cs = _five_cs(b.factors)
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    red_flags = sorted(_dedupe_flags(b.red_flags), key=lambda f: sev_order.get(f["severity"], 4))

    summary = _summary(overall, band, dims, confidence, red_flags, policy)
    features = {f.key: f.value for f in b.factors if isinstance(f.value, (int, float)) or f.value is None}
    return ScorecardResult(
        overall_score=overall, risk_category=band, dimensions=dims, risk_categories=risk_categories, five_cs=five_cs,
        confidence=confidence, red_flags=red_flags, missing_information=missing, document_coverage=coverage,
        data_completeness=completeness, summary=summary, factors=[f.to_dict() for f in b.factors],
        model_label=f"{MODEL_NAME} v{MODEL_VERSION} (prototype policy model — not statistically calibrated)",
        features=features,
    )


def _dedupe_flags(flags: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in flags:
        k = (f["title"].lower(), f["source"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _dimension_explanation(name: str, score: float, factors: list[Factor], status: str, policy: dict) -> str:
    if status == "insufficient":
        reasons = "; ".join(f.explanation for f in factors[:3])
        return (f"{name}: {score:.0f}/100 (conservative policy default — insufficient evidence). {reasons}").strip()
    avail = sorted([f for f in factors if f.score is not None], key=lambda f: f.score)
    weakest = avail[0] if avail else None
    strongest = avail[-1] if avail else None
    parts = [f"{name}: {score:.0f}/100."]
    if weakest is not None and weakest.score < 60:
        parts.append(weakest.explanation)
    if strongest is not None and strongest is not weakest and strongest.score >= 70:
        parts.append(strongest.explanation)
    missing = [f.name for f in factors if f.score is None]
    if missing:
        parts.append("Not assessed: " + ", ".join(missing) + ".")
    return " ".join(p for p in parts if p)


def _collateral_factors(inp: AssessmentInputs, policy: dict) -> list[Factor]:
    app = inp.application
    req = float(app.get("requested_amount") or 0)
    haircuts = policy["decision"]["collateral_haircuts"]
    items = app.get("collateral") or []
    out = []
    if items and req > 0:
        realisable = 0.0
        lines = []
        for c in items:
            val = float(c.get("value") or 0)
            hc = haircuts.get(c.get("type") or "other", haircuts["other"])
            if c.get("existing_charge"):
                hc *= 0.5
            realisable += val * hc
            lines.append(f"{c.get('description') or c.get('type')}: {_money(val)} × {hc:.0%}")
        cov = realisable / req
        sc = curve_score(policy["factor_curves"]["security_coverage"], cov)
        out.append(Factor("security_coverage", "Security coverage", None, "financial", ["collateral"], sc, "ok", f"{cov:.2f}x",
                          f"Realisable security value {_money(realisable)} covers {cov:.2f}x of the requested amount ({'; '.join(lines)}). "
                          "Valuations are as declared and require independent verification.", 1.5, cov,
                          [{"type": "application", "field": "collateral"}]))
    else:
        out.append(Factor("security_coverage", "Security coverage", None, "financial", ["collateral"],
                          15.0 if req > 0 else None, "ok" if req > 0 else "missing", "unsecured" if req > 0 else "n/a",
                          "No collateral has been offered; the facility would be unsecured.", 1.5, 0.0))
    guarantors = app.get("guarantors") or []
    if guarantors:
        nw = sum(float(g.get("net_worth") or 0) for g in guarantors)
        sc = 85.0 if req and nw >= 0.5 * req else 70.0
        out.append(Factor("guarantees", "Guarantee support", None, "financial", ["collateral", "character"], sc, "ok",
                          f"{len(guarantors)} guarantor(s)",
                          f"{len(guarantors)} guarantor(s) offered" + (f" with declared net worth {_money(nw)}." if nw else " (net worth not declared)."),
                          0.8, len(guarantors), [{"type": "application", "field": "guarantors"}]))
    else:
        out.append(Factor("guarantees", "Guarantee support", None, "financial", ["collateral", "character"], 45.0, "ok", "none",
                          "No personal or corporate guarantees offered.", 0.8, 0))
    return out


def _five_cs(factors: list[Factor]) -> dict:
    names = {"character": "Character", "capacity": "Capacity", "capital": "Capital", "collateral": "Collateral",
             "conditions": "Conditions"}
    out = {}
    for key, name in names.items():
        fs = [f for f in factors if key in f.five_cs]
        avail = [f for f in fs if f.score is not None]
        score = round(sum(f.score * f.weight for f in avail) / sum(f.weight for f in avail), 1) if avail else None
        strengths = [f"{f.name}: {f.explanation}" for f in sorted(avail, key=lambda f: -f.score) if f.score >= 70][:4]
        risks = [f"{f.name}: {f.explanation}" for f in sorted(avail, key=lambda f: f.score) if f.score < 55][:4]
        gaps = [f.name for f in fs if f.score is None]
        if score is None:
            assessment = f"{name} could not be assessed — insufficient evidence ({', '.join(gaps)})."
        else:
            tone = "strong" if score >= 75 else "adequate" if score >= 60 else "weak" if score >= 40 else "very weak"
            assessment = f"{name} is {tone} ({score:.0f}/100) based on {len(avail)} of {len(fs)} factors."
        out[key] = {"name": name, "score": score, "assessment": assessment, "strengths": strengths, "risks": risks,
                    "not_assessed": gaps, "factors": [f.key for f in fs]}
    return out


KEY_METRICS = ["revenue_from_operations", "pat", "ebitda", "total_assets", "current_assets", "current_liabilities",
               "total_debt", "shareholders_equity", "finance_costs", "operating_cash_flow"]


def _confidence(inp: AssessmentInputs, policy: dict, factors: list[Factor], dims: list[dict]):
    cfg = policy["confidence"]
    present = {d["category"] for d in inp.documents if d.get("extraction_status") in ("extracted", "partial")}
    uploaded = {d["category"] for d in inp.documents}
    req, rec = cfg["required_documents"], cfg["recommended_documents"]
    doc_weight_total = 2 * len(req) + len(rec)
    doc_score = (2 * sum(1 for c in req if c in present) + sum(1 for c in rec if c in present)) / doc_weight_total * 100
    coverage = {c: ("processed" if c in present else "uploaded_not_processed" if c in uploaded else "missing")
                for c in req + rec + ["board_minutes", "legal_notices"]}

    scorable = [f for f in factors if f.dimension]
    w = {d["key"]: d["weight"] for d in dims}
    total_w = sum(w[f.dimension] * f.weight for f in scorable) or 1
    completeness = sum(w[f.dimension] * f.weight for f in scorable if f.score is not None) / total_w * 100

    if inp.metric_confidences:
        avg = sum(inp.metric_confidences) / len(inp.metric_confidences)
        extraction = min(100.0, avg * 100 * 0.85 + inp.verified_metric_share * 15)
    else:
        avg, extraction = None, 0.0

    rd = 0.0
    if inp.research_mode in ("live", "synthetic"):
        rd += 50
    if inp.observations:
        rd += 30 + 20 * (sum(1 for o in inp.observations if o.get("reviewer_status") == "verified") / len(inp.observations))

    age_months = None
    if inp.latest_period_end:
        age_months = (inp.as_of.year - inp.latest_period_end.year) * 12 + (inp.as_of.month - inp.latest_period_end.month)
        recency = 100 if age_months <= 12 else 70 if age_months <= 18 else 40 if age_months <= 24 else 15
    else:
        recency = 0

    comps = {
        "documents": {"score": round(doc_score, 1), "weight": cfg["weights"]["documents"],
                      "detail": f"{sum(1 for c in req if c in present)}/{len(req)} required and {sum(1 for c in rec if c in present)}/{len(rec)} recommended document types processed."},
        "completeness": {"score": round(completeness, 1), "weight": cfg["weights"]["completeness"],
                         "detail": f"{sum(1 for f in scorable if f.score is not None)}/{len(scorable)} scorecard factors have evidence."},
        "extraction": {"score": round(extraction, 1), "weight": cfg["weights"]["extraction"],
                       "detail": (f"Average extraction confidence {avg:.0%}; {inp.verified_metric_share:.0%} of figures verified by an officer."
                                  if avg is not None else "No extracted financial figures.")},
        "research_diligence": {"score": round(rd, 1), "weight": cfg["weights"]["research_diligence"],
                               "detail": f"Research: {inp.research_mode.replace('_', ' ')}; {len(inp.observations)} due-diligence observation(s)."},
        "recency": {"score": float(recency), "weight": cfg["weights"]["recency"],
                    "detail": (f"Latest financial period {inp.latest_period} ended {age_months} month(s) before assessment."
                               if age_months is not None else "No dated financial period available.")},
    }
    score = sum(c["score"] * c["weight"] for c in comps.values()) / sum(c["weight"] for c in comps.values())
    level = next(l["label"] for l in cfg["levels"] if score >= l["min"])

    missing: list[dict] = []
    for c in req:
        if c not in present:
            missing.append({"type": "document", "item": c, "importance": "critical",
                            "message": f"Required document missing or unprocessed: {c.replace('_', ' ')}."})
    for c in rec:
        if c not in present:
            missing.append({"type": "document", "item": c, "importance": "recommended",
                            "message": f"Recommended document not provided: {c.replace('_', ' ')}."})
    for k in KEY_METRICS:
        if inp.current.get(k, {}).get("value") is None:
            missing.append({"type": "metric", "item": k, "importance": "high",
                            "message": f"Key financial figure not available: {k.replace('_', ' ')}."})
    if inp.research_mode not in ("live", "synthetic"):
        missing.append({"type": "research", "item": "external_research", "importance": "high",
                        "message": "External research was not performed (search provider not configured or not run)."})
    if not inp.observations:
        missing.append({"type": "due_diligence", "item": "site_visit", "importance": "high",
                        "message": "No primary due-diligence observations (site visit / management meeting) recorded."})
    if age_months is not None and age_months > policy["thresholds"]["stale_financials_months"]:
        missing.append({"type": "recency", "item": "stale_financials", "importance": "high",
                        "message": f"Latest financials ({inp.latest_period}) are {age_months} months old — obtain recent/provisional figures."})
    low_conf = sum(1 for c in inp.metric_confidences if c < 0.6)
    if low_conf:
        missing.append({"type": "verification", "item": "low_confidence_extractions", "importance": "medium",
                        "message": f"{low_conf} extracted figure(s) have low confidence and should be verified."})
    if not inp.prior:
        missing.append({"type": "metric", "item": "prior_period", "importance": "medium",
                        "message": "Only one financial period available — trends and growth cannot be assessed."})

    confidence = {"score": round(score, 1), "level": level, "components": comps}
    return confidence, coverage, round(completeness, 1), missing


def _summary(overall: float, band: str, dims: list[dict], confidence: dict, flags: list[dict], policy: dict) -> str:
    ranked = sorted(dims, key=lambda d: d["score"])
    weak = [f"{d['name']} ({d['score']:.0f})" for d in ranked[:2]]
    strong = [f"{d['name']} ({d['score']:.0f})" for d in ranked[-2:][::-1]]
    insufficient = [d["name"] for d in dims if d["data_status"] == "insufficient"]
    s = (f"Overall credit score {overall:.1f}/100 — {band} under the {policy['label']}. "
         f"Strongest dimensions: {', '.join(strong)}. Weakest: {', '.join(weak)}. "
         f"Assessment confidence is {confidence['level']} ({confidence['score']:.0f}/100).")
    if insufficient:
        s += f" Insufficient evidence for: {', '.join(insufficient)} (conservative default applied)."
    crit = [f for f in flags if f["severity"] in ("critical", "high")]
    if crit:
        s += f" {len(crit)} critical/high red flag(s) require attention."
    return s
