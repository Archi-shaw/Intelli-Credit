"""Deterministic generator for SYNTHETIC demonstration documents.

Every file is fictitious, labelled "SYNTHETIC DEMONSTRATION DATA" and describes no real business
or person. Documents are realistic enough to exercise the real extraction pipeline (they are
parsed exactly like user uploads — nothing is inserted into the database directly).

Cases:
  healthy       — consistent revenue, moderate leverage, positive operating cash flow.
  inconsistent  — GST turnover far above bank credits, round-trip transfers, heavy cash deposits.
  stressed      — losses, negative net worth, high leverage, negative operating cash flow, cheque returns.
"""
from __future__ import annotations

import csv
import io
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

SYN = "SYNTHETIC DEMONSTRATION DATA — fictitious entity, not a real borrower"
MONTHS = [(2023, m) for m in range(4, 13)] + [(2024, m) for m in range(1, 4)]


@dataclass
class Case:
    key: str
    company: dict
    application: dict
    # Financial statements in ₹ lakh: {line: (FY2023-24, FY2022-23)}
    pl: dict
    bs: dict
    cf: dict
    monthly_gst_lakh: list[float]
    monthly_bank_business_lakh: list[float]
    extras: dict = field(default_factory=dict)
    research: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)


def _cases() -> list[Case]:
    healthy_gst = [205, 198, 212, 220, 208, 215, 231, 196, 204, 222, 210, 239]  # ≈ ₹25.6 Cr
    return [
        Case(
            key="healthy",
            company={"name": "Sahyadri Precision Components Pvt Ltd (Synthetic)", "industry": "Auto Components",
                     "business_type": "Private Limited", "pan": "AAKCS9001P", "gstin": "27AAKCS9001P1Z5",
                     "cin": "U29200MH2009PTC190001", "incorporation_date": date(2009, 6, 15),
                     "registered_address": "Plot 14, Synthetic Industrial Estate, Pune, Maharashtra (fictitious)",
                     "headquarters": "Pune", "annual_turnover": 2.46e8, "contact_person": "Demo Contact A",
                     "business_description": "SYNTHETIC: Manufacturer of precision machined components for two-wheeler "
                                             "OEMs. Fictitious profile generated for demonstration.",
                     "directors": [("Demo Director One", "90000001", "Managing Director"),
                                   ("Demo Director Two", "90000002", "Director")],
                     "promoters": [("Demo Promoter One", 62.0, 22), ("Demo Promoter Two", 18.0, 15)]},
            application={"loan_type": "term_loan", "requested_amount": 3.0e7, "tenure_months": 60,
                         "purpose": "Purchase of two CNC machining centres to expand capacity (synthetic request).",
                         "collateral": [{"type": "property", "description": "Factory land and building (synthetic)",
                                         "value": 6.5e7}, {"type": "plant_machinery", "description": "Machinery financed",
                                                            "value": 3.2e7}],
                         "existing_borrowings": [{"lender": "Synthetic Co-operative Bank", "facility_type": "term_loan",
                                                  "sanctioned_amount": 1.8e7, "outstanding_amount": 1.2e7,
                                                  "interest_rate": 9.75, "remaining_tenure_months": 36},
                                                 {"lender": "Synthetic Co-operative Bank", "facility_type": "cash_credit",
                                                  "sanctioned_amount": 1.5e7, "outstanding_amount": 1.1e7,
                                                  "interest_rate": 10.25}]},
            pl={"Revenue from operations": (2460.40, 2185.10), "Other income": (18.20, 14.60),
                "Total income": (2478.60, 2199.70), "Cost of materials consumed": (1452.30, 1306.80),
                "Employee benefits expense": (296.40, 268.20), "Finance costs": (24.60, 26.40),
                "Depreciation and amortisation expense": (78.60, 71.90), "Other expenses": (335.70, 308.30),
                "Total expenses": (2187.60, 1981.60), "Profit before tax": (291.00, 218.10),
                "Tax expense": (73.20, 54.90), "Profit for the year": (217.80, 163.20)},
            bs={"Share capital": (150.00, 150.00), "Reserves and surplus": (1046.30, 828.50),
                "Total equity": (1196.30, 978.50), "Long-term borrowings": (120.00, 180.00),
                "Short-term borrowings": (110.00, 95.00), "Trade payables": (238.40, 214.10),
                "Other current liabilities": (61.20, 58.30), "Total current liabilities": (409.60, 367.40),
                "Total equity and liabilities": (1725.90, 1525.90), "Property, plant and equipment": (812.40, 742.10),
                "Inventories": (318.70, 286.50), "Trade receivables": (402.60, 361.20),
                "Cash and cash equivalents": (122.80, 88.60), "Other current assets": (69.40, 47.50),
                "Total current assets": (913.50, 783.80), "Total assets": (1725.90, 1525.90)},
            cf={"Net cash generated from operating activities": (268.40, 204.70),
                "Net cash used in investing activities": (-148.90, -122.40),
                "Net cash used in financing activities": (-85.30, -61.20)},
            monthly_gst_lakh=healthy_gst,
            monthly_bank_business_lakh=[round(v * 1.04, 2) for v in healthy_gst],
            research=[{"category": "industry_outlook", "title": "Synthetic sector note: two-wheeler demand steady",
                       "severity": "info", "sentiment": "positive", "published": "2024-08-12",
                       "summary": "SYNTHETIC: Illustrative sector note indicating stable OEM demand. Not real-world information."}],
            observations=[{"category": "factory_inspection", "sub_category": "factory_utilization", "title": "Plant visit (synthetic)",
                           "notes": "Synthetic site visit: plant operating at 82% capacity utilisation, machines well maintained, "
                                    "inventory records reconciled with the stock register.", "rating": 4},
                          {"category": "management_assessment", "sub_category": "management_quality", "title": "Promoter meeting (synthetic)",
                           "notes": "Synthetic meeting notes: promoters experienced, clear expansion plan, timely information sharing.",
                           "rating": 4}],
        ),
        Case(
            key="inconsistent",
            company={"name": "Ganga Textile Traders Pvt Ltd (Synthetic)", "industry": "Textiles",
                     "business_type": "Private Limited", "pan": "AAGCG4002K", "gstin": "09AAGCG4002K1Z2",
                     "cin": "U17100UP2015PTC070002", "incorporation_date": date(2015, 2, 3),
                     "registered_address": "Unit 7, Synthetic Textile Market, Kanpur, Uttar Pradesh (fictitious)",
                     "headquarters": "Kanpur", "annual_turnover": 9.6e7, "contact_person": "Demo Contact B",
                     "business_description": "SYNTHETIC: Wholesale trader of cotton and blended fabrics. Fictitious profile.",
                     "directors": [("Demo Director Three", "90000003", "Director")],
                     "promoters": [("Demo Promoter Three", 74.0, 11)]},
            application={"loan_type": "working_capital", "requested_amount": 1.5e7, "tenure_months": 12,
                         "purpose": "Working capital for seasonal fabric procurement (synthetic request).",
                         "collateral": [{"type": "inventory", "description": "Hypothecation of stock (synthetic)", "value": 1.4e7}],
                         "existing_borrowings": []},
            pl={"Revenue from operations": (985.20, 902.60), "Other income": (3.10, 2.40),
                "Total income": (988.30, 905.00), "Purchases of stock-in-trade": (861.40, 792.30),
                "Employee benefits expense": (38.20, 35.10), "Finance costs": (14.60, 12.90),
                "Depreciation and amortisation expense": (6.20, 5.80), "Other expenses": (31.70, 28.40),
                "Total expenses": (952.10, 874.50), "Profit before tax": (36.20, 30.50),
                "Tax expense": (9.30, 7.80), "Profit for the year": (26.90, 22.70)},
            bs={"Share capital": (50.00, 50.00), "Reserves and surplus": (138.40, 111.50),
                "Total equity": (188.40, 161.50), "Long-term borrowings": (22.00, 28.00),
                "Short-term borrowings": (96.00, 84.00), "Trade payables": (162.30, 140.20),
                "Total current liabilities": (271.50, 236.90), "Total equity and liabilities": (481.90, 426.40),
                "Inventories": (196.80, 171.40), "Trade receivables": (214.60, 189.30),
                "Cash and cash equivalents": (9.40, 12.80), "Total current assets": (430.70, 380.10),
                "Total assets": (481.90, 426.40)},
            cf={"Net cash generated from operating activities": (52.40, 41.60),
                "Net cash used in investing activities": (-6.10, -4.20),
                "Net cash used in financing activities": (-49.70, -33.20)},
            monthly_gst_lakh=[80.0] * 12,  # ≈ ₹9.6 Cr declared outward supplies
            monthly_bank_business_lakh=[12.0] * 12,  # ≈ ₹1.44 Cr genuine receipts
            extras={"round_trip": True, "cash_deposits": True, "itc_excess": True},
            observations=[{"category": "business_environment", "sub_category": "customer_concentration",
                           "title": "Market enquiry (synthetic)",
                           "notes": "Synthetic enquiry: 65% of sales comes from a single buyer; warehouse stock appeared lower than "
                                    "book inventory during the visit.", "rating": 2}],
        ),
        Case(
            key="stressed",
            company={"name": "Vindhya Agro Foods Pvt Ltd (Synthetic)", "industry": "Food Processing",
                     "business_type": "Private Limited", "pan": "AAVCV7003M", "gstin": "23AAVCV7003M1Z8",
                     "cin": "U15400MP2012PTC030003", "incorporation_date": date(2012, 9, 21),
                     "registered_address": "Survey 22, Synthetic Food Park, Indore, Madhya Pradesh (fictitious)",
                     "headquarters": "Indore", "annual_turnover": 4.1e7, "contact_person": "Demo Contact C",
                     "business_description": "SYNTHETIC: Processor of packaged pulses and snacks. Fictitious profile.",
                     "directors": [("Demo Director Four", "90000004", "Managing Director"),
                                   ("Demo Director Five", "90000005", "Director")],
                     "promoters": [("Demo Promoter Four", 51.0, 9)]},
            application={"loan_type": "term_loan", "requested_amount": 2.5e7, "tenure_months": 84,
                         "purpose": "Refinancing of existing debt and plant modernisation (synthetic request).",
                         "collateral": [{"type": "plant_machinery", "description": "Existing plant (synthetic)",
                                         "value": 1.2e7, "existing_charge": True}],
                         "existing_borrowings": [{"lender": "Synthetic Regional Bank", "facility_type": "term_loan",
                                                  "outstanding_amount": 3.4e7, "interest_rate": 12.5,
                                                  "remaining_tenure_months": 48},
                                                 {"lender": "Synthetic NBFC Ltd", "facility_type": "cash_credit",
                                                  "outstanding_amount": 1.6e7, "interest_rate": 14.0}]},
            pl={"Revenue from operations": (412.60, 498.30), "Other income": (1.80, 2.20),
                "Total income": (414.40, 500.50), "Cost of materials consumed": (318.90, 362.40),
                "Employee benefits expense": (58.40, 55.10), "Finance costs": (64.20, 52.80),
                "Depreciation and amortisation expense": (31.60, 30.90), "Other expenses": (49.70, 44.20),
                "Total expenses": (522.80, 545.40), "Profit before tax": (-108.40, -44.90),
                "Tax expense": (0.00, 0.00), "Profit for the year": (-108.40, -44.90)},
            bs={"Share capital": (100.00, 100.00), "Reserves and surplus": (-162.70, -54.30),
                "Total equity": (-62.70, 45.70), "Long-term borrowings": (340.00, 312.00),
                "Short-term borrowings": (160.00, 128.00), "Trade payables": (118.60, 92.40),
                "Total current liabilities": (318.20, 251.30), "Total equity and liabilities": (595.50, 609.00),
                "Inventories": (96.40, 118.20), "Trade receivables": (84.30, 101.60),
                "Cash and cash equivalents": (3.10, 7.40), "Total current assets": (201.70, 248.30),
                "Total assets": (595.50, 609.00)},
            cf={"Net cash generated from operating activities": (-38.60, 12.40),
                "Net cash used in investing activities": (-4.20, -18.60),
                "Net cash used in financing activities": (40.10, 9.80)},
            monthly_gst_lakh=[36, 34, 35, 33, 37, 34, 32, 35, 33, 34, 36, 33],
            monthly_bank_business_lakh=[34, 33, 33, 31, 35, 33, 30, 33, 31, 33, 34, 31],
            extras={"cheque_returns": True, "audit_emphasis": True},
            research=[{"category": "financial_distress", "title": "Synthetic note: delayed supplier payments reported",
                       "severity": "high", "sentiment": "adverse", "published": "2024-09-03",
                       "summary": "SYNTHETIC: Illustrative fixture describing delayed payments to suppliers. Not real-world information."}],
            observations=[{"category": "factory_inspection", "sub_category": "factory_utilization", "title": "Plant visit (synthetic)",
                           "notes": "Synthetic site visit: plant operating at 38% capacity; one production line idle due to "
                                    "working-capital shortage.", "rating": 2}],
        ),
    ]


