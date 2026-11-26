"""Interpret primary due-diligence observations into risk signals.

Observations are subjective. The interpretation explains the potential impact but the scorecard
gives unverified observations only partial weight until a reviewer verifies them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NEGATIVE = [
    (r"\bidle\b|shut ?down|\bclosed\b|not operational|non[- ]operational", "Plant idle/shut", "high"),
    (r"\bstrike\b|labou?r unrest|lock[- ]?out", "Labour disruption", "high"),
    (r"obsolete|outdated|old machinery|poor(ly)? maintain|rust(ed|ing)?|damaged|frequent breakdowns?", "Poor equipment condition", "medium"),
    (r"slow[- ]moving|non[- ]moving|obsolete (stock|inventory)|damaged stock|inventory (pile|build)", "Inventory quality concern", "medium"),
    (r"evasive|not cooperative|uncooperative|unable to explain|inconsistent (answers|explanations?)|reluctant", "Management transparency concern", "high"),
    (r"family dispute|succession (issue|concern)|key[- ]man risk|promoter (not involved|absent)", "Management continuity risk", "medium"),
    (r"overdue|default|delayed (payment|salar)|pending (statutory )?dues|statutory dues", "Payment delays / pending dues", "high"),
    (r"single (customer|buyer|supplier)|dependen(ce|t) on (one|a single|few)|concentrat", "Concentration risk", "medium"),
    (r"intense competition|price war|losing market share|cheap imports", "Competitive pressure", "medium"),
    (r"power cuts|water shortage|unsafe|no fire safety|safety violations?|pollution", "Infrastructure / compliance gap", "medium"),
    (r"litigation|dispute with|legal notice|court case", "Dispute or litigation noted", "medium"),
    (r"governance (concern|issue|lapse)|related[- ]party|diversion of funds|siphon", "Governance concern", "high"),
]
POSITIVE = [
    r"well[- ]maintained|modern|automated|state[- ]of[- ]the[- ]art|good housekeeping",
    r"fully operational|running (at|in) (full|three) shifts?|healthy order book|order book",
    r"transparent|experienced|professional(ly)? managed|cooperative|forthcoming|hands[- ]on",
    r"diversified (customer|client|supplier)|long[- ]standing (customers|relationships)",
    r"iso (9001|14001|certified)|export (orders|market)|capacity expansion",
]

UTIL_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s*)?(?:installed\s*)?(?:capacity|utili[sz]ation)|(?:capacity|utili[sz]ation)\D{0,25}(\d{1,3}(?:\.\d+)?)\s*%", re.I)
CONC_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*of\s*(?:total\s*)?(?:revenue|sales|turnover|purchases|supplies)\s*(?:comes?\s*)?(?:from|to)\s*(?:a\s*single|one|the\s*top|its\s*largest|top)", re.I)

SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class Interpretation:
    severity: str
    risk_impact: str
    interpretation: str
    numeric_value: float | None
    signals: list[dict] = field(default_factory=list)


def _max_sev(a: str, b: str) -> str:
    return a if SEV_ORDER[a] >= SEV_ORDER[b] else b


def interpret_observation(category: str, sub_category: str, title: str, notes: str, rating: int | None,
                          numeric_value: float | None, thresholds: dict | None = None) -> Interpretation:
    th = thresholds or {}
    util_min = float(th.get("capacity_utilisation_min", 60))
    text = f"{title}. {notes}"
    severity = "info"
    signals: list[dict] = []
    sentences: list[str] = []

    if sub_category in ("factory_utilization", "capacity_utilisation") and numeric_value is None:
        m = UTIL_RE.search(text)
        if m:
            numeric_value = float(m.group(1) or m.group(2))
    if sub_category in ("factory_utilization", "capacity_utilisation") and numeric_value is not None:
        v = numeric_value
        if v < 40:
            sev, impact = "high", "negative"
        elif v < util_min:
            sev, impact = "medium", "negative"
        elif v < 75:
            sev, impact = "low", "neutral"
        else:
            sev, impact = "info", "positive"
        severity = _max_sev(severity, sev)
        signals.append({"signal": "capacity_utilisation", "value": v, "severity": sev, "impact": impact})
        if impact == "negative":
            sentences.append(
                f"Capacity utilisation of {v:.0f}% is below the {util_min:.0f}% policy threshold — a potential operational risk. "
                "Under-utilised capacity can indicate weak demand, working-capital constraints or operational problems; fixed "
                "costs are spread over lower output, which pressures margins and repayment capacity.")
        elif impact == "positive":
            sentences.append(f"Capacity utilisation of {v:.0f}% indicates healthy demand and efficient use of installed assets.")
        else:
            sentences.append(f"Capacity utilisation of {v:.0f}% is moderate; monitor the trend against projections.")

    cm = CONC_RE.search(text)
    if cm:
        share = float(cm.group(1))
        sev = "high" if share >= 40 else "medium" if share >= 25 else "low"
        severity = _max_sev(severity, sev)
        signals.append({"signal": "concentration", "value": share, "severity": sev, "impact": "negative" if sev != "low" else "neutral"})
        sentences.append(f"About {share:.0f}% dependence on a single counterparty creates concentration risk — loss of that "
                         "relationship would materially affect cash flows.")

    neg_hits = []
    for pattern, label, sev in NEGATIVE:
        if re.search(pattern, text, re.I):
            if label == "Concentration risk" and cm:
                continue
            neg_hits.append(label)
            severity = _max_sev(severity, sev)
            signals.append({"signal": label, "severity": sev, "impact": "negative"})
    pos_hits = [p for p in POSITIVE if re.search(p, text, re.I)]

    if rating is not None:
        rsev = {1: "high", 2: "medium", 3: "low", 4: "info", 5: "info"}[max(1, min(5, int(rating)))]
        severity = _max_sev(severity, rsev)
        signals.append({"signal": "officer_rating", "value": rating, "severity": rsev,
                        "impact": "negative" if rating <= 2 else "positive" if rating >= 4 else "neutral"})

    negative = any(s["impact"] == "negative" for s in signals)
    positive = bool(pos_hits) or any(s["impact"] == "positive" for s in signals)
    impact = "negative" if negative else "positive" if positive else "neutral"
    if neg_hits:
        sentences.append("Observation flags: " + ", ".join(dict.fromkeys(neg_hits)) + ".")
    if pos_hits and not negative:
        sentences.append("The observation notes positive operating or management indicators.")
    if rating is not None:
        sentences.append(f"Officer rating: {rating}/5.")
    if not sentences:
        sentences.append("No specific risk indicators were detected in this observation.")
    sentences.append("This is a subjective field observation; it carries partial weight in scoring until a reviewer verifies it.")
    return Interpretation(severity=severity, risk_impact=impact, interpretation=" ".join(sentences),
                          numeric_value=numeric_value, signals=signals)
