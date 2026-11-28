"""CAM PDF renderer (ReportLab). Uses DejaVu Sans (bundled in reporting/fonts) so ₹ renders correctly."""
from __future__ import annotations

import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from reporting.generators.common import LOAN_TYPES, SECTIONS, evidence_references, inr, label, lakh, missing_items, pct, ratio

# DejaVu Sans renders ₹. Bundled in reporting/fonts (license alongside) so CAM rendering does not need matplotlib
# at runtime; falls back to matplotlib's copy of the same files.
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def _font_dir() -> str:
    if os.path.isfile(os.path.join(_FONT_DIR, "DejaVuSans.ttf")):
        return _FONT_DIR
    import matplotlib

    return os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
_registered = False

NAVY = colors.HexColor("#0f2a4a")
MUTED = colors.HexColor("#5b6675")
LINE = colors.HexColor("#d5dbe3")
SHADE = colors.HexColor("#f1f4f8")
SEV = {"critical": "#9b1c1c", "high": "#b45309", "medium": "#a16207", "low": "#3f6212", "info": "#475569"}


def _fonts():
    global _registered
    if not _registered:
        d = _font_dir()
        pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(d, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(d, "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Oblique", os.path.join(d, "DejaVuSans-Oblique.ttf")))
        from reportlab.pdfbase.pdfmetrics import registerFontFamily

        registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu-Oblique", boldItalic="DejaVu-Bold")
        _registered = True


def _styles():
    ss = getSampleStyleSheet()
    base = dict(fontName="DejaVu", fontSize=9, leading=12.5, textColor=colors.HexColor("#1f2933"))
    return {
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 7.5, "leading": 10, "textColor": MUTED}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8, "leading": 10}),
        "cellb": ParagraphStyle("cellb", **{**base, "fontSize": 8, "leading": 10, "fontName": "DejaVu-Bold"}),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="DejaVu-Bold", fontSize=13, textColor=NAVY,
                             spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="DejaVu-Bold", fontSize=10.5, textColor=NAVY,
                             spaceBefore=6, spaceAfter=3),
        "title": ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=24, leading=30, textColor=NAVY, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("subtitle", fontName="DejaVu", fontSize=12, leading=16, textColor=MUTED, alignment=TA_CENTER),
        "banner": ParagraphStyle("banner", fontName="DejaVu-Bold", fontSize=9, leading=12, textColor=colors.HexColor("#7c2d12"),
                                 backColor=colors.HexColor("#ffedd5"), borderPadding=6, alignment=TA_CENTER),
    }


def P(text, style) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "—")).replace("\n", "<br/>"), style)


def _table(rows, widths, st, header=True, zebra=True):
    data = [[c if isinstance(c, Paragraph) else P(c, st["cellb"] if header and i == 0 else st["cell"]) for c in r]
            for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), SHADE))
    t.setStyle(TableStyle(style))
    return t


def _kv(pairs, st, w=(55 * mm, 115 * mm)):
    return _table([[k, v] for k, v in pairs], list(w), st, header=False)


def _bullets(items, st, empty="None recorded."):
    if not items:
        return [P(empty, st["small"])]
    return [P(f"• {i}", st["body"]) for i in items]


def _period_section(story, sec, st, title):
    story.append(Paragraph(title, st["h1"]))
    rows = [r for r in sec["rows"] if r["available"]]
    if not sec["periods"] or not rows:
        story.append(P("No figures for this statement were extracted from the uploaded documents. Not available.", st["small"]))
        return
    head = ["Line item (₹ lakh)"] + sec["periods"] + ["Basis / source (latest)"]
    body = []
    for r in rows:
        last = r["cells"][-1]
        src = f"{label(last['basis'])}: {last['source']}" if last["source"] else label(last["basis"])
        body.append([r["label"]] + [lakh(c["value"]) for c in r["cells"]] + [src])
    n = len(sec["periods"])
    widths = [52 * mm] + [22 * mm] * n + [(170 - 52 - 22 * n) * mm]
    story.append(_table([head] + body, widths, st))
    missing = [r["label"] for r in sec["rows"] if not r["available"]]
    if missing:
        story.append(P("Not available in the documents: " + ", ".join(missing) + ".", st["small"]))


