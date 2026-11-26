"""Formatting shared by the PDF and DOCX CAM renderers. Renderers never compute figures."""
from __future__ import annotations

LOAN_TYPES = {
    "term_loan": "Term loan", "working_capital": "Working capital", "cash_credit": "Cash credit", "overdraft": "Overdraft",
    "equipment_finance": "Equipment finance", "project_finance": "Project finance", "letter_of_credit": "Letter of credit",
    "bank_guarantee": "Bank guarantee",
}


def inr(v, missing: str = "Not available") -> str:
    """Indian-style money: crore / lakh for large values, grouped rupees otherwise."""
    if v is None:
        return missing
    v = float(v)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def lakh(v) -> str:
    """Table cells in ₹ lakh."""
    if v is None:
        return "—"
    return f"{float(v) / 1e5:,.2f}"


def ratio(v, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "days":
        return f"{v:.0f} days"
    return f"{v:.2f}x"


def label(s: str | None) -> str:
    return (s or "").replace("_", " ").strip().capitalize() or "—"


def pct(v) -> str:
    return "n/a" if v is None else f"{v:.2f}%"


SECTIONS = [
    "Borrower details", "Business profile", "Loan request", "Documents reviewed", "Financial information summary",
    "Profit and loss analysis", "Balance sheet analysis", "Cash-flow analysis", "Financial ratios",
    "GST and bank reconciliation findings", "Potential discrepancies", "Credit-risk assessment",
    "Explainable scoring factors", "Five Cs of Credit", "Repayment capacity", "Collateral assessment",
    "Recommended loan amount", "Indicative interest-rate calculation", "Recommended tenure", "Risk mitigations",
    "Analyst observations", "Human review and approval status", "Sources and evidence references",
    "Limitations and disclaimers",
]


def evidence_references(c: dict) -> list[str]:
    refs = []
    for d in c["documents"]:
        refs.append(f"[DOC-{d['id']}] {d['name']} — {label(d['category'])}, {d['pages'] or '?'} page(s), "
                    f"extraction: {label(d['status'])}{' (OCR)' if d['ocr_used'] else ''}")
    for r in c["research"]:
        src = r["url"] or "no URL (synthetic fixture)" if r["synthetic"] else (r["url"] or "no URL")
        refs.append(f"[RES] {r['title']} — {r['source']}; {src}; published {r['published'] or 'unknown'}; "
                    f"retrieved {r['retrieved'][:10]}; {label(r['verification'])}")
    return refs


def missing_items(c: dict) -> list[str]:
    risk = c.get("risk") or {}
    return [m.get("message", str(m)) for m in risk.get("missing_information", [])]
