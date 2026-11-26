"""Lexicons for Indian corporate-finance documents.

Patterns are matched against a *normalised* line label (lower-case, numbering removed,
'&' → 'and'). Order matters: the first matching metric wins for a label.
"""
from __future__ import annotations

import re

# metric_key -> (display name, [label regexes])
METRIC_PATTERNS: list[tuple[str, str, list[str]]] = [
    ("gross_revenue", "Gross revenue", [r"gross (revenue|sales|turnover)( from operations)?", r"revenue from operations \(gross\)"]),
    ("revenue_from_operations", "Revenue from operations", [
        r"(net )?revenue from operations( \(net\))?", r"income from operations", r"net sales", r"sales( \(net\))?",
        r"(net )?turnover", r"total operating (income|revenue)", r"operating revenues?", r"net revenue",
    ]),
    ("other_income", "Other income", [r"other (operating )?income"]),
    ("total_income", "Total income", [r"total (income|revenue)"]),
    ("cost_of_goods_sold", "Cost of materials / goods sold", [
        r"cost of (raw )?materials? consumed", r"cost of goods sold", r"cost of sales", r"cost of revenue",
    ]),
    ("purchases", "Purchases of stock-in-trade", [r"purchases?( of stock[- ]in[- ]trade| of traded goods)?"]),
    ("employee_expense", "Employee benefits expense", [r"employee benefits? expenses?", r"staff costs?"]),
    ("finance_costs", "Finance costs", [
        r"finance costs?", r"interest expenses?", r"interest and finance (charges|costs)", r"finance charges",
        r"interest on borrowings",
    ]),
    ("depreciation", "Depreciation and amortisation", [r"depreciation( and amorti[sz]ation)?( expenses?)?"]),
    ("total_expenses", "Total expenses", [r"total expenses?"]),
    ("ebitda", "EBITDA", [
        r"ebitda", r"pbdit", r"operating profit before depreciation.*",
        r"earnings before interest,? tax(es)?,? depreciation and amorti[sz]ation",
    ]),
    ("ebit", "EBIT", [r"ebit", r"pbit", r"(profit|earnings) before interest and tax(es|ation)?"]),
    ("pbt", "Profit before tax", [
        r"(profit|profit ?/ ?\(loss\)|\(loss\) ?/ ?profit|profit \(loss\)) before tax(ation)?", r"pbt",
    ]),
    ("tax_expense", "Tax expense", [r"(total )?(income )?tax expenses?", r"provision for tax(ation)?"]),
    ("pat", "Profit after tax", [
        r"(profit|profit ?/ ?\(loss\)|\(loss\) ?/ ?profit|net profit) (after tax(ation)?|for the (year|period))",
        r"pat", r"net profit", r"profit after tax(ation)?",
    ]),
    ("total_assets", "Total assets", [r"total assets"]),
    ("current_assets", "Current assets", [r"total current assets", r"current assets"]),
    ("non_current_assets", "Non-current assets", [r"total non[- ]current assets"]),
    ("inventory", "Inventories", [r"inventories", r"inventory", r"stock[- ]in[- ]trade", r"closing stock"]),
    ("trade_receivables", "Trade receivables", [r"trade receivables?", r"sundry debtors", r"debtors", r"accounts receivables?"]),
    ("cash_and_equivalents", "Cash and cash equivalents", [
        r"cash and cash equivalents( at the end of the (year|period))?", r"cash and bank balances?",
    ]),
    ("current_liabilities", "Current liabilities", [r"total current liabilities", r"current liabilities"]),
    ("non_current_liabilities", "Non-current liabilities", [r"total non[- ]current liabilities"]),
    ("trade_payables", "Trade payables", [r"trade payables?", r"sundry creditors", r"creditors", r"accounts payables?"]),
    ("long_term_borrowings", "Long-term borrowings", [
        r"long[- ]term borrowings", r"non[- ]current borrowings", r"borrowings \(non[- ]current\)", r"term loans? from banks?",
    ]),
    ("short_term_borrowings", "Short-term borrowings", [
        r"short[- ]term borrowings", r"current borrowings", r"borrowings \(current\)",
        r"working capital (loans?|borrowings|facilities)", r"cash credit( facilities)?",
    ]),
    ("total_debt", "Total borrowings", [r"total (borrowings|debt|outstanding borrowings)"]),
    ("share_capital", "Share capital", [r"(equity )?share capital"]),
    ("reserves_and_surplus", "Reserves and surplus / other equity", [r"reserves and surplus", r"other equity"]),
    ("shareholders_equity", "Shareholders' equity (net worth)", [
        r"total equity", r"(total )?shareholders'? funds", r"net worth", r"tangible net worth",
        r"equity attributable to (the )?(equity holders|owners).*",
    ]),
    ("total_equity_and_liabilities", "Total equity and liabilities", [r"total equity and liabilities"]),
    ("operating_cash_flow", "Operating cash flow", [
        r"net cash (flow )?(generated )?(from|used in|\(used in\) ?/ ?generated from|generated from ?/ ?\(used in\)|from ?/ ?\(used in\)) operating activities.*",
        r"cash (flow|generated) from operat(ing activities|ions)", r"net cash from operations",
    ]),
    ("investing_cash_flow", "Investing cash flow", [
        r"net cash (flow )?(generated )?(from|used in|\(used in\) ?/ ?generated from|generated from ?/ ?\(used in\)|from ?/ ?\(used in\)) investing activities.*",
    ]),
    ("financing_cash_flow", "Financing cash flow", [
        r"net cash (flow )?(generated )?(from|used in|\(used in\) ?/ ?generated from|generated from ?/ ?\(used in\)|from ?/ ?\(used in\)) financing activities.*",
    ]),
    ("interest_paid", "Interest paid", [r"interest paid"]),
    ("contingent_liabilities", "Contingent liabilities", [r"(total )?contingent liabilities( not provided for)?"]),
    ("related_party_transactions", "Related-party transactions", [r"(total )?(transactions with )?related part(y|ies)( transactions)?"]),
    ("guarantees_given", "Guarantees given", [r"(corporate )?guarantees? (given|issued|outstanding)"]),
    ("closing_bank_balance", "Closing bank balance", [r"closing (bank )?balance", r"balance as at (the )?end"]),
    ("gst_turnover", "GST turnover (taxable value)", [r"(aggregate|total) (taxable )?turnover", r"total taxable value", r"taxable outward supplies"]),
    ("itr_gross_receipts", "Gross receipts / turnover per ITR", [r"(sales ?/ ?)?(gross receipts|turnover ?/ ?gross receipts)( of business)?"]),
    ("itr_total_income", "Total income per ITR", [r"gross total income", r"total income \(as computed\)"]),
    ("itr_tax_paid", "Tax paid per ITR", [r"total tax(es)? paid", r"tax payable"]),
    ("sanctioned_amount", "Sanctioned limit", [r"sanctioned (limit|amount)", r"sanction amount", r"facility amount", r"limit sanctioned"]),
    ("outstanding_amount", "Outstanding amount", [r"(amount |principal )?outstanding( balance| amount)?( as on.*)?"]),
    ("promoter_holding_pct", "Promoter & promoter group holding (%)", [r"(total )?(shareholding of )?promoters?( and| &) promoter group", r"promoter holding"]),
    ("promoter_pledge_pct", "Promoter shares pledged (%)", [r"(shares )?pledged( or otherwise encumbered)?( shares)?"]),
]

