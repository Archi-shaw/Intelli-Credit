"""Deterministic financial ratio engine.

Ratios are only computed when every input is available. Missing inputs, zero denominators and
negative denominators each produce an explicit status instead of a fabricated number.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class RatioResult:
    key: str
    name: str
    category: str
    formula: str
    unit: str
    value: float | None
    status: str  # ok | missing_inputs | zero_denominator | negative_denominator
    inputs: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    interpretation: str = ""
    period: str | None = None
    sources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RatioDef:
    key: str
    name: str
    category: str
    formula: str
    unit: str
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]
    num_sign: tuple[int, ...] = ()
    scale: float = 1.0
    uses_prior: bool = False


RATIO_DEFS: list[RatioDef] = [
    RatioDef("current_ratio", "Current Ratio", "liquidity", "Current Assets / Current Liabilities", "x",
             ("current_assets",), ("current_liabilities",)),
    RatioDef("quick_ratio", "Quick Ratio", "liquidity", "(Current Assets − Inventory) / Current Liabilities", "x",
             ("current_assets", "inventory"), ("current_liabilities",), (1, -1)),
    RatioDef("debt_to_equity", "Debt-to-Equity", "leverage", "Total Debt / Shareholders' Equity", "x",
             ("total_debt",), ("shareholders_equity",)),
    RatioDef("debt_to_assets", "Debt-to-Assets", "leverage", "Total Debt / Total Assets", "x",
             ("total_debt",), ("total_assets",)),
    RatioDef("debt_to_ebitda", "Debt-to-EBITDA", "leverage", "Total Debt / EBITDA", "x", ("total_debt",), ("ebitda",)),
    RatioDef("interest_coverage", "Interest Coverage", "leverage", "EBIT / Interest Expense", "x",
             ("ebit",), ("finance_costs",)),
    RatioDef("net_profit_margin", "Net Profit Margin", "profitability", "PAT / Revenue × 100", "%",
             ("pat",), ("revenue_from_operations",), scale=100.0),
    RatioDef("ebitda_margin", "EBITDA Margin", "profitability", "EBITDA / Revenue × 100", "%",
             ("ebitda",), ("revenue_from_operations",), scale=100.0),
    RatioDef("roa", "Return on Assets", "profitability", "PAT / Total Assets × 100", "%",
             ("pat",), ("total_assets",), scale=100.0),
    RatioDef("roe", "Return on Equity", "profitability", "PAT / Shareholders' Equity × 100", "%",
             ("pat",), ("shareholders_equity",), scale=100.0),
    RatioDef("receivables_turnover", "Receivables Turnover", "efficiency", "Revenue / Trade Receivables", "x",
             ("revenue_from_operations",), ("trade_receivables",)),
    RatioDef("receivable_days", "Receivable Days", "efficiency", "Trade Receivables / Revenue × 365", "days",
             ("trade_receivables",), ("revenue_from_operations",), scale=365.0),
    RatioDef("inventory_turnover", "Inventory Turnover", "efficiency", "Cost of Goods Sold / Inventory", "x",
             ("cost_of_goods_sold",), ("inventory",)),
    RatioDef("payables_turnover", "Payables Turnover", "efficiency", "Cost of Goods Sold / Trade Payables", "x",
             ("cost_of_goods_sold",), ("trade_payables",)),
    RatioDef("ocf_to_debt", "Operating Cash Flow to Total Debt", "cash_flow", "Operating Cash Flow / Total Debt", "x",
             ("operating_cash_flow",), ("total_debt",)),
    RatioDef("cash_flow_interest_cover", "Cash Flow to Interest", "cash_flow",
             "Operating Cash Flow / Interest Expense", "x", ("operating_cash_flow",), ("finance_costs",)),
    RatioDef("cash_conversion", "Cash Conversion (OCF / EBITDA)", "cash_flow", "Operating Cash Flow / EBITDA", "x",
             ("operating_cash_flow",), ("ebitda",)),
    RatioDef("contingent_to_networth", "Contingent Liabilities to Net Worth", "leverage",
             "Contingent Liabilities / Shareholders' Equity", "x", ("contingent_liabilities",), ("shareholders_equity",)),
    RatioDef("rpt_to_revenue", "Related-Party Transactions to Revenue", "governance",
             "Related-Party Transactions / Revenue", "x", ("related_party_transactions",), ("revenue_from_operations",)),
]


def _interpret(key: str, v: float, th: dict) -> str:
    if key == "current_ratio":
        lim = th.get("current_ratio_min", 1.2)
        return (f"Current ratio of {v:.2f}x is below the policy threshold of {lim:.2f}x, indicating limited short-term liquidity."
                if v < lim else f"Current ratio of {v:.2f}x meets the policy threshold of {lim:.2f}x; short-term obligations are covered.")
    if key == "quick_ratio":
        lim = th.get("quick_ratio_min", 0.8)
        return (f"Excluding inventory, liquid assets cover {v:.2f}x of current liabilities (below {lim:.2f}x — reliance on inventory liquidation)."
                if v < lim else f"Liquid assets excluding inventory cover {v:.2f}x of current liabilities.")
    if key == "debt_to_equity":
        lim = th.get("debt_to_equity_max", 3.0)
        if v > lim:
            return f"Debt-to-equity of {v:.2f}x exceeds the policy maximum of {lim:.1f}x — highly leveraged capital structure."
        return f"Debt-to-equity of {v:.2f}x is within the policy maximum of {lim:.1f}x." + (" Leverage is conservative." if v < 1 else "")
    if key == "debt_to_assets":
        return f"{v:.0%} of total assets are debt-funded."
    if key == "debt_to_ebitda":
        return f"Total debt equals {v:.1f} years of EBITDA." + (" Elevated debt burden relative to earnings." if v > 4 else "")
    if key == "interest_coverage":
        lim = th.get("interest_coverage_min", 2.0)
        if v < 1:
            return f"EBIT covers interest only {v:.2f}x — operating profit is insufficient to meet interest cost."
        return (f"Interest coverage of {v:.2f}x is below the policy minimum of {lim:.1f}x." if v < lim
                else f"EBIT covers interest expense {v:.2f}x, above the policy minimum of {lim:.1f}x.")
    if key in ("net_profit_margin", "ebitda_margin", "roa", "roe"):
        label = {"net_profit_margin": "Net profit margin", "ebitda_margin": "EBITDA margin", "roa": "Return on assets",
                 "roe": "Return on equity"}[key]
        return f"{label} of {v:.1f}%" + (" — loss-making." if v < 0 else ".")
    if key == "receivable_days":
        lim = th.get("receivable_days_max", 120)
        return (f"Receivables represent {v:.0f} days of sales, above the {lim:.0f}-day policy threshold — slow collections."
                if v > lim else f"Receivables represent {v:.0f} days of sales.")
    if key == "ocf_to_debt":
        return (f"Operating cash flow is negative relative to debt ({v:.2f}x)." if v < 0
                else f"Operating cash flow covers {v:.0%} of total debt annually.")
    if key == "cash_conversion":
        return f"{v:.0%} of EBITDA converted to operating cash flow."
    if key == "cash_flow_interest_cover":
        return f"Operating cash flow covers interest {v:.2f}x."
    if key == "contingent_to_networth":
        return f"Contingent liabilities equal {v:.0%} of net worth."
    if key == "rpt_to_revenue":
        return f"Related-party transactions equal {v:.0%} of revenue."
    if key in ("receivables_turnover", "inventory_turnover", "payables_turnover"):
        return f"Turnover of {v:.2f}x per year."
    return ""


def compute_ratios(current: dict[str, dict], prior: dict[str, dict] | None = None, *, period: str | None = None,
                   thresholds: dict | None = None) -> list[RatioResult]:
    """current/prior: metric_key -> {"value": float, "document_id": int, "page_number": int, "metric_id": int}"""
    th = thresholds or {}
    results: list[RatioResult] = []

    def get(key: str):
        item = current.get(key)
        return None if item is None or item.get("value") is None else float(item["value"])

    for rd in RATIO_DEFS:
        needed = list(rd.numerator) + list(rd.denominator)
        inputs = {k: get(k) for k in needed}
        missing = [k for k, v in inputs.items() if v is None]
        sources = [{"metric": k, **{x: current[k].get(x) for x in ("document_id", "page_number", "metric_id")}}
                   for k in needed if k in current and current[k].get("value") is not None]
        res = RatioResult(key=rd.key, name=rd.name, category=rd.category, formula=rd.formula, unit=rd.unit,
                          value=None, status="ok", inputs=inputs, missing=missing, period=period, sources=sources)
        if missing:
            res.status = "missing_inputs"
            res.interpretation = "Not calculated — missing: " + ", ".join(m.replace("_", " ") for m in missing) + "."
            results.append(res)
            continue
        signs = rd.num_sign or tuple(1 for _ in rd.numerator)
        num = sum(s * inputs[k] for s, k in zip(signs, rd.numerator))
        den = sum(inputs[k] for k in rd.denominator)
        if den == 0:
            res.status = "zero_denominator"
            res.interpretation = "Not calculated — the denominator is zero."
        elif den < 0 and rd.key in ("debt_to_equity", "roe", "contingent_to_networth"):
            res.status = "negative_denominator"
            res.interpretation = "Not meaningful — shareholders' equity is negative (net worth erosion); treated as a critical leverage risk."
        elif den < 0 and rd.key in ("debt_to_ebitda", "interest_coverage", "cash_conversion"):
            res.status = "negative_denominator"
            res.interpretation = ("Not meaningful — EBITDA is negative." if rd.key != "interest_coverage"
                                  else "Not meaningful — negative interest expense reported; verify extraction.")
        else:
            res.value = round(num / den * rd.scale, 4)
            res.interpretation = _interpret(rd.key, res.value, th)
        results.append(res)

    # Growth ratios need a prior period
    if prior is not None:
        for key, name, metric in (("revenue_growth", "Revenue Growth", "revenue_from_operations"),
                                  ("pat_growth", "PAT Growth", "pat"), ("ebitda_growth", "EBITDA Growth", "ebitda")):
            cur_v = get(metric)
            pri = prior.get(metric)
            pri_v = None if pri is None or pri.get("value") is None else float(pri["value"])
            res = RatioResult(key=key, name=name, category="growth", formula=f"({metric.replace('_', ' ')} current − prior) / |prior| × 100",
                              unit="%", value=None, status="ok", inputs={"current": cur_v, "prior": pri_v}, period=period)
            if cur_v is None or pri_v is None:
                res.status = "missing_inputs"
                res.missing = [x for x, v in (("current", cur_v), ("prior", pri_v)) if v is None]
                res.interpretation = "Not calculated — requires two comparable periods."
            elif pri_v == 0:
                res.status = "zero_denominator"
                res.interpretation = "Not calculated — prior-period value is zero."
            else:
                res.value = round((cur_v - pri_v) / abs(pri_v) * 100.0, 2)
                res.interpretation = f"{name.split()[0]} {'grew' if res.value >= 0 else 'declined'} {abs(res.value):.1f}% year on year."
            results.append(res)
    return results


def derive_metrics(stmt: dict[str, dict]) -> dict[str, dict]:
    """Fill standard aggregates from their components when the aggregate itself was not reported.

    Derived values are labelled with ``derived_from`` so they are never mistaken for reported figures.
    """
    out = dict(stmt)

    def v(k):
        it = out.get(k)
        return None if it is None or it.get("value") is None else float(it["value"])

    def put(k, value, parts):
        out[k] = {"value": round(value, 2), "derived_from": parts, "document_id": None, "page_number": None,
                  "metric_id": None, "confidence": min((out[p].get("confidence") or 0.5) for p in parts)}

    if v("total_debt") is None and v("long_term_borrowings") is not None and v("short_term_borrowings") is not None:
        put("total_debt", v("long_term_borrowings") + v("short_term_borrowings"), ["long_term_borrowings", "short_term_borrowings"])
    if v("shareholders_equity") is None and v("share_capital") is not None and v("reserves_and_surplus") is not None:
        put("shareholders_equity", v("share_capital") + v("reserves_and_surplus"), ["share_capital", "reserves_and_surplus"])
    if v("ebit") is None and v("pbt") is not None and v("finance_costs") is not None:
        put("ebit", v("pbt") + v("finance_costs"), ["pbt", "finance_costs"])
    if v("ebitda") is None and v("pbt") is not None and v("finance_costs") is not None and v("depreciation") is not None:
        put("ebitda", v("pbt") + v("finance_costs") + v("depreciation"), ["pbt", "finance_costs", "depreciation"])
    if v("ebit") is None and v("ebitda") is not None and v("depreciation") is not None:
        put("ebit", v("ebitda") - v("depreciation"), ["ebitda", "depreciation"])
    if v("revenue_from_operations") is None and v("net_revenue") is not None:
        out["revenue_from_operations"] = out["net_revenue"]
    return out