# ------------------------------------------------------------------ renderers

def _fs_pdf(case: Case) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from reporting.generators.pdf_report import _fonts

    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    name = case.company["name"]

    def page(title: str, rows: dict, note: str | None = None):
        y = 800
        c.setFont("DejaVu", 7)
        c.drawString(40, 820, SYN)
        c.setFont("DejaVu-Bold", 12)
        c.drawString(40, y, name)
        y -= 18
        c.setFont("DejaVu-Bold", 10)
        c.drawString(40, y, title)
        y -= 14
        c.setFont("DejaVu", 9)
        c.drawString(40, y, "(All amounts in ₹ Lakhs, unless otherwise stated)")
        y -= 22
        c.setFont("DejaVu-Bold", 9)
        c.drawString(40, y, "Particulars")
        c.drawRightString(420, y, "FY 2023-24")
        c.drawRightString(520, y, "FY 2022-23")
        y -= 16
        c.setFont("DejaVu", 9)
        for label, (cur, pri) in rows.items():
            c.drawString(40, y, label)
            c.drawRightString(420, y, _fmt(cur))
            c.drawRightString(520, y, _fmt(pri))
            y -= 15
        if note:
            y -= 12
            for line in note.split("\n"):
                c.drawString(40, y, line)
                y -= 13
        c.showPage()

    page("Statement of Profit and Loss for the year ended 31 March 2024", case.pl)
    page("Balance Sheet as at 31 March 2024", case.bs)
    note = None
    if case.extras.get("audit_emphasis"):
        note = ("Independent Auditor's Report (synthetic extract) — Emphasis of Matter:\n"
                "The company has incurred a net loss and its net worth has been fully eroded. These conditions\n"
                "indicate that a material uncertainty related to going concern exists. (Synthetic text.)")
    page("Cash Flow Statement for the year ended 31 March 2024", case.cf, note)
    c.save()
    return buf.getvalue()