COMPILED_METRIC_PATTERNS: list[tuple[str, str, list[re.Pattern]]] = [
    (key, name, [re.compile(r"^" + p + r"$") for p in pats]) for key, name, pats in METRIC_PATTERNS
]

METRIC_NAMES = {key: name for key, name, _ in METRIC_PATTERNS}
METRIC_NAMES.update({"interest_rate": "Interest rate (%)", "credit_rating": "Credit rating", "tenure_months": "Tenure (months)"})

PERCENT_METRICS = {"promoter_holding_pct", "promoter_pledge_pct", "interest_rate"}

# Metrics that only make sense for specific document categories.
CATEGORY_ONLY_METRICS = {
    "itr_gross_receipts": {"income_tax_returns"},
    "itr_total_income": {"income_tax_returns"},
    "itr_tax_paid": {"income_tax_returns"},
    "gst_turnover": {"gst_returns"},
    "sanctioned_amount": {"sanction_letters", "credit_rating_reports"},
    "outstanding_amount": {"sanction_letters", "credit_rating_reports", "bank_statements"},
    "promoter_holding_pct": {"shareholding_disclosures", "annual_reports"},
    "promoter_pledge_pct": {"shareholding_disclosures", "annual_reports"},
    "closing_bank_balance": {"bank_statements"},
}