def _on_page(canvas, doc, content):
    canvas.saveState()
    canvas.setFont("DejaVu", 7)
    canvas.setFillColor(MUTED)
    meta = content["meta"]
    canvas.drawString(18 * mm, 10 * mm, f"Intelli-Credit · CAM {meta['reference']} · Decision-support output, subject to authorised human review")
    canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
    if meta.get("is_synthetic"):
        canvas.setFillColor(colors.HexColor("#b45309"))
        canvas.drawCentredString(105 * mm, 290 * mm, "SYNTHETIC DEMONSTRATION DATA — NOT A REAL BORROWER")
    canvas.restoreState()


def render_pdf(c: dict) -> bytes:
    _fonts()
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"Credit Appraisal Memo {c['meta']['reference']}",
                            author="Intelli-Credit")
    s: list = []
    b, lr, risk, rec = c["borrower"], c["loan_request"], c.get("risk"), c.get("recommendation")

    # 1. Cover page
    s += [Spacer(1, 45 * mm), P("Credit Appraisal Memo", st["title"]), Spacer(1, 6 * mm),
          P(b["name"], st["subtitle"]), P(f"Reference {c['meta']['reference']}", st["subtitle"]), Spacer(1, 10 * mm)]
    if c["meta"].get("synthetic_notice"):
        s += [P(c["meta"]["synthetic_notice"], st["banner"]), Spacer(1, 8 * mm)]
    status = "Pending authorised human review" if c["review"]["pending"] else f"Reviewed: {label(c['review']['decisions'][-1]['decision'])}"
    s.append(_kv([("Facility requested", f"{LOAN_TYPES.get(lr['loan_type'], label(lr['loan_type']))} — {inr(lr['requested_amount'])}"),
                  ("Recommendation (model)", rec["outcome_label"] if rec else "Not yet generated"),
                  ("Risk score (model)", f"{risk['overall_score']:.1f}/100 — {risk['risk_category']}" if risk else "Not yet assessed"),
                  ("Assessment confidence", f"{risk['confidence_level']} ({risk['confidence_score']:.0f}/100)" if risk else "—"),
                  ("Review status", status),
                  ("Generated", f"{c['meta']['generated_at'][:16].replace('T', ' ')} UTC by {c['meta']['generated_by'] or 'system'}")], st))
    s += [Spacer(1, 8 * mm), P("Basis labels used in this memo: " + "; ".join(f"{label(k)} = {v}" for k, v in c["meta"]["basis_legend"].items()), st["small"]),
          PageBreak()]

    # 2. Borrower details
    s.append(Paragraph("1. " + SECTIONS[0], st["h1"]))
    s.append(_kv([("Legal name", b["name"]), ("CIN", b["cin"] or "Not provided"), ("PAN", b["pan"] or "Not provided"),
                  ("GSTIN", b["gstin"] or "Not provided"), ("Industry", b["industry"]), ("Constitution", b["business_type"]),
                  ("Incorporated", b["incorporation_date"] or "Not provided"), ("Registered address", b["registered_address"] or "Not provided")], st))
    if b["directors"]:
        s.append(Paragraph("Directors", st["h2"]))
        s.append(_table([["Name", "DIN", "Designation"]] + [[d["name"], d["din"] or "—", d["designation"] or "—"] for d in b["directors"]],
                        [70 * mm, 30 * mm, 70 * mm], st))
    if b["promoters"]:
        s.append(Paragraph("Promoters", st["h2"]))
        s.append(_table([["Name", "Holding", "Experience"]] + [[p["name"], pct(p["shareholding_pct"]),
                                                                f"{p['experience_years']} yrs" if p["experience_years"] else "—"] for p in b["promoters"]],
                        [80 * mm, 40 * mm, 50 * mm], st))

    # 3. Business profile
    s.append(Paragraph("2. " + SECTIONS[1], st["h1"]))
    bp = c["business_profile"]
    s.append(P(bp["description"] or "No business description provided.", st["body"]))
    s.append(P(f"Declared annual turnover (declared, unverified): {inr(bp['declared_annual_turnover'])}", st["small"]))

    # 4. Loan request
    s.append(Paragraph("3. " + SECTIONS[2], st["h1"]))
    s.append(_kv([("Facility", LOAN_TYPES.get(lr["loan_type"], label(lr["loan_type"]))), ("Amount requested", inr(lr["requested_amount"])),
                  ("Tenure requested", f"{lr['tenure_months']} months"), ("Moratorium", f"{lr['moratorium_months']} months"),
                  ("Repayment", label(lr["repayment_frequency"])), ("Purpose", lr["purpose"]), ("Application date", lr["application_date"])], st))
    if lr["existing_borrowings"]:
        s.append(Paragraph("Existing borrowings (declared)", st["h2"]))
        s.append(_table([["Lender", "Facility", "Outstanding", "Rate"]] + [[x.get("lender", "—"), label(x.get("facility_type")),
                                                                            inr(x.get("outstanding_amount")), pct(x.get("interest_rate"))]
                                                                           for x in lr["existing_borrowings"]],
                        [55 * mm, 40 * mm, 40 * mm, 35 * mm], st))

    # 5. Documents
    s.append(Paragraph("4. " + SECTIONS[3], st["h1"]))
    if c["documents"]:
        s.append(_table([["Ref", "Document", "Category", "Pages", "Extraction"]] +
                        [[f"DOC-{d['id']}", d["name"], label(d["category"]), d["pages"] or "—",
                          label(d["status"]) + (" (OCR)" if d["ocr_used"] else "")] for d in c["documents"]],
                        [16 * mm, 62 * mm, 38 * mm, 14 * mm, 40 * mm], st))
    else:
        s.append(P("No documents uploaded.", st["small"]))

    # 6. Financial summary
    s.append(Paragraph("5. " + SECTIONS[4], st["h1"]))
    fs = c["financial_summary"]
    s.append(P(f"Periods available: {', '.join(fs['periods']) or 'none'}. Figures in the following tables are in ₹ lakh and "
               "come from the cited source pages; derived totals are labelled as computed.", st["body"]))
    if fs["conflicts"]:
        s.append(P(f"{len(fs['conflicts'])} conflicting value(s) were found across sources; the highest-confidence value was used "
                   "and alternatives retained for review.", st["small"]))

    _period_section(s, c["profit_and_loss"], st, "6. " + SECTIONS[5])
    _period_section(s, c["balance_sheet"], st, "7. " + SECTIONS[6])
    _period_section(s, c["cash_flow"], st, "8. " + SECTIONS[7])

    # 10. Ratios
    s.append(Paragraph("9. " + SECTIONS[8], st["h1"]))
    if c["ratios"]:
        s.append(_table([["Ratio", "Value", "Formula", "Interpretation"]] +
                        [[r["name"], ratio(r["value"], r["unit"]), r["formula"], r["interpretation"]] for r in c["ratios"]],
                        [36 * mm, 20 * mm, 50 * mm, 64 * mm], st))
    else:
        s.append(P("Ratios have not been computed (no assessment yet).", st["small"]))

    # 11-12. Reconciliation
    s.append(Paragraph("10. " + SECTIONS[9], st["h1"]))
    recon = c["reconciliation"]
    if recon:
        s.append(_table([["Check", "Status", "Source A", "Source B", "Difference"]] +
                        [[r["metric"], f"{label(r['status'])} ({r['severity']})", f"{r['source_a'] or '—'}: {inr(r['value_a'], '—')}",
                          f"{r['source_b'] or '—'}: {inr(r['value_b'], '—')}",
                          inr(r["difference"], "—") + (f" ({r['difference_pct']:+.1f}%)" if r["difference_pct"] is not None else "")]
                         for r in recon], [44 * mm, 30 * mm, 36 * mm, 36 * mm, 24 * mm], st))
    else:
        s.append(P("Reconciliation has not been run.", st["small"]))
    s.append(Paragraph("11. " + SECTIONS[10], st["h1"]))
    flagged = [r for r in recon if r["status"] in ("potential_discrepancy", "unusual_pattern", "requires_verification")]
    if flagged:
        for r in flagged:
            s.append(KeepTogether([P(f"{label(r['status'])} — {r['metric']}" + (f" ({r['period']})" if r["period"] else ""), st["h2"]),
                                   P(r["description"], st["body"]),
                                   P(f"Suggested verification: {r['investigation'] or '—'} · Sources: {', '.join(r['documents']) or '—'} · "
                                     f"Analyst review: {label(r['review_status'])}", st["small"])]))
        s.append(P("These are potential discrepancies or patterns requiring verification — not findings of fraud.", st["small"]))
    else:
        s.append(P("No potential discrepancies were identified in the checks that could be performed.", st["small"]))

    # 13-15. Risk
    s.append(Paragraph("12. " + SECTIONS[11], st["h1"]))
    if risk:
        s.append(_kv([("Overall risk score (model)", f"{risk['overall_score']:.1f} / 100 (higher = lower risk)"),
                      ("Risk category (model)", risk["risk_category"]),
                      ("Confidence indicator", f"{risk['confidence_level']} — {risk['confidence_score']:.0f}/100 (data coverage and quality, not risk)"),
                      ("Data completeness", f"{risk['data_completeness']:.0f}%"), ("Model", risk["model_label"])], st))
        s.append(Spacer(1, 3 * mm))
        s.append(P(risk["summary"], st["body"]))
        if risk["red_flags"]:
            s.append(Paragraph("Red flags", st["h2"]))
            s += _bullets([f"[{f['severity'].upper()}] {f['title']} — {f.get('detail', '')}" for f in risk["red_flags"]], st)
        s.append(Paragraph("Missing information", st["h2"]))
        s += _bullets(missing_items(c), st, "No missing items recorded.")
        s.append(Paragraph("13. " + SECTIONS[12], st["h1"]))
        s.append(_table([["Dimension", "Weight", "Score", "Data", "Explanation"]] +
                        [[d["name"], f"{d['weight']:.0f}%", f"{d['score']:.0f}", label(d["status"]), d["explanation"]] for d in risk["dimensions"]],
                        [40 * mm, 14 * mm, 14 * mm, 20 * mm, 82 * mm], st))
        for d in risk["dimensions"]:
            rows = [[f["name"], f["display"], "missing" if f["score"] is None else f"{f['score']:.0f}", f["explanation"]] for f in d["factors"]]
            if rows:
                s.append(Paragraph(d["name"], st["h2"]))
                s.append(_table([["Factor", "Value", "Score", "Explanation"]] + rows, [42 * mm, 26 * mm, 14 * mm, 88 * mm], st))
        s.append(Paragraph("14. " + SECTIONS[13], st["h1"]))
        for k, v in (risk["five_cs"] or {}).items():
            s.append(P(f"{v['name']}: {v['assessment']}", st["h2"]))
            s += _bullets([f"Strength — {x}" for x in v["strengths"]] + [f"Risk — {x}" for x in v["risks"]], st, "No scored factors.")
    else:
        s.append(P("A credit-risk assessment has not been generated.", st["small"]))

    # 16-21. Recommendation
    if rec:
        rc = rec["repayment_capacity"] or {}
        s.append(Paragraph("15. " + SECTIONS[14], st["h1"]))
        s.append(_kv([("Cash available for debt service", f"{inr(rc.get('cash_available_for_debt_service'))} ({rc.get('basis') or 'basis unavailable'}, {rc.get('period') or '—'})"),
                      ("Existing annual debt service", inr(rc.get("existing_annual_debt_service")) + (" (includes policy assumptions)" if rc.get("existing_service_assumed") else "")),
                      ("Minimum DSCR (policy)", f"{rc.get('min_dscr')}x"), ("Proposed DSCR", ratio(rc.get("proposed_dscr"), "x")),
                      ("Indicative EMI", inr(rc.get("indicative_emi")))], st))
        s += _bullets(rc.get("existing_service_notes") or [], st, "")
        s.append(Paragraph("16. " + SECTIONS[15], st["h1"]))
        colls = [x for x in rec["collateral_requirements"]]
        s += _bullets([f"{label(x.get('type'))}: {x.get('description')} [{label(x.get('status'))}]" for x in colls], st,
                      "No collateral information provided.")
        s.append(Paragraph("17. " + SECTIONS[16], st["h1"]))
        s.append(_kv([("Requested", inr(rec["requested_amount"])), ("Recommended (model)", inr(rec["recommended_amount"])),
                      ("Outcome (model)", rec["outcome_label"])], st))
        s.append(_table([["Constraint", "Limit", "Binding", "Formula"]] +
                        [[x["name"], inr(x.get("value"), "n/a"), "Yes" if x.get("binding") else "", x.get("formula", "")]
                         for x in rec["amount_constraints"]], [45 * mm, 30 * mm, 15 * mm, 80 * mm], st))
        s.append(P((rec["explanation"] or {}).get("why_amount", ""), st["body"]))
        s.append(Paragraph("18. " + SECTIONS[17], st["h1"]))
        if rec["indicative_rate"] is not None:
            rows = [["Illustrative base rate (assumption)", pct(rec["base_rate"])], [f"Risk premium — {rec.get('outcome_label')}", pct(rec["risk_premium"])]]
            rows += [[a["name"], f"{a['value']:+.2f}%"] for a in rec["rate_adjustments"]]
            rows.append(["Indicative rate", pct(rec["indicative_rate"])])
            s.append(_table([["Component", "Rate"]] + rows, [120 * mm, 50 * mm], st))
        s.append(P((rec["explanation"] or {}).get("why_rate", ""), st["body"]))
        s.append(Paragraph("19. " + SECTIONS[18], st["h1"]))
        s.append(P(f"Requested {rec['requested_tenure_months']} months; recommended "
                   f"{rec['recommended_tenure_months'] or 'n/a'} months. {rec['tenure_rationale'] or ''}", st["body"]))
        s.append(Paragraph("20. " + SECTIONS[19], st["h1"]))
        s += _bullets(rec["conditions_precedent"] + rec["repayment_considerations"], st)
        if rec["critical_warnings"]:
            s.append(Paragraph("Critical warnings", st["h2"]))
            s += _bullets(rec["critical_warnings"], st)
        s.append(P((rec["explanation"] or {}).get("headline", ""), st["body"]))
    else:
        s.append(Paragraph("15–20. Recommendation", st["h1"]))
        s.append(P("A recommendation has not been generated.", st["small"]))

    # 22. Analyst observations
    s.append(Paragraph("21. " + SECTIONS[20], st["h1"]))
    s += _bullets([f"{o['title']} ({o['observed_on']}, {o['author'] or 'analyst'}; {label(o['severity'])}): {o['notes']}"
                   for o in c["observations"]] + [f"Note [{n['section']}] by {n['author'] or 'analyst'}: {n['body']}" for n in c["analyst_notes"]],
                  st, "No analyst observations recorded.")
    if c["research"]:
        s.append(Paragraph("External research", st["h2"]))
        s += _bullets([f"{r['title']} — {r['source']} ({label(r['verification'])}{', synthetic' if r['synthetic'] else ''}): {r['summary']}"
                       for r in c["research"]], st)

    # 23. Review
    s.append(Paragraph("22. " + SECTIONS[21], st["h1"]))
    if c["review"]["decisions"]:
        s.append(_table([["Decision", "Reviewer", "Date", "Final terms", "Justification"]] +
                        [[label(d["decision"]), d["reviewer"] or "—", d["at"][:10],
                          f"{inr(d['final_amount'], '—')} @ {pct(d['final_rate'])} / {d['final_tenure_months'] or '—'}m", d["comments"]]
                         for d in c["review"]["decisions"]], [22 * mm, 30 * mm, 20 * mm, 38 * mm, 60 * mm], st))
    s.append(P("Pending authorised human review. This memo does not constitute approval." if c["review"]["pending"]
               else "Recorded human review decision(s) shown above.", st["body"]))

    # 24-25
    s.append(Paragraph("23. " + SECTIONS[22], st["h1"]))
    s += _bullets(evidence_references(c), st, "No sources recorded.")
    s.append(Paragraph("24. " + SECTIONS[23], st["h1"]))
    s += _bullets(c["disclaimers"], st)
    if c["meta"].get("synthetic_notice"):
        s.append(P(c["meta"]["synthetic_notice"], st["banner"]))

    doc.build(s, onFirstPage=lambda cv, d: _on_page(cv, d, c), onLaterPages=lambda cv, d: _on_page(cv, d, c))
    return buf.getvalue()