def _fmt(v: float) -> str:
    s = f"{abs(v):,.2f}"
    return f"({s})" if v < 0 else s


def _bank_csv(case: Case, rng: random.Random) -> bytes:
    rows = [["Txn Date", "Description", "Withdrawal (Dr)", "Deposit (Cr)", "Balance"]]
    bal = 1_850_000.0
    for i, (y, m) in enumerate(MONTHS):
        total = case.monthly_bank_business_lakh[i] * 1e5
        weights = [rng.uniform(0.8, 1.2) for _ in range(4)]
        for k, w in enumerate(weights):
            amt = round(total * w / sum(weights), 2)
            bal += amt
            rows.append([date(y, m, 3 + k * 7).strftime("%d/%m/%Y"), f"NEFT CR-CUSTOMER {k + 1} (SYNTHETIC)", "",
                         f"{amt:.2f}", f"{bal:.2f}"])
        payout = round(total * rng.uniform(0.86, 0.95), 2)
        for label, share in (("RTGS DR-SUPPLIER PAYMENT (SYNTHETIC)", 0.7), ("SALARY PAYROLL (SYNTHETIC)", 0.18),
                             ("GST PAYMENT CHALLAN (SYNTHETIC)", 0.12)):
            v = round(payout * share, 2)
            bal -= v
            rows.append([date(y, m, 26 if "SUPPLIER" in label else 28).strftime("%d/%m/%Y"), label, f"{v:.2f}", "", f"{bal:.2f}"])
        emi = {"healthy": 520_000, "inconsistent": 90_000, "stressed": 1_150_000}[case.key]
        bal -= emi
        rows.append([date(y, m, 5).strftime("%d/%m/%Y"), "EMI TERM LOAN REPAYMENT (SYNTHETIC)", f"{emi:.2f}", "", f"{bal:.2f}"])
        if case.extras.get("round_trip") and m in (7, 10, 1):
            for day in (8, 18):
                v = 2_500_000.0
                bal += v
                rows.append([date(y, m, day).strftime("%d/%m/%Y"), "RTGS CR-ZENITH TRADING CO (SYNTHETIC)", "", f"{v:.2f}", f"{bal:.2f}"])
                bal -= v
                rows.append([date(y, m, day + 2).strftime("%d/%m/%Y"), "RTGS DR-ZENITH TRADING CO (SYNTHETIC)", f"{v:.2f}", "", f"{bal:.2f}"])
        if case.extras.get("cash_deposits"):
            v = 380_000.0
            bal += v
            rows.append([date(y, m, 15).strftime("%d/%m/%Y"), "CASH DEPOSIT BY SELF (SYNTHETIC)", "", f"{v:.2f}", f"{bal:.2f}"])
        if case.extras.get("cheque_returns") and m in (8, 11, 2):
            rows.append([date(y, m, 12).strftime("%d/%m/%Y"), "CHQ RETURN INSUFFICIENT FUNDS (SYNTHETIC)", "450000.00", "", f"{bal:.2f}"])
    # Sort chronologically (stable within a day), then recompute the running balance.
    body = sorted(rows[1:], key=lambda r: (r[0][6:], r[0][3:5], r[0][:2]))
    bal = 1_850_000.0
    for r in body:
        bal += (float(r[3]) if r[3] else 0.0) - (float(r[2]) if r[2] else 0.0)
        r[4] = f"{bal:.2f}"
    rows[1:] = body
    out = io.StringIO()
    out.write(f"# {SYN}\n# Statement of Account — {case.company['name']} — 01/04/2023 to 31/03/2024\n")
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("utf-8")