# --- Document classification lexicon: category -> [(pattern, weight)] ---
CLASSIFIER_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "gst_returns": [(r"\bgstr[- ]?(1|3b|2a|2b|9)\b", 4), (r"goods and services tax", 3), (r"\bgstin\b", 1.5),
                    (r"input tax credit|\bitc\b", 2), (r"outward (taxable )?supplies", 2.5), (r"return period|tax period", 1.5)],
    "income_tax_returns": [(r"\bitr[- ]?[1-7]\b", 4), (r"income tax return", 4), (r"assessment year", 3),
                           (r"acknowledg(e)?ment number", 2), (r"gross total income", 2), (r"income[- ]tax department", 2)],
    "bank_statements": [(r"statement of account", 4), (r"opening balance", 2), (r"closing balance", 1.5),
                        (r"withdrawals?|debits?", 1), (r"deposits?|credits?", 1), (r"narration|particulars|chq|cheque", 1.5),
                        (r"\bifsc\b", 2), (r"account (no|number)", 1.5)],
    "financial_statements": [(r"balance sheet", 3), (r"statement of profit and loss|profit and loss (account|statement)", 3),
                             (r"cash flow statement|statement of cash flows", 3), (r"notes (forming part|to the financial statements)", 2),
                             (r"revenue from operations", 1.5), (r"total equity and liabilities", 2)],
    "annual_reports": [(r"annual report", 4), (r"directors'? report|board'?s report", 3), (r"management discussion and analysis", 3),
                       (r"corporate governance", 2), (r"chairman'?s (message|letter)", 2), (r"notice of (the )?annual general meeting", 2)],
    "board_minutes": [(r"minutes of (the )?(meeting|proceedings)", 5), (r"board of directors", 1.5), (r"resolved that", 3),
                      (r"quorum", 2), (r"chairman of the meeting|leave of absence", 2)],
    "credit_rating_reports": [(r"rating rationale", 5), (r"rating action", 3), (r"\b(crisil|icra|care ratings|india ratings|acuit[eé]|brickwork|infomerics)\b", 3),
                              (r"outlook\s*[:\-]?\s*(stable|negative|positive|developing)", 2), (r"long[- ]term rating|short[- ]term rating", 2)],
    "sanction_letters": [(r"sanction letter", 5), (r"(we are pleased to|has been) sanction(ed)?", 3), (r"terms and conditions", 1.5),
                         (r"rate of interest|roi", 1.5), (r"repayment (schedule|terms)", 1.5), (r"security\s*[:\-]", 1), (r"processing fee", 1.5)],
    "shareholding_disclosures": [(r"shareholding pattern", 5), (r"category of shareholders?", 3), (r"promoters? (and|&) promoter group", 3),
                                 (r"% of total (no\.? of )?shares", 2), (r"pledged or otherwise encumbered", 2)],
    "legal_notices": [(r"legal notice", 5), (r"notice under section", 3), (r"\bnclt\b|national company law tribunal", 3),
                      (r"insolvency and bankruptcy|\bibc\b", 3), (r"(petition|plaintiff|respondent|petitioner)", 2),
                      (r"section 138 of the negotiable instruments", 3), (r"arbitra(tion|l tribunal)", 2), (r"demand notice", 3)],
}

