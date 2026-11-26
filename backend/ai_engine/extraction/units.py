"""Number, unit, currency and reporting-period parsing for Indian financial documents."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

UNIT_MULTIPLIERS = {
    "crore": 1e7,
    "lakh": 1e5,
    "million": 1e6,
    "billion": 1e9,
    "thousand": 1e3,
    "units": 1.0,
}

_UNIT_WORDS = (
    r"(crores?|cr\.?|lakhs?|lacs?|lac|lakh|millions?|mn|mio|billions?|bn|thousands?|'000s?|’000s?|000s)"
)
_UNIT_CONTEXT = [
    re.compile(r"(?:₹|rs\.?|inr|rupees)\s*(?:in\s*)?[\(\[]?\s*" + _UNIT_WORDS, re.I),
    re.compile(r"(?:amounts?|figures|all amounts|values?)\s+(?:are\s+)?(?:stated\s+)?in\s+(?:₹|rs\.?|inr|rupees)?\s*" + _UNIT_WORDS, re.I),
    re.compile(r"\(\s*in\s+(?:₹|rs\.?|inr)?\s*" + _UNIT_WORDS + r"\s*\)", re.I),
    re.compile(r"(?:₹|rs\.?|inr)\s+" + _UNIT_WORDS, re.I),
]

_NUM_TOKEN = re.compile(r"(?<![\w.])\(?\s*[-–−]?\s*(?:₹|rs\.?\s*)?\d[\d,]*(?:\.\d+)?\s*\)?(?![\w])", re.I)

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

_PERIOD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bFY\s*'?(\d{4})\s*[-–/]\s*(\d{2,4})\b", re.I), "fy_range"),
    (re.compile(r"\b(20\d{2})\s*[-–/]\s*(\d{2}|20\d{2})\b"), "fy_range"),
    (re.compile(r"\bFY\s*'?(\d{2,4})\b(?!\s*[-–/]\s*\d)", re.I), "fy_single"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r",?\s+(20\d{2})\b", re.I), "dmy_text"),
    (re.compile(r"\b" + _MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b", re.I), "mdy_text"),
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b"), "dmy_num"),
]


@dataclass
class UnitInfo:
    unit: str
    multiplier: float
    explicit: bool
    currency: str


def normalise_unit_word(word: str) -> str:
    w = word.lower().strip(" .()[]")
    if w.startswith(("crore", "cr")):
        return "crore"
    if w.startswith(("lakh", "lac")):
        return "lakh"
    if w.startswith(("million", "mn", "mio")):
        return "million"
    if w.startswith(("billion", "bn")):
        return "billion"
    if w.startswith("thousand") or "000" in w:
        return "thousand"
    return "units"


def detect_currency(text: str) -> str:
    sample = text[:6000]
    if re.search(r"₹|\brs\.?\s|\binr\b|rupees", sample, re.I):
        return "INR"
    if re.search(r"\busd\b|us\$|\$\s?\d", sample, re.I):
        return "USD"
    if re.search(r"\beur\b|€", sample, re.I):
        return "EUR"
    return "INR"


def detect_unit(text: str) -> UnitInfo:
    currency = detect_currency(text)
    for pattern in _UNIT_CONTEXT:
        m = pattern.search(text[:8000])
        if m:
            unit = normalise_unit_word(m.group(1))
            return UnitInfo(unit=unit, multiplier=UNIT_MULTIPLIERS[unit], explicit=True, currency=currency)
    return UnitInfo(unit="units", multiplier=1.0, explicit=False, currency=currency)


def parse_number(token: str) -> float | None:
    """Parse '1,23,456.70', '(4,500)', '-12.5', '₹ 3,000' → float. Returns None if not numeric."""
    if token is None:
        return None
    s = str(token).strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    if s[:1] in "-–−":
        negative = True
        s = s[1:].strip()
    s = s.replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        return None
    val = float(s)
    return -val if negative else val


def find_numbers(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _NUM_TOKEN.finditer(text):
        tok = m.group(0).strip()
        val = parse_number(tok)
        if val is not None:
            out.append((tok, val))
    return out


def fy_label_from_end(end: date) -> str:
    if end.month == 3:
        return f"FY{end.year - 1}-{str(end.year)[2:]}"
    return f"P{end.year}-{end.month:02d}"


def _fy_end(start_year: int) -> date:
    return date(start_year + 1, 3, 31)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_period(text: str) -> list[tuple[str, date, int]]:
    """Return [(label, period_end, position)] for every reporting period mention, in text order."""
    found: list[tuple[str, date, int]] = []
    for pattern, kind in _PERIOD_PATTERNS:
        for m in pattern.finditer(text):
            end: date | None = None
            if kind == "fy_range":
                y1 = int(m.group(1))
                y2s = m.group(2)
                y2 = int(y2s) if len(y2s) == 4 else (y1 // 100) * 100 + int(y2s)
                if y2 != y1 + 1 or not (2000 <= y1 <= 2100):
                    continue
                end = _fy_end(y1)
            elif kind == "fy_single":
                ys = m.group(1)
                y = int(ys) if len(ys) == 4 else 2000 + int(ys)
                if not (2000 <= y <= 2100):
                    continue
                end = date(y, 3, 31)
            elif kind == "dmy_text":
                end = _safe_date(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
            elif kind == "mdy_text":
                end = _safe_date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
            elif kind == "dmy_num":
                end = _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if end is None:
                continue
            found.append((fy_label_from_end(end), end, m.start(), m.end()))
    # Keep the longest match at each position and drop matches overlapping an accepted span.
    found.sort(key=lambda x: (x[2], -(x[3] - x[2])))
    accepted: list[tuple[str, date, int, int]] = []
    for item in found:
        if any(item[2] < a[3] and a[2] < item[3] for a in accepted):
            continue
        accepted.append(item)
    found = [(lbl, end, start) for lbl, end, start, _ in accepted]
    # Collapse overlapping mentions of the same period (e.g. "FY 2023-24 (31 March 2024)")
    deduped: list[tuple[str, date, int]] = []
    for item in found:
        if deduped and deduped[-1][0] == item[0] and item[2] - deduped[-1][2] < 40:
            continue
        deduped.append(item)
    return deduped


def header_periods(text: str) -> list[tuple[str, date]]:
    """Ordered unique periods mentioned in a column-header line."""
    seen: list[tuple[str, date]] = []
    for label, end, _ in parse_period(text):
        if all(label != s[0] for s in seen):
            seen.append((label, end))
    return seen


def parse_month_period(value) -> str | None:
    """Normalise GST/bank month cells ('Apr-2023', '2023-04', '04/2023', 'April 2023', datetime) → 'YYYY-MM'."""
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month"):
        return f"{value.year:04d}-{value.month:02d}"
    s = str(value).strip()
    m = re.fullmatch(r"(20\d{2})[-/](\d{1,2})(?:[-/]\d{1,2})?", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2})[-/](20\d{2})", s)
    if m:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.fullmatch(_MONTH_RE + r"[\s,'-]*(\d{2}|20\d{2})", s, re.I)
    if m:
        mon = m.group(1).lower()
        mon_n = MONTHS[mon[:3]]
        yr = int(m.group(2))
        yr = yr if yr > 100 else 2000 + yr
        return f"{yr:04d}-{mon_n:02d}"
    return None


def parse_date_value(value) -> date | None:
    """Parse common Indian bank-statement date formats."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", s)
    if m:
        y = int(m.group(3))
        y = y if y > 100 else 2000 + y
        return _safe_date(y, int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[\s-]" + _MONTH_RE + r"[\s,-]+(\d{2,4})", s, re.I)
    if m:
        mon = m.group(2).lower()
        y = int(m.group(3))
        y = y if y > 100 else 2000 + y
        return _safe_date(y, MONTHS[mon[:3]], int(m.group(1)))
    return None
