"""Cross-source financial reconciliation.

Compares GST returns, bank statements, financial statements and declared/sanctioned borrowings.
Outputs are framed as *Potential Discrepancy*, *Unusual Pattern* or *Requires Verification* —
a numerical mismatch is never treated as proof of wrongdoing.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

NON_BUSINESS_CREDIT = {"loan_disbursement", "inter_account_transfer", "interest_credit", "reversal", "cheque_return"}


@dataclass
class ReconItem:
    check_type: str
    metric: str
    status: str  # consistent | potential_discrepancy | unusual_pattern | requires_verification | insufficient_data
    severity: str  # info | low | medium | high | critical
    description: str
    period: str | None = None
    source_a_label: str | None = None
    source_a_value: float | None = None
    source_b_label: str | None = None
    source_b_value: float | None = None
    difference: float | None = None
    difference_pct: float | None = None
    risk_implication: str | None = None
    confidence: float = 0.5
    suggested_investigation: str | None = None
    source_document_ids: list[int] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReconInputs:
    gst_rows: list[dict]
    bank_txns: list[dict]
    statements: dict[str, dict[str, dict]]  # period_label -> metric_key -> {value, document_id}
    declared_borrowings: list[dict]
    sanction_metrics: list[dict]
    application_date: date | None = None


DEFAULT_TOLERANCES = {
    "gst_bank_pct": (10.0, 25.0),
    "itc_excess_pct": (5.0, 15.0),
    "gst_fs_pct": (10.0, 20.0),
    "fs_bank_pct": (15.0, 30.0),
    "debt_pct": (10.0, 25.0),
    "interest_pct": (25.0, 50.0),
    "circular_amount_tolerance_pct": 2.0,
    "circular_window_days": 7,
    "cash_deposit_share_pct": 20.0,
}


def _pct(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return round((a - b) / abs(b) * 100.0, 2)


def _band(pct: float | None, tol: tuple[float, float]) -> tuple[str, str]:
    if pct is None:
        return "insufficient_data", "info"
    a = abs(pct)
    if a <= tol[0]:
        return "consistent", "info"
    if a <= tol[1]:
        return "potential_discrepancy", "medium"
    return "potential_discrepancy", "high"


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _fy_months(period_end: date) -> list[str]:
    start = date(period_end.year - 1, period_end.month, 1) + timedelta(days=32)
    start = date(start.year, start.month, 1)
    months = []
    cur = start
    for _ in range(12):
        months.append(_month(cur))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _latest_period(statements: dict) -> tuple[str | None, dict]:
    if not statements:
        return None, {}
    label = sorted(statements.keys(), key=lambda p: statements[p].get("_period_end") or date.min)[-1]
    return label, statements[label]


def _val(stmt: dict, key: str) -> float | None:
    item = stmt.get(key)
    return None if item is None else item.get("value")


def _docs(*items) -> list[int]:
    out: set[int] = set()
    for it in items:
        if isinstance(it, dict) and it.get("document_id"):
            out.add(int(it["document_id"]))
        elif isinstance(it, (list, set, tuple)):
            for x in it:
                if isinstance(x, dict) and x.get("document_id"):
                    out.add(int(x["document_id"]))
                elif isinstance(x, int):
                    out.add(x)
    return sorted(out)


def _fmt(v: float | None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def _business_receipts_by_month(txns: list[dict]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in txns:
        if t.get("credit") and t.get("category") not in NON_BUSINESS_CREDIT:
            out[_month(t["txn_date"])] += float(t["credit"])
    return dict(out)


def _gst_outward_by_month(gst_rows: list[dict]) -> tuple[dict[str, dict], str | None]:
    for rtype in ("GSTR3B", "GSTR1"):
        rows = [r for r in gst_rows if r["return_type"] == rtype and r.get("taxable_value") is not None]
        if rows:
            out: dict[str, dict] = {}
            for r in rows:
                m = out.setdefault(r["period"], {"taxable": 0.0, "tax": 0.0, "docs": set()})
                m["taxable"] += float(r["taxable_value"] or 0)
                m["tax"] += float(r.get("total_tax") or 0)
                if r.get("document_id"):
                    m["docs"].add(r["document_id"])
            return out, rtype
    return {}, None


# --- individual checks -----------------------------------------------------------------------------

def check_gst_vs_bank(inp: ReconInputs, tol) -> list[ReconItem]:
    gst, rtype = _gst_outward_by_month(inp.gst_rows)
    bank = _business_receipts_by_month(inp.bank_txns)
    if not gst or not bank:
        missing = [n for n, d in (("GST returns", gst), ("bank statements", bank)) if not d]
        return [ReconItem(
            check_type="gst_vs_bank", metric="GST outward supplies vs bank business receipts", status="insufficient_data",
            severity="info", description=f"Comparison not possible: no usable {' or '.join(missing)} data.",
            suggested_investigation=f"Upload {' and '.join(missing)} covering the same months.", confidence=0.0,
        )]
    months = sorted(set(gst) & set(bank))
    if not months:
        return [ReconItem(
            check_type="gst_vs_bank", metric="GST outward supplies vs bank business receipts", status="insufficient_data",
            severity="info", description="GST returns and bank statements do not cover any common month.",
            suggested_investigation="Obtain bank statements for the same months as the GST returns.", confidence=0.0,
        )]
    gst_gross = sum(gst[m]["taxable"] + gst[m]["tax"] for m in months)
    bank_total = sum(bank[m] for m in months)
    pct = _pct(bank_total, gst_gross)
    status, severity = _band(pct, tol["gst_bank_pct"])
    monthly = []
    for m in months:
        g = gst[m]["taxable"] + gst[m]["tax"]
        b = bank[m]
        mp = _pct(b, g)
        monthly.append({"month": m, "gst_gross": round(g, 2), "bank_receipts": round(b, 2), "difference_pct": mp,
                        "flag": mp is not None and abs(mp) > tol["gst_bank_pct"][1]})
    flagged_months = [x["month"] for x in monthly if x["flag"]]
    period = f"{months[0]} to {months[-1]} ({len(months)} months)"
    if status == "consistent":
        desc = (f"Bank business receipts ({_fmt(bank_total)}) are within {tol['gst_bank_pct'][0]:.0f}% of GST-reported "
                f"gross outward supplies incl. tax ({_fmt(gst_gross)}) over {len(months)} overlapping months.")
        implication = "Reported GST turnover is supported by banking inflows in the analysed account."
        action = None
    elif pct is not None and pct < 0:
        desc = (f"Potential Discrepancy: bank business receipts ({_fmt(bank_total)}) are {abs(pct):.1f}% lower than GST "
                f"gross outward supplies ({_fmt(gst_gross)}).")
        implication = ("Sales may be realised through other bank accounts, collections may be delayed (receivable build-up), "
                       "or GST turnover may not be supported by actual cash inflows.")
        action = "Obtain statements for all operative bank accounts and a debtor ageing schedule; reconcile month-wise."
    else:
        desc = (f"Potential Discrepancy: bank business receipts ({_fmt(bank_total)}) exceed GST gross outward supplies "
                f"({_fmt(gst_gross)}) by {abs(pct or 0):.1f}%.")
        implication = ("Credits may include non-sales inflows (advances, related-party funds) or turnover that is not "
                       "reflected in GST returns. Requires Verification.")
        action = "Identify the largest non-GST-backed credits and obtain supporting invoices or fund-flow explanations."
    conf = 0.8 if len(months) >= 6 else (0.6 if len(months) >= 3 else 0.4)
    gst_docs = set().union(*(gst[m]["docs"] for m in months))
    bank_docs = {t.get("document_id") for t in inp.bank_txns if t.get("document_id")}
    items = [ReconItem(
        check_type="gst_vs_bank", metric=f"{rtype} outward supplies (incl. tax) vs bank business receipts", status=status,
        severity=severity, description=desc, period=period, source_a_label=f"{rtype} gross outward supplies",
        source_a_value=round(gst_gross, 2), source_b_label="Bank business receipts", source_b_value=round(bank_total, 2),
        difference=round(bank_total - gst_gross, 2), difference_pct=pct, risk_implication=implication,
        confidence=conf, suggested_investigation=action, source_document_ids=sorted(gst_docs | bank_docs),
        details={"monthly": monthly, "excluded_credit_categories": sorted(NON_BUSINESS_CREDIT)},
    )]
    if flagged_months and status == "consistent":
        items.append(ReconItem(
            check_type="gst_vs_bank", metric="Month-level GST vs bank variance", status="unusual_pattern",
            severity="low", period=", ".join(flagged_months),
            description=f"Unusual Pattern: {len(flagged_months)} month(s) show >{tol['gst_bank_pct'][1]:.0f}% variance "
                        "between GST and bank receipts although the totals agree (possible timing differences).",
            risk_implication="Timing mismatches are common; persistent swings can indicate period-end window dressing.",
            confidence=0.5, suggested_investigation="Review month-end invoices and receipts for the flagged months.",
            source_document_ids=sorted(gst_docs | bank_docs), details={"months": flagged_months},
        ))
    return items


def check_itc(inp: ReconInputs, tol) -> list[ReconItem]:
    claimed = defaultdict(float)
    available = defaultdict(float)
    docs = set()
    for r in inp.gst_rows:
        if r["return_type"] == "GSTR3B" and r.get("itc_claimed") is not None:
            claimed[r["period"]] += float(r["itc_claimed"])
            docs.add(r.get("document_id"))
        if r["return_type"] in ("GSTR2A", "GSTR2B") and r.get("itc_available") is not None:
            available[r["period"]] += float(r["itc_available"])
            docs.add(r.get("document_id"))
    months = sorted(set(claimed) & set(available))
    if not months:
        return [ReconItem(
            check_type="gstr2a_vs_3b", metric="Input tax credit: GSTR-3B claimed vs GSTR-2A/2B available",
            status="insufficient_data", severity="info", confidence=0.0,
            description="ITC comparison not possible: GSTR-3B ITC claims and GSTR-2A/2B data for the same months were not found.",
            suggested_investigation="Upload GSTR-3B and GSTR-2A (or 2B) summaries for the same tax periods.",
        )]
    total_c = sum(claimed[m] for m in months)
    total_a = sum(available[m] for m in months)
    pct = _pct(total_c, total_a)
    monthly = [{"month": m, "itc_claimed": round(claimed[m], 2), "itc_available": round(available[m], 2),
                "excess": round(claimed[m] - available[m], 2), "excess_pct": _pct(claimed[m], available[m])} for m in months]
    excess_months = [x["month"] for x in monthly if x["excess_pct"] is not None and x["excess_pct"] > tol["itc_excess_pct"][0]]
    if pct is None or pct <= tol["itc_excess_pct"][0]:
        status, severity = "consistent", "info"
        desc = (f"ITC claimed in GSTR-3B ({_fmt(total_c)}) does not exceed supplier-reported ITC in GSTR-2A/2B "
                f"({_fmt(total_a)}) beyond the {tol['itc_excess_pct'][0]:.0f}% tolerance.")
        implication, action = "ITC claims appear supported by supplier filings.", None
    else:
        status = "potential_discrepancy"
        severity = "high" if pct > tol["itc_excess_pct"][1] else "medium"
        desc = (f"Potential Discrepancy: ITC claimed ({_fmt(total_c)}) exceeds ITC available per GSTR-2A/2B "
                f"({_fmt(total_a)}) by {pct:.1f}% across {len(months)} month(s).")
        implication = ("Excess ITC may be reversed with interest and penalty on GST scrutiny, creating a contingent "
                       "tax liability; it can also indicate purchases from non-filing or non-genuine suppliers.")
        action = "Obtain the ITC reconciliation statement and supplier-wise mismatch report; check for GST notices (DRC-01)."
    return [ReconItem(
        check_type="gstr2a_vs_3b", metric="Input tax credit: GSTR-3B claimed vs GSTR-2A/2B available", status=status,
        severity=severity, description=desc, period=f"{months[0]} to {months[-1]}", source_a_label="ITC claimed (GSTR-3B)",
        source_a_value=round(total_c, 2), source_b_label="ITC available (GSTR-2A/2B)", source_b_value=round(total_a, 2),
        difference=round(total_c - total_a, 2), difference_pct=pct, risk_implication=implication,
        confidence=0.8 if len(months) >= 6 else 0.55, suggested_investigation=action,
        source_document_ids=sorted(d for d in docs if d), details={"monthly": monthly, "excess_months": excess_months},
    )]


def check_filing_pattern(inp: ReconInputs, tol) -> list[ReconItem]:
    rows = [r for r in inp.gst_rows if r["return_type"] == "GSTR3B"]
    if len(rows) < 3:
        return []
    periods = sorted({r["period"] for r in rows})
    y0, m0 = map(int, periods[0].split("-"))
    y1, m1 = map(int, periods[-1].split("-"))
    expected = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        expected.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    missing = [p for p in expected if p not in periods]
    late = []
    for r in rows:
        if r.get("filing_date"):
            yy, mm = map(int, r["period"].split("-"))
            ny, nm = (yy + 1, 1) if mm == 12 else (yy, mm + 1)
            due = date(ny, nm, min(20, calendar.monthrange(ny, nm)[1]))
            if r["filing_date"] > due:
                late.append({"period": r["period"], "filed": r["filing_date"].isoformat(), "days_late": (r["filing_date"] - due).days})
    docs = sorted({r.get("document_id") for r in rows if r.get("document_id")})
    items = []
    if missing:
        items.append(ReconItem(
            check_type="gst_filing_pattern", metric="GSTR-3B filing continuity", status="requires_verification",
            severity="high" if len(missing) >= 3 else "medium", period=f"{periods[0]} to {periods[-1]}",
            description=f"Requires Verification: no GSTR-3B data for {len(missing)} month(s) within the filed range: {', '.join(missing[:8])}.",
            risk_implication="Gaps can indicate non-filing (compliance risk, possible GSTIN suspension) or incomplete documents.",
            confidence=0.6, suggested_investigation="Obtain the GST portal filing status (return compliance history) for the gap months.",
            source_document_ids=docs, details={"missing_periods": missing},
        ))
    if late:
        avg_delay = sum(x["days_late"] for x in late) / len(late)
        items.append(ReconItem(
            check_type="gst_filing_pattern", metric="GSTR-3B filing timeliness", status="unusual_pattern",
            severity="medium" if len(late) >= 3 else "low", period=f"{periods[0]} to {periods[-1]}",
            description=f"Unusual Pattern: {len(late)} of {len(rows)} GSTR-3B returns were filed after the 20th-of-month due date "
                        f"(average delay {avg_delay:.0f} days).",
            risk_implication="Persistent late filing attracts interest/late fees and can signal liquidity stress.",
            confidence=0.7, suggested_investigation="Check whether late filings coincide with cash-flow tightness in bank statements.",
            source_document_ids=docs, details={"late_filings": late},
        ))
    if not items:
        items.append(ReconItem(
            check_type="gst_filing_pattern", metric="GSTR-3B filing continuity and timeliness", status="consistent",
            severity="info", period=f"{periods[0]} to {periods[-1]}", confidence=0.7, source_document_ids=docs,
            description=f"All {len(rows)} GSTR-3B periods in range are present and no late filing was detected (where filing dates were available).",
        ))
    return items


def check_gst_vs_fs(inp: ReconInputs, tol) -> list[ReconItem]:
    gst, rtype = _gst_outward_by_month(inp.gst_rows)
    out = []
    for label, stmt in inp.statements.items():
        rev = stmt.get("revenue_from_operations")
        pend = stmt.get("_period_end")
        if not rev or rev.get("value") is None or not pend or not gst:
            continue
        months = [m for m in _fy_months(pend) if m in gst]
        if len(months) < 6:
            continue
        gst_turnover = sum(gst[m]["taxable"] for m in months)
        fs_rev = float(rev["value"]) * len(months) / 12.0
        pct = _pct(gst_turnover, fs_rev)
        status, severity = _band(pct, tol["gst_fs_pct"])
        prorated = len(months) < 12
        desc_basis = f"{len(months)} months of {rtype}" + (" (FS revenue pro-rated)" if prorated else "")
        if status == "consistent":
            desc = f"GST taxable turnover ({_fmt(gst_turnover)}) is consistent with reported revenue from operations ({_fmt(fs_rev)}) for {label} [{desc_basis}]."
            implication, action = "Revenue reported in financial statements is corroborated by GST filings.", None
        else:
            direction = "lower" if (pct or 0) < 0 else "higher"
            desc = (f"Potential Discrepancy: GST taxable turnover ({_fmt(gst_turnover)}) is {abs(pct or 0):.1f}% {direction} than "
                    f"revenue from operations ({_fmt(fs_rev)}) for {label} [{desc_basis}].")
            implication = ("Differences can arise from exports/exempt supplies, multiple GSTINs, or unbilled revenue; "
                           "large unexplained gaps may indicate over-statement of revenue in financial statements or under-reporting to GST.")
            action = "Obtain a GST-to-books turnover reconciliation (GSTR-9C or management reconciliation) for the year."
        out.append(ReconItem(
            check_type="gst_vs_financials", metric="GST taxable turnover vs revenue from operations", status=status,
            severity=severity, description=desc, period=label, source_a_label=f"{rtype} taxable turnover",
            source_a_value=round(gst_turnover, 2), source_b_label="Revenue from operations (financial statements)",
            source_b_value=round(fs_rev, 2), difference=round(gst_turnover - fs_rev, 2), difference_pct=pct,
            risk_implication=implication, confidence=0.75 if not prorated else 0.55, suggested_investigation=action,
            source_document_ids=sorted(set().union(*(gst[m]["docs"] for m in months)) | set(_docs(rev))),
            details={"months_compared": months, "prorated": prorated},
        ))
    return out


def check_fs_vs_bank(inp: ReconInputs, tol) -> list[ReconItem]:
    label, stmt = _latest_period(inp.statements)
    rev = stmt.get("revenue_from_operations") if stmt else None
    bank = _business_receipts_by_month(inp.bank_txns)
    if not rev or rev.get("value") is None or not bank:
        return [ReconItem(
            check_type="fs_vs_bank", metric="Declared revenue vs bank cash inflows", status="insufficient_data", severity="info",
            confidence=0.0, description="Comparison not possible: revenue from financial statements or bank statement credits are missing.",
            suggested_investigation="Upload audited financial statements and 12 months of bank statements.",
        )]
    months = sorted(bank)
    n = len(months)
    taxable = sum(float(r.get("taxable_value") or 0) for r in inp.gst_rows if r["return_type"] == "GSTR3B")
    tax = sum(float(r.get("total_tax") or 0) for r in inp.gst_rows if r["return_type"] == "GSTR3B")
    gross_up = (1 + tax / taxable) if taxable > 0 else 1.0
    expected = float(rev["value"]) * n / 12.0 * gross_up
    actual = sum(bank.values())
    pct = _pct(actual, expected)
    status, severity = _band(pct, tol["fs_bank_pct"])
    basis = (f"revenue pro-rated to {n} statement month(s)" + (f", grossed up by the GST effective rate ({(gross_up - 1) * 100:.1f}%)"
             if gross_up > 1 else ", not grossed up for GST (no GST data)"))
    if status == "consistent":
        desc = f"Bank business receipts ({_fmt(actual)}) support declared revenue for {label} ({_fmt(expected)}; {basis})."
        implication, action = "Accounting revenue appears to be supported by cash inflows.", None
    else:
        desc = (f"Potential Discrepancy: bank business receipts ({_fmt(actual)}) differ from declared revenue for {label} "
                f"({_fmt(expected)}; {basis}) by {pct:+.1f}%.")
        implication = ("If inflows are lower, revenue may be booked but not collected, or collections may route through other "
                       "accounts; if higher, credits may include non-operating funds.")
        action = "Request statements for all bank accounts and a revenue-to-receipts reconciliation."
    return [ReconItem(
        check_type="fs_vs_bank", metric="Declared revenue vs bank cash inflows", status=status, severity=severity,
        description=desc, period=f"{label} vs {months[0]}–{months[-1]}", source_a_label="Revenue (pro-rated, FS)",
        source_a_value=round(expected, 2), source_b_label="Bank business receipts", source_b_value=round(actual, 2),
        difference=round(actual - expected, 2), difference_pct=pct, risk_implication=implication,
        confidence=0.65 if n >= 6 else 0.4, suggested_investigation=action,
        source_document_ids=sorted(set(_docs(rev)) | {t["document_id"] for t in inp.bank_txns if t.get("document_id")}),
        details={"statement_months": months, "gst_gross_up_factor": round(gross_up, 4)},
    )]


def check_cash_flow_support(inp: ReconInputs, tol) -> list[ReconItem]:
    label, stmt = _latest_period(inp.statements)
    if not stmt:
        return []
    ocf, pat, ebitda = _val(stmt, "operating_cash_flow"), _val(stmt, "pat"), _val(stmt, "ebitda")
    if ocf is None or (pat is None and ebitda is None):
        return [ReconItem(
            check_type="cash_flow_support", metric="Profit vs operating cash flow", status="insufficient_data", severity="info",
            confidence=0.0, period=label, description="Operating cash flow or profit figures were not extracted; cash conversion cannot be assessed.",
            suggested_investigation="Upload the cash flow statement for the latest year.",
        )]
    docs = _docs(stmt.get("operating_cash_flow"), stmt.get("pat"), stmt.get("ebitda"))
    if pat is not None and pat > 0 and ocf < 0:
        return [ReconItem(
            check_type="cash_flow_support", metric="Profit vs operating cash flow", status="requires_verification",
            severity="high", period=label, source_a_label="Profit after tax", source_a_value=pat,
            source_b_label="Operating cash flow", source_b_value=ocf, difference=round(ocf - pat, 2),
            description=f"Requires Verification: the company reports a profit ({_fmt(pat)}) but negative operating cash flow ({_fmt(ocf)}).",
            risk_implication="Accounting profit is not converting into cash — typically working-capital build-up (receivables/inventory) and a liquidity risk.",
            confidence=0.8, suggested_investigation="Analyse receivable and inventory movements; obtain debtor ageing and stock statements.",
            source_document_ids=docs,
        )]
    if ebitda and ebitda > 0:
        conversion = ocf / ebitda
        if conversion < 0.5:
            return [ReconItem(
                check_type="cash_flow_support", metric="Cash conversion (OCF / EBITDA)", status="potential_discrepancy",
                severity="medium", period=label, source_a_label="EBITDA", source_a_value=ebitda,
                source_b_label="Operating cash flow", source_b_value=ocf, difference=round(ocf - ebitda, 2),
                difference_pct=round((conversion - 1) * 100, 2),
                description=f"Potential Discrepancy: only {conversion:.0%} of EBITDA converted into operating cash flow.",
                risk_implication="Weak cash conversion limits debt-servicing capacity even when profitability looks adequate.",
                confidence=0.75, suggested_investigation="Review working-capital cycle and one-off cash items.", source_document_ids=docs,
            )]
    return [ReconItem(
        check_type="cash_flow_support", metric="Profit vs operating cash flow", status="consistent", severity="info",
        period=label, source_a_label="Profit after tax" if pat is not None else "EBITDA",
        source_a_value=pat if pat is not None else ebitda, source_b_label="Operating cash flow", source_b_value=ocf,
        description="Operating cash flow supports reported profitability.", confidence=0.75, source_document_ids=docs,
    )]


def _declared_outstanding(inp: ReconInputs) -> tuple[float | None, list[dict]]:
    rows = [b for b in inp.declared_borrowings if b.get("outstanding_amount") not in (None, "")]
    if not rows:
        return None, []
    return sum(float(b["outstanding_amount"]) for b in rows), rows


def check_debt(inp: ReconInputs, tol) -> list[ReconItem]:
    label, stmt = _latest_period(inp.statements)
    items: list[ReconItem] = []
    fs_debt_item = stmt.get("total_debt") if stmt else None
    fs_debt = _val(stmt, "total_debt") if stmt else None
    if fs_debt is None and stmt:
        lt, st = _val(stmt, "long_term_borrowings"), _val(stmt, "short_term_borrowings")
        if lt is not None and st is not None:
            fs_debt = lt + st
            fs_debt_item = [stmt.get("long_term_borrowings"), stmt.get("short_term_borrowings")]
    declared, rows = _declared_outstanding(inp)
    sanction_out = [m for m in inp.sanction_metrics if m["metric_key"] == "outstanding_amount" and m.get("value")]
    sanctioned = [m for m in inp.sanction_metrics if m["metric_key"] == "sanctioned_amount" and m.get("value")]
    if fs_debt is None or (declared is None and not sanction_out and not sanctioned):
        missing = []
        if fs_debt is None:
            missing.append("borrowings in financial statements")
        if declared is None and not sanction_out and not sanctioned:
            missing.append("declared existing borrowings or sanction letters")
        items.append(ReconItem(
            check_type="debt_vs_declared", metric="Borrowings: financial statements vs declared/sanctioned facilities",
            status="insufficient_data", severity="info", confidence=0.0, period=label,
            description=f"Comparison not possible: missing {' and '.join(missing)}.",
            suggested_investigation="Capture existing borrowings in the application and upload sanction letters of existing lenders.",
        ))
        return items
    compare_val = declared if declared is not None else sum(m["value"] for m in (sanction_out or sanctioned))
    compare_label = "Declared outstanding borrowings (application)" if declared is not None else (
        "Outstanding per sanction letters" if sanction_out else "Sanctioned limits per sanction letters")
    pct = _pct(fs_debt, compare_val)
    status, severity = _band(pct, tol["debt_pct"])
    if status == "consistent":
        desc = f"Borrowings in financial statements ({_fmt(fs_debt)}, {label}) are consistent with {compare_label.lower()} ({_fmt(compare_val)})."
        implication, action = "Declared liabilities agree with audited borrowings.", None
    elif (pct or 0) > 0:
        desc = (f"Potential Discrepancy: borrowings per financial statements ({_fmt(fs_debt)}) exceed {compare_label.lower()} "
                f"({_fmt(compare_val)}) by {pct:.1f}%.")
        implication = "Possible undisclosed or under-declared liabilities (other lenders, NBFC or related-party loans)."
        action = "Obtain a lender-wise borrowing schedule reconciled to the balance sheet and a CIBIL/CRILC commercial report."
    else:
        desc = (f"Potential Discrepancy: {compare_label.lower()} ({_fmt(compare_val)}) exceed borrowings per financial statements "
                f"({_fmt(fs_debt)}) by {abs(pct or 0):.1f}%.")
        implication = "Borrowings have likely increased after the balance-sheet date, or the financial statements are outdated."
        action = "Obtain provisional/latest financials and confirm the date of each sanction."
    items.append(ReconItem(
        check_type="debt_vs_declared", metric="Borrowings: financial statements vs declared/sanctioned facilities",
        status=status, severity=severity, description=desc, period=label, source_a_label="Total borrowings (financial statements)",
        source_a_value=round(fs_debt, 2), source_b_label=compare_label, source_b_value=round(compare_val, 2),
        difference=round(fs_debt - compare_val, 2), difference_pct=pct, risk_implication=implication, confidence=0.7,
        suggested_investigation=action,
        source_document_ids=sorted(set(_docs(fs_debt_item)) | set(_docs(sanction_out or sanctioned))),
        details={"declared_rows": rows},
    ))

    # Interest cost vs debt × declared rates
    fc = _val(stmt, "finance_costs")
    rated = [b for b in rows if b.get("interest_rate") not in (None, "")]
    if fc and rated:
        implied = sum(float(b["outstanding_amount"]) * float(b["interest_rate"]) / 100.0 for b in rated)
        if implied > 0:
            ipct = _pct(fc, implied)
            ist, isev = _band(ipct, tol["interest_pct"])
            items.append(ReconItem(
                check_type="interest_vs_debt", metric="Finance costs vs interest implied by declared borrowings", status=ist,
                severity=isev, period=label, source_a_label="Finance costs (P&L)", source_a_value=round(fc, 2),
                source_b_label="Implied interest (declared outstanding × rate)", source_b_value=round(implied, 2),
                difference=round(fc - implied, 2), difference_pct=ipct, confidence=0.55,
                description=(f"Finance costs ({_fmt(fc)}) are consistent with interest implied by declared borrowings ({_fmt(implied)})."
                             if ist == "consistent" else
                             f"Potential Discrepancy: finance costs ({_fmt(fc)}) differ from interest implied by declared borrowings "
                             f"({_fmt(implied)}) by {ipct:+.1f}%."),
                risk_implication=None if ist == "consistent" else (
                    "Materially higher interest cost suggests additional or higher-cost borrowings not declared; lower cost may reflect "
                    "repayments during the year or stale declarations."),
                suggested_investigation=None if ist == "consistent" else "Obtain the interest expense break-up by lender from the auditor's notes.",
                source_document_ids=_docs(stmt.get("finance_costs")),
            ))
    return items


def _name_match(a: str, b: str) -> bool:
    ta = {w for w in a.lower().replace(".", " ").split() if len(w) > 2 and w not in ("bank", "ltd", "limited", "the", "of")}
    tb = {w for w in b.lower().replace(".", " ").split() if len(w) > 2 and w not in ("bank", "ltd", "limited", "the", "of")}
    return bool(ta & tb)


def check_loan_servicing(inp: ReconInputs, tol) -> list[ReconItem]:
    debits = [t for t in inp.bank_txns if t.get("debit") and t.get("category") in ("loan_repayment", "interest_debit")]
    if not debits:
        return []
    by_cp: dict[str, float] = defaultdict(float)
    for t in debits:
        by_cp[t.get("counterparty") or "UNIDENTIFIED"] += float(t["debit"])
    declared_names = [str(b.get("lender") or "") for b in inp.declared_borrowings if b.get("lender")]
    declared_names += [str(m.get("text_value") or "") for m in inp.sanction_metrics if m.get("text_value")]
    undeclared = {cp: amt for cp, amt in by_cp.items()
                  if cp != "UNIDENTIFIED" and not any(_name_match(cp, n) for n in declared_names)}
    docs = sorted({t["document_id"] for t in debits if t.get("document_id")})
    if undeclared:
        total = sum(undeclared.values())
        return [ReconItem(
            check_type="bank_loan_servicing", metric="Loan servicing debits vs declared lenders", status="requires_verification",
            severity="high" if total > 0.25 * sum(by_cp.values()) else "medium",
            source_a_label="Loan/EMI debits to undeclared counterparties", source_a_value=round(total, 2),
            source_b_label="Total loan servicing debits", source_b_value=round(sum(by_cp.values()), 2),
            description=f"Requires Verification: bank statements show loan/EMI debits to {len(undeclared)} counterparty(ies) not in the declared borrowings: "
                        + ", ".join(list(undeclared)[:5]) + ".",
            risk_implication="Possible undisclosed borrowings that increase the actual debt-service burden.",
            confidence=0.6, suggested_investigation="Ask the borrower to explain each counterparty and provide the related sanction letters/loan statements.",
            source_document_ids=docs, details={"undeclared_counterparties": {k: round(v, 2) for k, v in undeclared.items()},
                                               "all_servicing": {k: round(v, 2) for k, v in by_cp.items()}},
        )]
    return [ReconItem(
        check_type="bank_loan_servicing", metric="Loan servicing debits vs declared lenders", status="consistent",
        severity="info", confidence=0.6, source_document_ids=docs,
        description="All identifiable loan/EMI debits in bank statements relate to declared lenders.",
        details={"all_servicing": {k: round(v, 2) for k, v in by_cp.items()}},
    )]


def check_circular(inp: ReconInputs, tol) -> list[ReconItem]:
    credits = [t for t in inp.bank_txns if t.get("credit") and t.get("counterparty")]
    debits = [t for t in inp.bank_txns if t.get("debit") and t.get("counterparty")]
    if not credits or not debits:
        return []
    total_credits = sum(float(t["credit"]) for t in inp.bank_txns if t.get("credit")) or 1.0
    min_amount = max(100000.0, total_credits * 0.002)
    window = timedelta(days=tol["circular_window_days"])
    tol_pct = tol["circular_amount_tolerance_pct"] / 100.0
    debits_by_cp: dict[str, list[dict]] = defaultdict(list)
    for d in debits:
        debits_by_cp[d["counterparty"]].append(d)
    pairs = []
    used: set[int] = set()
    for c in credits:
        amt = float(c["credit"])
        if amt < min_amount:
            continue
        for d in debits_by_cp.get(c["counterparty"], []):
            if id(d) in used:
                continue
            if abs(float(d["debit"]) - amt) <= amt * tol_pct and abs((d["txn_date"] - c["txn_date"]).days) <= window.days:
                used.add(id(d))
                pairs.append({"counterparty": c["counterparty"], "credit_date": c["txn_date"].isoformat(), "credit": amt,
                              "debit_date": d["txn_date"].isoformat(), "debit": float(d["debit"]),
                              "credit_ref": c.get("row_ref"), "debit_ref": d.get("row_ref")})
                break
    if len(pairs) < 2:
        return [ReconItem(
            check_type="circular_transactions", metric="Round-trip (circular) transaction screening", status="consistent",
            severity="info", confidence=0.6, description="No repeated matching inflow/outflow pairs with the same counterparty were detected.",
            source_document_ids=sorted({t["document_id"] for t in inp.bank_txns if t.get("document_id")}),
        )]
    paired_amount = sum(p["credit"] for p in pairs)
    share = paired_amount / total_credits * 100.0
    cps = sorted({p["counterparty"] for p in pairs})
    return [ReconItem(
        check_type="circular_transactions", metric="Round-trip (circular) transaction screening", status="unusual_pattern",
        severity="high" if share >= 10 else "medium", source_a_label="Matched in/out amount", source_a_value=round(paired_amount, 2),
        source_b_label="Total credits", source_b_value=round(total_credits, 2), difference_pct=round(share, 2),
        description=(f"Unusual Pattern: {len(pairs)} credit(s) were followed within {tol['circular_window_days']} days by near-identical "
                     f"debits to the same counterparty ({', '.join(cps[:4])}), totalling {_fmt(paired_amount)} ({share:.1f}% of credits)."),
        risk_implication=("Matching in-and-out flows with the same party can inflate apparent turnover (round-tripping or "
                          "accommodation entries). This is a screening signal only and requires verification."),
        confidence=0.55, suggested_investigation="Verify the commercial nature of these transactions with invoices, contracts and the counterparty's relationship to promoters.",
        source_document_ids=sorted({t["document_id"] for t in inp.bank_txns if t.get("document_id")}), details={"pairs": pairs[:30]},
    )]


def check_cheque_returns(inp: ReconInputs, tol) -> list[ReconItem]:
    rets = [t for t in inp.bank_txns if t.get("category") == "cheque_return"]
    if not rets:
        return []
    own = [t for t in rets if any(k in t["description"].lower() for k in ("i/w", "inward", "insufficient", "ecs", "nach", "ach", "emi"))]
    severity = "high" if len(own) >= 2 else ("medium" if own or len(rets) >= 3 else "low")
    return [ReconItem(
        check_type="cheque_returns", metric="Cheque / mandate returns", status="unusual_pattern", severity=severity,
        source_a_label="Returns of company-issued instruments", source_a_value=float(len(own)),
        source_b_label="All returns", source_b_value=float(len(rets)),
        description=(f"Unusual Pattern: {len(rets)} cheque/mandate return(s) found, of which {len(own)} appear to be the company's own "
                     "payment instruments (inward returns / insufficient funds / mandate returns)."),
        risk_implication="Dishonour of the company's own instruments is a strong liquidity-stress indicator and may expose it to s.138 NI Act proceedings.",
        confidence=0.7, suggested_investigation="Obtain the reasons for each return and confirm whether the payments were subsequently honoured.",
        source_document_ids=sorted({t["document_id"] for t in rets if t.get("document_id")}),
        details={"returns": [{"date": t["txn_date"].isoformat(), "description": t["description"][:120],
                              "amount": t.get("debit") or t.get("credit")} for t in rets[:20]]},
    )]


def check_cash_deposits(inp: ReconInputs, tol) -> list[ReconItem]:
    credits = [t for t in inp.bank_txns if t.get("credit")]
    if len(credits) < 10:
        return []
    total = sum(float(t["credit"]) for t in credits)
    cash = sum(float(t["credit"]) for t in credits if t.get("category") == "cash_deposit")
    share = cash / total * 100 if total else 0
    if share <= tol["cash_deposit_share_pct"]:
        return []
    return [ReconItem(
        check_type="cash_deposit_concentration", metric="Cash deposits as share of credits", status="unusual_pattern",
        severity="medium", source_a_label="Cash deposits", source_a_value=round(cash, 2), source_b_label="Total credits",
        source_b_value=round(total, 2), difference_pct=round(share, 2), confidence=0.6,
        description=f"Unusual Pattern: cash deposits make up {share:.1f}% of all credits.",
        risk_implication="High cash intensity limits the verifiability of turnover for a B2B manufacturing/trading business.",
        suggested_investigation="Understand the business reason for cash receipts and check consistency with the customer profile.",
        source_document_ids=sorted({t["document_id"] for t in credits if t.get("document_id")}),
    )]


def check_balance_vs_fs(inp: ReconInputs, tol) -> list[ReconItem]:
    out = []
    dated = sorted([t for t in inp.bank_txns if t.get("balance") is not None], key=lambda t: t["txn_date"])
    if not dated:
        return out
    for label, stmt in inp.statements.items():
        pend = stmt.get("_period_end")
        cash = _val(stmt, "cash_and_equivalents")
        if not pend or cash is None:
            continue
        if not (dated[0]["txn_date"] <= pend <= dated[-1]["txn_date"]):
            continue
        before = [t for t in dated if t["txn_date"] <= pend]
        bal = float(before[-1]["balance"])
        if bal > cash * 1.05 and bal - cash > 100000:
            out.append(ReconItem(
                check_type="balance_vs_fs", metric="Bank balance at year end vs cash in balance sheet", status="potential_discrepancy",
                severity="medium", period=label, source_a_label="Bank closing balance (statement)", source_a_value=bal,
                source_b_label="Cash and cash equivalents (balance sheet)", source_b_value=cash, difference=round(bal - cash, 2),
                difference_pct=_pct(bal, cash), confidence=0.6,
                description=f"Potential Discrepancy: the analysed account alone held {_fmt(bal)} on {pend.isoformat()}, more than total cash reported in the balance sheet ({_fmt(cash)}).",
                risk_implication="Cash may be mis-stated or the statement may belong to a different entity/account.",
                suggested_investigation="Confirm the account holder name and obtain the bank balance confirmation used by the auditor.",
                source_document_ids=sorted(set(_docs(stmt.get("cash_and_equivalents"))) | {before[-1].get("document_id")} - {None}),
            ))
    return out


ALL_CHECKS = (
    check_gst_vs_bank, check_itc, check_filing_pattern, check_gst_vs_fs, check_fs_vs_bank, check_cash_flow_support,
    check_debt, check_loan_servicing, check_circular, check_cheque_returns, check_cash_deposits, check_balance_vs_fs,
)


def run_reconciliation(inp: ReconInputs, tolerances: dict | None = None) -> list[ReconItem]:
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    for k, v in list(tol.items()):
        if isinstance(v, list):
            tol[k] = tuple(v)
    items: list[ReconItem] = []
    for check in ALL_CHECKS:
        items.extend(check(inp, tol))
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    items.sort(key=lambda i: (order.get(i.severity, 5), i.check_type))
    return items