def _gst_csv(case: Case) -> bytes:
    rows = [["Return Type", "Tax Period", "GSTIN", "Taxable Value", "IGST", "CGST", "SGST", "Total Tax",
             "ITC Claimed", "ITC Available", "Filing Date"]]
    for i, (y, m) in enumerate(MONTHS):
        taxable = case.monthly_gst_lakh[i] * 1e5
        tax = round(taxable * 0.12, 2)
        itc_avail = round(tax * 0.72, 2)
        itc_claimed = round(itc_avail * (1.22 if case.extras.get("itc_excess") else 0.98), 2)
        fy, fm = (y, m + 1) if m < 12 else (y + 1, 1)
        late = case.key == "stressed" and m in (9, 12, 3)
        filed = date(fy, fm, 28 if late else 19)
        rows.append(["GSTR-3B", f"{y}-{m:02d}", case.company["gstin"], f"{taxable:.2f}", f"{tax / 2:.2f}", f"{tax / 4:.2f}",
                     f"{tax / 4:.2f}", f"{tax:.2f}", f"{itc_claimed:.2f}", "", filed.isoformat()])
        rows.append(["GSTR-2A", f"{y}-{m:02d}", case.company["gstin"], "", "", "", "", "", "", f"{itc_avail:.2f}", ""])
    out = io.StringIO()
    out.write(f"# {SYN}\n# GST return summary (GSTR-3B / GSTR-2A) — {case.company['name']}\n")
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("utf-8")


