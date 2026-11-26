"""Keyword-weighted document-type classifier (filename + first pages)."""
from __future__ import annotations

import re

from ai_engine.nlp.indian_terms import CLASSIFIER_KEYWORDS

_COMPILED = {cat: [(re.compile(p, re.I), w) for p, w in kws] for cat, kws in CLASSIFIER_KEYWORDS.items()}

_FILENAME_HINTS = {
    "gst_returns": r"gst|gstr",
    "income_tax_returns": r"\bitr\b|income[_ -]?tax",
    "bank_statements": r"bank|statement[_ -]?of[_ -]?account|\bsoa\b",
    "financial_statements": r"financial|balance[_ -]?sheet|\bp&?l\b|profit",
    "annual_reports": r"annual[_ -]?report",
    "board_minutes": r"minutes|board[_ -]?meeting",
    "credit_rating_reports": r"rating",
    "sanction_letters": r"sanction",
    "shareholding_disclosures": r"sharehold",
    "legal_notices": r"legal|notice|nclt|court",
}


def classify(filename: str, text: str) -> tuple[str, float, dict[str, float]]:
    sample = text[:15000]
    scores: dict[str, float] = {}
    for cat, patterns in _COMPILED.items():
        s = 0.0
        for pat, w in patterns:
            hits = len(pat.findall(sample))
            if hits:
                s += w * min(3, hits) ** 0.5
        if re.search(_FILENAME_HINTS.get(cat, r"$^"), filename, re.I):
            s += 3.0
        scores[cat] = round(s, 2)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_cat, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top < 3.0:
        return "other", 0.3, scores
    confidence = min(0.97, 0.5 + (top - second) / (top + 4.0))
    return top_cat, round(confidence, 2), scores
