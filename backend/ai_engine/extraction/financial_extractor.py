"""Deterministic extraction of financial figures and qualitative signals from parsed pages.

Every extracted value carries its source page, period, unit, method and a confidence score.
Nothing is ever defaulted: a figure that cannot be located is simply absent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ai_engine.extraction.units import (
    UNIT_MULTIPLIERS,
    UnitInfo,
    detect_unit,
    header_periods,
    normalise_unit_word,
    parse_number,
)
from ai_engine.nlp.indian_terms import (
    COMPILED_METRIC_PATTERNS,
    COMPILED_SIGNALS,
    METRIC_NAMES,
    PERCENT_METRICS,
    RATING_RE,
    match_metric,
    normalise_label,
)


@dataclass
class PageContent:
    page_number: int
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    method: str = "text"
    ocr_confidence: float | None = None
    label: str | None = None


@dataclass
class ExtractedMetric:
    metric_key: str
    label: str
    value: float | None
    raw_text: str
    page_number: int | None
    period_label: str | None
    period_end: date | None
    unit: str
    unit_multiplier: float
    currency: str
    confidence: float
    method: str
    excerpt: str
    text_value: str | None = None
    flags: list[str] = field(default_factory=list)


@dataclass
class ExtractedSignal:
    signal_key: str
    label: str
    area: str
    severity: str
    excerpt: str
    page_number: int | None
    amount: float | None = None


@dataclass
class ExtractionResult:
    metrics: list[ExtractedMetric]
    signals: list[ExtractedSignal]
    periods: list[str]
    unit: UnitInfo
    warnings: list[str]


_TRAILING_NUMS = re.compile(r"((?:\s+(?:\(\s*)?[-–−]?\s*(?:₹\s*)?\d[\d,]*(?:\.\d+)?\s*\)?)+)\s*$")
_INLINE_UNIT_VALUE = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lac|millions?|mn)\b", re.I
)
_PERCENT_VALUE = re.compile(r"([\d]+(?:\.\d+)?)\s*%")


def _is_note_ref(tok: str) -> bool:
    t = tok.strip()
    return bool(re.fullmatch(r"\d{1,2}(?:\.\d)?", t)) and float(t) <= 60


def _primary_pattern_bonus(key: str, label: str) -> float:
    norm = normalise_label(label)
    for k, _, pats in COMPILED_METRIC_PATTERNS:
        if k == key:
            return 0.10 if pats and pats[0].match(norm) else 0.05
    return 0.0


def _confidence(*, period_source: str, unit_explicit: bool, key: str, label: str, method: str,
                ocr_conf: float | None) -> float:
    conf = 0.55
    conf += {"page_header": 0.15, "doc_header": 0.08, "inline": 0.12, "none": -0.05}.get(period_source, 0.0)
    conf += 0.10 if unit_explicit else -0.05
    conf += _primary_pattern_bonus(key, label)
    if method == "table":
        conf += 0.05
    if ocr_conf is not None:
        conf *= 0.6 + 0.4 * max(0.0, min(1.0, ocr_conf))
    return round(max(0.05, min(0.97, conf)), 3)


def _scale(value: float, key: str, unit: UnitInfo) -> tuple[float, float, str]:
    if key in PERCENT_METRICS:
        return value, 1.0, "percent"
    return round(value * unit.multiplier, 2), unit.multiplier, unit.unit


class _PeriodTracker:
    """Keeps the most recent column-header periods seen while scanning a page top-down."""

    def __init__(self, doc_periods: list[tuple[str, date]]):
        self.doc_periods = doc_periods
        self.page_periods: list[tuple[str, date]] = []

    def observe(self, line: str) -> bool:
        periods = header_periods(line)
        # A header line names periods but carries few other numbers (avoid prose with dates and amounts).
        numeric_tokens = re.findall(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{2,3})+", line)
        if periods and len(numeric_tokens) <= 1:
            self.page_periods = periods
            return True
        return False

    def current(self) -> tuple[list[tuple[str, date]], str]:
        if self.page_periods:
            return self.page_periods, "page_header"
        if self.doc_periods:
            return self.doc_periods, "doc_header"
        return [], "none"


def _assign_periods(values: list[tuple[str, float]], periods: list[tuple[str, date]]):
    """Map trailing numeric tokens to header periods, dropping a leading note reference."""
    if periods:
        if len(values) == len(periods) + 1 and _is_note_ref(values[0][0]):
            values = values[1:]
        elif len(values) > len(periods):
            values = values[-len(periods):]
        return [(tok, val, periods[i]) for i, (tok, val) in enumerate(values[: len(periods)])]
    if values and _is_note_ref(values[0][0]) and len(values) > 1:
        values = values[1:]
    return [(values[0][0], values[0][1], None)] if values else []


def _extract_lines(page: PageContent, category: str, doc_unit: UnitInfo, tracker: _PeriodTracker) -> list[ExtractedMetric]:
    out: list[ExtractedMetric] = []
    page_unit = detect_unit(page.text)
    unit = page_unit if page_unit.explicit else doc_unit
    text = page.text
    if page.method == "ocr":
        # OCR often glues a label to the first figure ("tax1,280.30"); split letter→grouped-number joins.
        text = re.sub(r"([A-Za-z)])(\(?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?)", r"\1 \2", text)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 4:
            continue
        if tracker.observe(line):
            continue
        # Prose with an inline unit, e.g. "Total turnover of ₹ 45.20 crore"
        inline = _INLINE_UNIT_VALUE.search(line)
        m = _TRAILING_NUMS.search(line)
        label_part = line[: m.start()] if m else line
        if m and re.search(r"[a-zA-Z]{3,}", label_part):
            matched = match_metric(label_part, category)
            if matched:
                key, _ = matched
                tokens = re.findall(r"\(?\s*[-–−]?\s*(?:₹\s*)?\d[\d,]*(?:\.\d+)?\s*\)?", m.group(1))
                values = [(t.strip(), parse_number(t)) for t in tokens]
                values = [(t, v) for t, v in values if v is not None]
                periods, source = tracker.current()
                for tok, val, period in _assign_periods(values, periods):
                    scaled, mult, unit_name = _scale(val, key, unit)
                    flags = []
                    if not unit.explicit and key not in PERCENT_METRICS:
                        flags.append("unit_not_stated")
                    if period is None:
                        flags.append("period_not_identified")
                    if unit.currency != "INR":
                        flags.append(f"currency_{unit.currency.lower()}")
                    out.append(ExtractedMetric(
                        metric_key=key, label=label_part.strip()[:250], value=scaled, raw_text=tok,
                        page_number=page.page_number, period_label=period[0] if period else None,
                        period_end=period[1] if period else None, unit=unit_name, unit_multiplier=mult,
                        currency=unit.currency,
                        confidence=_confidence(period_source=source if period else "none", unit_explicit=unit.explicit,
                                               key=key, label=label_part, method=page.method, ocr_conf=page.ocr_confidence),
                        method="ocr_line" if page.method == "ocr" else "text_line", excerpt=line[:300], flags=flags,
                    ))
                continue
        if inline:
            label_text = line[: inline.start()]
            # Use the phrase right before the amount as the candidate label.
            candidate = re.split(r"[.;:,]\s", label_text)[-1] if label_text else ""
            candidate = re.sub(r"\b(of|is|was|at|stood at|amounting to|aggregating)\s*$", "", candidate.strip(), flags=re.I)
            matched = match_metric(candidate, category) if candidate else None
            if matched:
                key, _ = matched
                val = parse_number(inline.group(1))
                if val is not None:
                    unit_name = normalise_unit_word(inline.group(2))
                    mult = UNIT_MULTIPLIERS[unit_name]
                    out.append(ExtractedMetric(
                        metric_key=key, label=candidate[:250], value=round(val * mult, 2), raw_text=inline.group(0),
                        page_number=page.page_number, period_label=None, period_end=None, unit=unit_name,
                        unit_multiplier=mult, currency="INR",
                        confidence=_confidence(period_source="none", unit_explicit=True, key=key, label=candidate,
                                               method=page.method, ocr_conf=page.ocr_confidence),
                        method="text_inline", excerpt=line[:300], flags=["period_not_identified"],
                    ))
    return out


def extract_from_table(rows: list[list[str]], *, page_number: int | None, category: str, unit: UnitInfo,
                       doc_periods: list[tuple[str, date]], method: str = "table",
                       ocr_conf: float | None = None) -> list[ExtractedMetric]:
    """Rows of cell strings → metrics. Header rows with period names define the value columns."""
    out: list[ExtractedMetric] = []
    col_periods: dict[int, tuple[str, date]] = {}
    period_source = "doc_header" if doc_periods else "none"
    for row in rows:
        cells = ["" if c is None else str(c).strip() for c in row]
        if not any(cells):
            continue
        # Header row detection
        header_hits = {i: header_periods(c) for i, c in enumerate(cells) if c}
        header_hits = {i: p[0] for i, p in header_hits.items() if len(p) == 1}
        numeric_cells = [c for c in cells if parse_number(c) is not None]
        if header_hits and len(numeric_cells) <= len(header_hits):
            col_periods = header_hits
            period_source = "page_header"
            continue
        label_idx = next((i for i, c in enumerate(cells) if re.search(r"[a-zA-Z]{3,}", c)), None)
        if label_idx is None:
            continue
        matched = match_metric(cells[label_idx], category)
        if not matched:
            continue
        key, _ = matched
        pairs: list[tuple[str, float, tuple[str, date] | None]] = []
        if col_periods:
            for idx, period in sorted(col_periods.items()):
                if idx < len(cells) and idx != label_idx:
                    v = parse_number(cells[idx])
                    if v is not None:
                        pairs.append((cells[idx], v, period))
        else:
            values = [(c, parse_number(c)) for c in cells[label_idx + 1:]]
            values = [(c, v) for c, v in values if v is not None]
            pairs = _assign_periods(values, doc_periods)
        for tok, val, period in pairs:
            scaled, mult, unit_name = _scale(val, key, unit)
            flags = []
            if not unit.explicit and key not in PERCENT_METRICS:
                flags.append("unit_not_stated")
            if period is None:
                flags.append("period_not_identified")
            out.append(ExtractedMetric(
                metric_key=key, label=cells[label_idx][:250], value=scaled, raw_text=tok, page_number=page_number,
                period_label=period[0] if period else None, period_end=period[1] if period else None,
                unit=unit_name, unit_multiplier=mult, currency=unit.currency,
                confidence=_confidence(period_source=period_source if period else "none", unit_explicit=unit.explicit,
                                       key=key, label=cells[label_idx], method="table", ocr_conf=ocr_conf),
                method=method, excerpt=" | ".join(c for c in cells if c)[:300], flags=flags,
            ))
    return out


# --- Sanction letters, ratings and qualitative signals ---------------------------------------

_FACILITY_RE = re.compile(
    r"(term loan|cash credit|working capital(?: demand loan| term loan)?|overdraft|letter of credit|bank guarantee|wcdl|"
    r"export packing credit|equipment (?:loan|finance))\b[^.\n]{0,80}?(?:of|for|amounting to|limit of|aggregating(?: to)?|:)\s*"
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lac|millions?|mn)?",
    re.I,
)
_INTEREST_RE = re.compile(r"(?:rate of interest|\broi\b|interest rate|rate)\s*[:\-@]?\s*(?:at\s*|of\s*)?([\d]{1,2}(?:\.\d{1,2})?)\s*%", re.I)
_TENURE_RE = re.compile(r"(?:tenor|tenure|repayment period|door[- ]to[- ]door)\s*[:\-]?\s*(?:of\s*)?(\d{1,3})\s*months", re.I)
_OUTSTANDING_RE = re.compile(
    r"outstanding[^.\n]{0,50}?(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lac|millions?|mn)?", re.I
)
_LENDER_RE = re.compile(r"\b([A-Z][A-Za-z&.]*(?:\s+[A-Z][A-Za-z&.]*){0,5}\s+Bank(?:\s+(?:Ltd\.?|Limited))?)\b")
_CLAIM_RE = re.compile(
    r"(?:sum|amount|claim|dues|outstanding|demand)\s+of\s+(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|lac|millions?|mn)?",
    re.I,
)


def _amount(value: str, unit_word: str | None, fallback: UnitInfo) -> tuple[float | None, str, float]:
    v = parse_number(value)
    if v is None:
        return None, fallback.unit, fallback.multiplier
    if unit_word:
        u = normalise_unit_word(unit_word)
        return round(v * UNIT_MULTIPLIERS[u], 2), u, UNIT_MULTIPLIERS[u]
    return round(v * fallback.multiplier, 2), fallback.unit, fallback.multiplier


def extract_sanction_terms(pages: list[PageContent], unit: UnitInfo) -> list[ExtractedMetric]:
    out: list[ExtractedMetric] = []
    full = "\n".join(p.text for p in pages[:3])
    lender_m = _LENDER_RE.search(full)
    lender = lender_m.group(1).strip() if lender_m else None
    for page in pages:
        for m in _FACILITY_RE.finditer(page.text):
            val, unit_name, mult = _amount(m.group(2), m.group(3), unit)
            if val is None:
                continue
            facility = m.group(1).strip().title()
            label = f"Sanctioned {facility}" + (f" — {lender}" if lender else "")
            out.append(ExtractedMetric(
                metric_key="sanctioned_amount", label=label, value=val, raw_text=m.group(0)[:120],
                page_number=page.page_number, period_label=None, period_end=None, unit=unit_name, unit_multiplier=mult,
                currency="INR", confidence=0.8 if m.group(3) else 0.65, method="text_pattern", excerpt=m.group(0)[:300],
                text_value=lender, flags=[] if m.group(3) else ["unit_not_stated"],
            ))
        for m in _OUTSTANDING_RE.finditer(page.text):
            val, unit_name, mult = _amount(m.group(1), m.group(2), unit)
            if val is not None:
                out.append(ExtractedMetric(
                    metric_key="outstanding_amount", label="Outstanding" + (f" — {lender}" if lender else ""), value=val,
                    raw_text=m.group(0)[:120], page_number=page.page_number, period_label=None, period_end=None,
                    unit=unit_name, unit_multiplier=mult, currency="INR", confidence=0.72, method="text_pattern",
                    excerpt=m.group(0)[:300], text_value=lender,
                ))
        im = _INTEREST_RE.search(page.text)
        if im:
            out.append(ExtractedMetric(
                metric_key="interest_rate", label="Rate of interest" + (f" — {lender}" if lender else ""),
                value=float(im.group(1)), raw_text=im.group(0), page_number=page.page_number, period_label=None,
                period_end=None, unit="percent", unit_multiplier=1.0, currency="INR", confidence=0.8,
                method="text_pattern", excerpt=im.group(0), text_value=lender,
            ))
        tm = _TENURE_RE.search(page.text)
        if tm:
            out.append(ExtractedMetric(
                metric_key="tenure_months", label="Tenure" + (f" — {lender}" if lender else ""), value=float(tm.group(1)),
                raw_text=tm.group(0), page_number=page.page_number, period_label=None, period_end=None, unit="months",
                unit_multiplier=1.0, currency="INR", confidence=0.8, method="text_pattern", excerpt=tm.group(0),
                text_value=lender,
            ))
    return out


def extract_ratings(pages: list[PageContent]) -> list[ExtractedMetric]:
    out: list[ExtractedMetric] = []
    seen: set[str] = set()
    for page in pages:
        for m in RATING_RE.finditer(page.text):
            agency, grade, outlook = m.group(1), m.group(2).upper(), m.group(3)
            # Skip short-term scale like "A1+" (the negative lookahead already rejects letters; reject digits too)
            nxt = page.text[m.end(2): m.end(2) + 1]
            if nxt.isdigit():
                continue
            text_value = f"{agency.upper()} {grade}" + (f" ({outlook.title()})" if outlook else "")
            if text_value in seen:
                continue
            seen.add(text_value)
            out.append(ExtractedMetric(
                metric_key="credit_rating", label="Credit rating", value=None, raw_text=m.group(0)[:80],
                page_number=page.page_number, period_label=None, period_end=None, unit="grade", unit_multiplier=1.0,
                currency="INR", confidence=0.85, method="text_pattern", excerpt=page.text[max(0, m.start() - 80): m.end() + 80].replace("\n", " "),
                text_value=text_value,
            ))
    return out


def detect_signals(pages: list[PageContent], category: str, unit: UnitInfo) -> list[ExtractedSignal]:
    found: dict[str, ExtractedSignal] = {}
    for page in pages:
        text = page.text
        sentences = re.split(r"(?<=[.;])\s+|\n{2,}", text)
        for key, label, area, severity, patterns in COMPILED_SIGNALS:
            if key in found:
                continue
            for sent in sentences:
                if not any(p.search(sent) for p in patterns):
                    continue
                if area == "credit_rating" and "rating" not in sent.lower():
                    continue
                amount = None
                cm = _CLAIM_RE.search(sent)
                if cm:
                    amount, _, _ = _amount(cm.group(1), cm.group(2), unit)
                found[key] = ExtractedSignal(
                    signal_key=key, label=label, area=area, severity=severity,
                    excerpt=re.sub(r"\s+", " ", sent).strip()[:400], page_number=page.page_number, amount=amount,
                )
                break
    return list(found.values())


def _dedupe(metrics: list[ExtractedMetric]) -> list[ExtractedMetric]:
    """Collapse identical (metric, period, page) hits; flag conflicting values instead of guessing."""
    best: dict[tuple, ExtractedMetric] = {}
    for m in metrics:
        if m.metric_key in ("credit_rating", "sanctioned_amount", "outstanding_amount", "interest_rate", "tenure_months"):
            k = (m.metric_key, m.label, m.page_number, m.value, m.text_value)
        else:
            k = (m.metric_key, m.period_label, m.page_number)
        cur = best.get(k)
        if cur is None:
            best[k] = m
            continue
        if cur.value is not None and m.value is not None and abs(cur.value - m.value) > max(1.0, abs(cur.value) * 0.001):
            winner = cur if cur.confidence >= m.confidence else m
            loser = m if winner is cur else cur
            if "conflicting_values_on_page" not in winner.flags:
                winner.flags.append("conflicting_values_on_page")
            winner.flags.append(f"alternative_value:{loser.value}")
            winner.confidence = round(max(0.05, winner.confidence - 0.15), 3)
            best[k] = winner
        elif m.confidence > cur.confidence:
            best[k] = m
    return list(best.values())


def _cross_validate(metrics: list[ExtractedMetric]) -> None:
    """Accounting identities raise or lower confidence (Assets = Equity + Liabilities, etc.)."""
    by_period: dict[str | None, dict[str, ExtractedMetric]] = {}
    for m in metrics:
        by_period.setdefault(m.period_label, {}).setdefault(m.metric_key, m)

    def adjust(keys: list[str], ok: bool, flag: str):
        for k in keys:
            mm = period.get(k)
            if mm:
                mm.confidence = round(min(0.98, mm.confidence + 0.05), 3) if ok else round(max(0.05, mm.confidence - 0.15), 3)
                if not ok and flag not in mm.flags:
                    mm.flags.append(flag)

    for _, period in by_period.items():
        ta, tel = period.get("total_assets"), period.get("total_equity_and_liabilities")
        if ta and tel and ta.value and tel.value:
            adjust(["total_assets", "total_equity_and_liabilities"], abs(ta.value - tel.value) <= abs(ta.value) * 0.005,
                   "balance_sheet_does_not_tally")
        pbt, fc, dep, ebitda = (period.get(k) for k in ("pbt", "finance_costs", "depreciation", "ebitda"))
        if pbt and fc and dep and ebitda and ebitda.value:
            implied = pbt.value + fc.value + dep.value
            adjust(["ebitda"], abs(implied - ebitda.value) <= abs(ebitda.value) * 0.03, "ebitda_inconsistent_with_pbt_build_up")


def extract_document(pages: list[PageContent], category: str) -> ExtractionResult:
    warnings: list[str] = []
    full_head = "\n".join(p.text for p in pages[:3])
    doc_unit = detect_unit(full_head)
    doc_periods: list[tuple[str, date]] = []
    for p in pages[:4]:
        for line in p.text.splitlines():
            hp = header_periods(line)
            if len(hp) >= 1 and len(re.findall(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{2,3})+", line)) <= 1:
                doc_periods = hp
                break
        if doc_periods:
            break

    metrics: list[ExtractedMetric] = []
    for page in pages:
        tracker = _PeriodTracker(doc_periods)
        metrics.extend(_extract_lines(page, category, doc_unit, tracker))
        page_unit = detect_unit(page.text)
        unit = page_unit if page_unit.explicit else doc_unit
        for table in page.tables:
            metrics.extend(extract_from_table(table, page_number=page.page_number, category=category, unit=unit,
                                              doc_periods=tracker.page_periods or doc_periods,
                                              ocr_conf=page.ocr_confidence))
    if category in ("sanction_letters", "credit_rating_reports"):
        metrics.extend(extract_sanction_terms(pages, doc_unit))
    if category in ("credit_rating_reports", "annual_reports", "sanction_letters", "other"):
        metrics.extend(extract_ratings(pages))

    metrics = _dedupe(metrics)
    _cross_validate(metrics)
    signals = detect_signals(pages, category, doc_unit)

    if not doc_unit.explicit and any(m.metric_key not in PERCENT_METRICS for m in metrics if m.value is not None):
        warnings.append("No reporting unit (₹ lakh/crore) was stated; values are recorded as absolute rupees and need verification.")
    if doc_unit.currency != "INR":
        warnings.append(f"Document appears to report in {doc_unit.currency}; values were not converted to INR.")
    periods = sorted({m.period_label for m in metrics if m.period_label})
    return ExtractionResult(metrics=metrics, signals=signals, periods=periods, unit=doc_unit, warnings=warnings)


def metric_display_name(key: str) -> str:
    return METRIC_NAMES.get(key, key.replace("_", " ").title())