def generate(out_dir: str | Path) -> dict[str, dict]:
    """Write all synthetic files; returns {case_key: {"files": [...], "case": Case}}."""
    out = Path(out_dir)
    (out / "research").mkdir(parents=True, exist_ok=True)
    manifest = {}
    for case in _cases():
        rng = random.Random(f"intelli-credit-{case.key}")
        d = out / case.key
        d.mkdir(parents=True, exist_ok=True)
        files = {f"{case.key}_financial_statements_FY2023-24.pdf": (_fs_pdf(case), "financial_statements"),
                 f"{case.key}_bank_statement_FY2023-24.csv": (_bank_csv(case, rng), "bank_statements"),
                 f"{case.key}_gst_returns_FY2023-24.csv": (_gst_csv(case), "gst_returns")}
        for fname, (data, _cat) in files.items():
            (d / fname).write_bytes(data)
        (d / "README.txt").write_text(f"{SYN}\nGenerated by app.seed.synthetic for the '{case.key}' demonstration case.\n")
        (out / "research" / f"{case.key}.json").write_text(json.dumps(case.research, indent=2))
        manifest[case.key] = {"case": case, "files": [(d / f, cat) for f, (_, cat) in files.items()]}
    return manifest


def cases() -> list[Case]:
    return _cases()


if __name__ == "__main__":  # pragma: no cover
    from app.core.config import settings

    for k, v in generate(settings.SYNTHETIC_DATA_DIR).items():
        print(k, [str(p) for p, _ in v["files"]])