# --- Qualitative signals detected in document text ---
# key -> (label, risk area, severity, [patterns])
DOCUMENT_SIGNALS: list[tuple[str, str, str, str, list[str]]] = [
    ("going_concern_doubt", "Auditor highlights material uncertainty about going concern", "governance", "critical",
     [r"material uncertainty related to going concern", r"significant doubt (on|about) the (company'?s )?ability to continue as a going concern"]),
    ("qualified_audit_opinion", "Qualified / adverse audit opinion", "governance", "high",
     [r"\bqualified opinion\b", r"\badverse opinion\b", r"basis for qualified opinion", r"disclaimer of opinion"]),
    ("emphasis_of_matter", "Emphasis of matter in audit report", "governance", "medium", [r"emphasis of matter"]),
    ("auditor_resignation", "Auditor resignation", "governance", "high", [r"resignation of (the )?(statutory )?auditors?", r"auditors? (has|have) resigned"]),
    ("director_resignation", "Director / KMP resignation", "governance", "medium",
     [r"resignation of (mr\.?|ms\.?|mrs\.?|shri|smt\.?)?\s?[a-z .]{2,40}\s?(as|from the (post|office) of) (a )?(director|cfo|chief financial officer|managing director|independent director)",
      r"(cfo|chief financial officer|independent director) (has )?resigned"]),
    ("related_party_approval", "Material related-party transaction approved", "governance", "medium",
     [r"related party transactions? (with|in favour of)", r"approval (of|for) (material )?related party transactions?"]),
    ("loan_default", "Reference to default / overdue on borrowings", "financial_distress", "high",
     [r"\b(default(ed)? (in|on) (repayment|payment)|delay(s|ed)? in (servicing|repayment) of (debt|loans?|interest))\b",
      r"\bspecial mention account\b|\bsma[- ]?[012]\b", r"\bnon[- ]performing asset\b|\bclassified as npa\b"]),
    ("restructuring", "Debt restructuring / one-time settlement", "financial_distress", "high",
     [r"\b(debt )?restructuring\b", r"one[- ]time settlement|\bots\b proposal", r"\bcorporate debt restructuring\b"]),
    ("insolvency_proceeding", "Insolvency / NCLT proceeding", "legal", "critical",
     [r"section [79] of the insolvency and bankruptcy code", r"corporate insolvency resolution process|\bcirp\b",
      r"petition (filed )?(before|with) the (hon'?ble )?(national company law tribunal|nclt)"]),
    ("winding_up", "Winding-up petition", "legal", "critical", [r"winding[- ]up petition", r"petition for winding up"]),
    ("cheque_dishonour", "Cheque dishonour (NI Act s.138)", "legal", "high",
     [r"section 138 of the negotiable instruments act", r"dishonou?r(ed)? (of )?cheques?", r"cheque (was )?returned unpaid"]),
    ("recovery_suit", "Recovery suit / DRT proceeding", "legal", "high",
     [r"recovery (suit|proceedings?)", r"debts? recovery tribunal|\bdrt\b", r"sarfaesi"]),
    ("tax_demand", "Tax demand / dispute", "compliance", "medium",
     [r"(show cause|demand) notice (under|u/s)", r"tax demand of", r"(gst|income tax) (demand|dispute|notice)"]),
    ("regulatory_penalty", "Regulatory penalty", "regulatory", "high",
     [r"penalty (of|amounting to) (₹|rs\.?|inr)", r"(sebi|rbi|pollution control board|nclt|roc) (has )?(imposed|levied) (a )?penalty"]),
    ("rating_downgrade", "Credit rating downgrade", "credit_rating", "high",
     [r"\bdowngraded?\b", r"rating (has been )?revised (downward|from .* to)", r"outlook revised to negative"]),
    ("rating_watch_negative", "Rating watch with negative implications", "credit_rating", "high",
     [r"(rating )?watch with negative implications", r"credit watch negative"]),
    ("rating_upgrade", "Credit rating upgrade", "credit_rating", "positive", [r"\bupgraded?\b"]),
    ("capacity_constraint", "Operational disruption (shutdown, strike, fire)", "operational", "medium",
     [r"\b(plant|factory|unit) (shut ?down|closure)\b", r"\blabou?r (unrest|strike)\b", r"\bfire (accident|incident)\b", r"\block[- ]?out\b"]),
]

COMPILED_SIGNALS = [
    (key, label, area, severity, [re.compile(p, re.I) for p in pats]) for key, label, area, severity, pats in DOCUMENT_SIGNALS
]

RATING_SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "C+", "C", "C-", "D"]
RATING_AGENCIES = r"(CRISIL|ICRA|CARE|IND|India Ratings|Acuit[eé]|ACUITE|BWR|Brickwork|Infomerics|IVR)"
RATING_RE = re.compile(
    r"[\[\(]?\s*" + RATING_AGENCIES + r"\s*[\]\)]?\s*(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|BB\+|BB-|BB|B\+|B-|B|C\+|C-|C|D)(?![A-Za-z])"
    r"(?:\s*[\(/;,]\s*(?:outlook\s*[:\-]?\s*)?(Stable|Negative|Positive|Developing))?",
    re.I,
)


def rating_rank(grade: str) -> int | None:
    g = grade.upper().strip()
    return RATING_SCALE.index(g) if g in RATING_SCALE else None


def normalise_label(raw: str) -> str:
    s = raw.lower().replace("&", " and ").replace("’", "'").replace("‘", "'")
    s = re.sub(r"^\s*(?:\(?[ivxlcdm]{1,5}[.)]|\(?[a-z][.)]|\d+(?:\.\d+)*[.)]?)\s+", "", s)
    s = re.sub(r"\(\s*refer note\s*\d+\s*\)", "", s)
    s = re.sub(r"\bnote\s*\d+\b", "", s)
    s = re.sub(r"[:*#]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-–")
    return s


def match_metric(label: str, category: str | None = None) -> tuple[str, str] | None:
    norm = normalise_label(label)
    if not norm or len(norm) > 140:
        return None
    for key, name, patterns in COMPILED_METRIC_PATTERNS:
        allowed = CATEGORY_ONLY_METRICS.get(key)
        if allowed is not None and category not in allowed:
            continue
        if any(p.match(norm) for p in patterns):
            return key, name
    return None
