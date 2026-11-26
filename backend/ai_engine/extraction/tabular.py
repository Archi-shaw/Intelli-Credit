"""Structured parsing of bank statements, GST return summaries and shareholding tables."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ai_engine.extraction.units import parse_date_value, parse_month_period, parse_number


@dataclass
class BankTxn:
    txn_date: date
    description: str
    debit: float | None
    credit: float | None
    balance: float | None
    counterparty: str | None
    category: str
    row_ref: str
    is_flagged: bool = False
    flag_reason: str | None = None


@dataclass
class GSTRow:
    return_type: str
    period: str
    gstin: str | None
    taxable_value: float | None
    igst: float | None
    cgst: float | None
    sgst: float | None
    cess: float | None
    total_tax: float | None
    itc_claimed: float | None
    itc_available: float | None
    filing_date: date | None
    row_ref: str


@dataclass
class ShareholdingRow:
    holder_name: str
    holder_type: str
    percentage: float
    shares: int | None
    pledged_pct: float | None


@dataclass
class TableParseResult:
    bank_transactions: list[BankTxn] = field(default_factory=list)
    gst_rows: list[GSTRow] = field(default_factory=list)
    shareholding: list[ShareholdingRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", str(h or "").lower()).strip()


def _find_col(headers: list[str], *candidates: str, exclude: tuple[str, ...] = ()) -> int | None:
    for cand in candidates:
        for i, h in enumerate(headers):
            if re.search(cand, h) and not any(re.search(e, h) for e in exclude):
                return i
    return None


# --- Bank statements -------------------------------------------------------------------------

_CATEGORY_RULES: list[tuple[str, str]] = [
    ("cheque_return", r"\b(chq|cheque|i ?w|o ?w|inward|outward|ecs|nach|ach)?\s*(ret(urn)?|bounce|dishono?u?r|unpaid)\b|insufficient fund|funds insufficient"),
    ("loan_disbursement", r"\b(loan|tl|term loan)\s*(disb|disbursal|disbursement)\b|disbursement of loan"),
    ("loan_repayment", r"\bemi\b|loan (repay|instal|inst)|\btl\b.*(repay|instal)|term loan repay|repayment of loan|principal repay"),
    ("interest_debit", r"\b(int(erest)?\.?\s*(coll|debit|charged|on cc|on od))\b|interest debited"),
    ("tax_payment", r"\b(gst|tds|advance tax|income tax|cbdt|gstn|challan|tax payment)\b"),
    ("salary", r"\b(salary|salaries|payroll|wages)\b"),
    ("inter_account_transfer", r"own a/?c|own account|self transfer|sweep|trf to own|transfer to own|from own a/?c|inter ?account"),
    ("cash_deposit", r"\b(cash dep(osit)?|by cash|cash cr)\b"),
    ("cash_withdrawal", r"\b(cash wdl|cash withdrawal|atm wdl|atm|self chq|to self)\b"),
    ("interest_credit", r"\b(int\.?\s*pd|interest (credit|paid)|int cr|sb int)\b"),
    ("reversal", r"\b(reversal|rev|refund|chargeback)\b"),
]

_STOPWORDS = {
    "neft", "rtgs", "imps", "upi", "cr", "dr", "utr", "trf", "by", "to", "from", "inb", "ib", "mb", "ach", "nach",
    "chq", "cheque", "no", "ref", "txn", "payment", "transfer", "towards", "the", "a", "c", "ac", "pvt", "ltd",
}


def _counterparty(desc: str) -> str | None:
    parts = re.split(r"[/\-|:*]+", desc)
    best = None
    for part in parts:
        p = part.strip()
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z&.]+", p) if w.lower() not in _STOPWORDS]
        if len(words) >= 1 and not re.search(r"\d{5,}", p):
            cand = " ".join(words)
            if len(cand) >= 4 and (best is None or len(cand) > len(best)):
                best = cand
    return best.upper()[:120] if best else None


def categorise_transaction(desc: str, debit: float | None, credit: float | None) -> str:
    d = desc.lower()
    for cat, pattern in _CATEGORY_RULES:
        if re.search(pattern, d):
            return cat
    if credit:
        return "business_receipt"
    if debit:
        return "payment"
    return "other"


def detect_bank_table(headers: list[str]) -> dict | None:
    h = [_norm(x) for x in headers]
    date_i = _find_col(h, r"^(txn |transaction |tran |value |posting )?date$", r"\bdate\b")
    debit_i = _find_col(h, r"withdraw", r"\bdebit", r"^dr\b", r"\bdr amount", exclude=(r"credit",))
    credit_i = _find_col(h, r"deposit", r"\bcredit", r"^cr\b", r"\bcr amount", exclude=(r"debit",))
    amount_i = _find_col(h, r"^amount$", r"txn amount|transaction amount")
    drcr_i = _find_col(h, r"^(dr ?/ ?cr|cr ?/ ?dr|type|txn type)$")
    desc_i = _find_col(h, r"narration", r"description", r"particulars", r"remarks", r"details")
    bal_i = _find_col(h, r"balance")
    if date_i is None or desc_i is None:
        return None
    if (debit_i is not None and credit_i is not None) or (amount_i is not None and drcr_i is not None):
        return {"date": date_i, "debit": debit_i, "credit": credit_i, "amount": amount_i, "drcr": drcr_i,
                "desc": desc_i, "balance": bal_i}
    return None


def parse_bank_rows(rows: list[list], source_label: str) -> tuple[list[BankTxn], list[str]]:
    warnings: list[str] = []
    txns: list[BankTxn] = []
    cols = None
    header_row_idx = None
    for i, row in enumerate(rows[:30]):
        cols = detect_bank_table([str(c or "") for c in row])
        if cols:
            header_row_idx = i
            break
    if not cols:
        return [], []
    skipped = 0
    for j, row in enumerate(rows[header_row_idx + 1:], start=header_row_idx + 2):
        cells = list(row) + [None] * 10
        d = parse_date_value(cells[cols["date"]])
        if d is None:
            if any(c not in (None, "") for c in row):
                skipped += 1
            continue
        desc = str(cells[cols["desc"]] or "").strip()
        debit = credit = None
        if cols["debit"] is not None and cols["credit"] is not None:
            debit = parse_number(cells[cols["debit"]]) if cells[cols["debit"]] not in (None, "") else None
            credit = parse_number(cells[cols["credit"]]) if cells[cols["credit"]] not in (None, "") else None
        else:
            amt = parse_number(cells[cols["amount"]])
            kind = str(cells[cols["drcr"]] or "").strip().lower()
            if amt is not None:
                if kind.startswith("d"):
                    debit = abs(amt)
                elif kind.startswith("c"):
                    credit = abs(amt)
        if debit is not None and debit < 0:
            debit = abs(debit)
        if credit is not None and credit < 0:
            credit = abs(credit)
        debit = debit or None
        credit = credit or None
        if debit is None and credit is None:
            skipped += 1
            continue
        bal = parse_number(cells[cols["balance"]]) if cols["balance"] is not None else None
        cat = categorise_transaction(desc, debit, credit)
        txn = BankTxn(txn_date=d, description=desc[:500], debit=debit, credit=credit, balance=bal,
                      counterparty=_counterparty(desc), category=cat, row_ref=f"{source_label}:row {j}")
        if cat == "cheque_return":
            txn.is_flagged = True
            txn.flag_reason = "Cheque/mandate return recorded in statement"
        txns.append(txn)
    if skipped:
        warnings.append(f"{skipped} bank statement row(s) could not be parsed (missing date or amount) and were skipped.")
    return txns, warnings


# --- GST ----------------------------------------------------------------------------------------

def _return_type_from(text: str) -> str | None:
    t = text.upper().replace(" ", "").replace("-", "")
    for rt in ("GSTR3B", "GSTR2A", "GSTR2B", "GSTR1", "GSTR9"):
        if rt in t:
            return rt
    return None


def parse_gst_rows(rows: list[list], source_label: str, default_return_type: str | None = None) -> tuple[list[GSTRow], list[str]]:
    warnings: list[str] = []
    out: list[GSTRow] = []
    header_idx = None
    h: list[str] = []
    for i, row in enumerate(rows[:20]):
        h = [_norm(c) for c in row]
        if _find_col(h, r"period", r"month") is not None and (
            _find_col(h, r"taxable", r"turnover", r"outward") is not None or _find_col(h, r"itc", r"input tax") is not None
        ):
            header_idx = i
            break
    if header_idx is None:
        return [], []
    c = {
        "type": _find_col(h, r"return type", r"^form$", r"^return$", r"gstr"),
        "period": _find_col(h, r"tax period", r"return period", r"period", r"month"),
        "gstin": _find_col(h, r"gstin"),
        "taxable": _find_col(h, r"taxable value", r"taxable turnover", r"outward taxable", r"taxable", r"turnover"),
        "igst": _find_col(h, r"^igst", r"integrated", exclude=(r"itc",)),
        "cgst": _find_col(h, r"^cgst", r"central", exclude=(r"itc",)),
        "sgst": _find_col(h, r"^sgst", r"^utgst", r"state", exclude=(r"itc",)),
        "cess": _find_col(h, r"^cess", exclude=(r"itc",)),
        "total_tax": _find_col(h, r"total tax", r"tax liability", r"tax payable"),
        "itc_claimed": _find_col(h, r"itc (claimed|availed)", r"itc as per 3b", r"input tax credit (claimed|availed)"),
        "itc_available": _find_col(h, r"itc (available|as per 2a|as per 2b|auto)", r"itc reflected", r"eligible itc"),
        "filing": _find_col(h, r"filing date", r"date of filing", r"filed on", r"arn date"),
    }
    if c["itc_claimed"] is None and c["itc_available"] is None:
        generic_itc = _find_col(h, r"\bitc\b", r"input tax credit")
        if generic_itc is not None:
            if default_return_type in ("GSTR2A", "GSTR2B"):
                c["itc_available"] = generic_itc
            else:
                c["itc_claimed"] = generic_itc
    skipped = 0

    def num(idx, cells):
        return parse_number(cells[idx]) if idx is not None and idx < len(cells) and cells[idx] not in (None, "") else None

    for j, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        cells = list(row)
        period = parse_month_period(cells[c["period"]]) if c["period"] is not None and c["period"] < len(cells) else None
        if not period:
            if any(x not in (None, "") for x in cells):
                skipped += 1
            continue
        rtype = None
        if c["type"] is not None and c["type"] < len(cells):
            rtype = _return_type_from(str(cells[c["type"]] or ""))
        rtype = rtype or default_return_type
        if not rtype:
            skipped += 1
            continue
        igst, cgst, sgst, cess = (num(c[k], cells) for k in ("igst", "cgst", "sgst", "cess"))
        total_tax = num(c["total_tax"], cells)
        if total_tax is None and any(v is not None for v in (igst, cgst, sgst)):
            total_tax = sum(v for v in (igst, cgst, sgst, cess) if v is not None)
        out.append(GSTRow(
            return_type=rtype, period=period,
            gstin=str(cells[c["gstin"]]).strip()[:15] if c["gstin"] is not None and c["gstin"] < len(cells) and cells[c["gstin"]] else None,
            taxable_value=num(c["taxable"], cells), igst=igst, cgst=cgst, sgst=sgst, cess=cess, total_tax=total_tax,
            itc_claimed=num(c["itc_claimed"], cells), itc_available=num(c["itc_available"], cells),
            filing_date=parse_date_value(cells[c["filing"]]) if c["filing"] is not None and c["filing"] < len(cells) else None,
            row_ref=f"{source_label}:row {j}",
        ))
    if skipped:
        warnings.append(f"{skipped} GST row(s) were skipped (no recognisable period or return type).")
    return out, warnings


# --- Shareholding ------------------------------------------------------------------------------

def parse_shareholding_rows(rows: list[list]) -> list[ShareholdingRow]:
    header_idx = None
    h: list[str] = []
    for i, row in enumerate(rows[:15]):
        h = [_norm(c) for c in row]
        if _find_col(h, r"%", r"percent", r"holding") is not None and _find_col(h, r"name", r"category", r"shareholder", r"holder") is not None:
            header_idx = i
            break
    if header_idx is None:
        return []
    name_i = _find_col(h, r"name of (the )?shareholder", r"shareholder", r"holder", r"name", r"category")
    pct_i = _find_col(h, r"% of (total )?(shares|shareholding|holding)", r"shareholding %", r"holding %", r"%", r"percent", exclude=(r"pledg",))
    shares_i = _find_col(h, r"no\.? of shares", r"number of shares", r"shares held", exclude=(r"pledg", r"%"))
    pledge_i = _find_col(h, r"pledg")
    type_i = _find_col(h, r"category", r"type")
    out = []
    for row in rows[header_idx + 1:]:
        cells = list(row) + [None] * 6
        name = str(cells[name_i] or "").strip()
        pct = parse_number(str(cells[pct_i]).replace("%", "")) if cells[pct_i] not in (None, "") else None
        if not name or pct is None or re.search(r"^total\b", name, re.I):
            continue
        cat_text = (str(cells[type_i]) if type_i is not None else name).lower()
        holder_type = "promoter" if "promoter" in cat_text else (
            "institutional" if re.search(r"institution|fii|fpi|mutual fund|bank|insurance", cat_text) else
            "public" if re.search(r"public|individual|retail", cat_text) else "other")
        shares = parse_number(cells[shares_i]) if shares_i is not None else None
        pledged = parse_number(str(cells[pledge_i]).replace("%", "")) if pledge_i is not None and cells[pledge_i] not in (None, "") else None
        out.append(ShareholdingRow(holder_name=name[:200], holder_type=holder_type, percentage=pct,
                                   shares=int(shares) if shares is not None else None, pledged_pct=pledged))
    return out


def parse_tables(tables: list[tuple[str, list[list]]], category: str) -> TableParseResult:
    """tables: [(source_label, rows)]"""
    res = TableParseResult()
    for label, rows in tables:
        if not rows:
            continue
        if category in ("bank_statements", "other"):
            txns, w = parse_bank_rows(rows, label)
            res.bank_transactions.extend(txns)
            res.warnings.extend(w)
        if category in ("gst_returns", "other"):
            default_rt = _return_type_from(label)
            g, w = parse_gst_rows(rows, label, default_rt)
            res.gst_rows.extend(g)
            res.warnings.extend(w)
        if category in ("shareholding_disclosures", "annual_reports"):
            res.shareholding.extend(parse_shareholding_rows(rows))
    return res
