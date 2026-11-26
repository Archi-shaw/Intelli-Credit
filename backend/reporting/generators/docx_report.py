"""CAM DOCX renderer (python-docx). Mirrors the PDF section order and content."""
from __future__ import annotations

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from reporting.generators.common import LOAN_TYPES, SECTIONS, evidence_references, inr, label, lakh, missing_items, pct, ratio

NAVY = RGBColor(0x0F, 0x2A, 0x4A)


def _table(doc, rows, header=True):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            cell = t.cell(i, j)
            cell.text = "" if v is None else str(v)
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(8)
                    run.bold = header and i == 0
    doc.add_paragraph()
    return t


def _h(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        r.font.color.rgb = NAVY


def _bullets(doc, items, empty="None recorded."):
    if not items:
        doc.add_paragraph(empty).runs[0].italic = True
        return
    for i in items:
        doc.add_paragraph(str(i), style="List Bullet")


def _period(doc, sec, title):
    _h(doc, title)
    rows = [r for r in sec["rows"] if r["available"]]
    if not sec["periods"] or not rows:
        doc.add_paragraph("No figures for this statement were extracted from the uploaded documents. Not available.")
        return
    head = ["Line item (₹ lakh)"] + sec["periods"] + ["Basis / source (latest)"]
    body = []
    for r in rows:
        last = r["cells"][-1]
        body.append([r["label"]] + [lakh(c["value"]) for c in r["cells"]] +
                    [f"{label(last['basis'])}: {last['source']}" if last["source"] else label(last["basis"])])
    _table(doc, [head] + body)
    missing = [r["label"] for r in sec["rows"] if not r["available"]]
    if missing:
        doc.add_paragraph("Not available in the documents: " + ", ".join(missing) + ".")


def render_docx(c: dict) -> bytes:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    b, lr, risk, rec = c["borrower"], c["loan_request"], c.get("risk"), c.get("recommendation")
    meta = c["meta"]

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run("Credit Appraisal Memo")
    run.bold, run.font.size, run.font.color.rgb = True, Pt(26), NAVY
    for line in (b["name"], f"Reference {meta['reference']}"):
        p = doc.add_paragraph(line)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if meta.get("synthetic_notice"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(meta["synthetic_notice"])
        r.bold, r.font.color.rgb = True, RGBColor(0xB4, 0x53, 0x09)
    status = "Pending authorised human review" if c["review"]["pending"] else f"Reviewed: {label(c['review']['decisions'][-1]['decision'])}"
    _table(doc, [["Facility requested", f"{LOAN_TYPES.get(lr['loan_type'], label(lr['loan_type']))} — {inr(lr['requested_amount'])}"],
                 ["Recommendation (model)", rec["outcome_label"] if rec else "Not yet generated"],
                 ["Risk score (model)", f"{risk['overall_score']:.1f}/100 — {risk['risk_category']}" if risk else "Not yet assessed"],
                 ["Assessment confidence", f"{risk['confidence_level']} ({risk['confidence_score']:.0f}/100)" if risk else "—"],
                 ["Review status", status],
                 ["Generated", f"{meta['generated_at'][:16].replace('T', ' ')} UTC by {meta['generated_by'] or 'system'}"]], header=False)
    doc.add_paragraph("Basis labels: " + "; ".join(f"{label(k)} = {v}" for k, v in meta["basis_legend"].items())).runs[0].font.size = Pt(8)
    doc.add_page_break()

    _h(doc, "1. " + SECTIONS[0])
    _table(doc, [["Legal name", b["name"]], ["CIN", b["cin"] or "Not provided"], ["PAN", b["pan"] or "Not provided"],
                 ["GSTIN", b["gstin"] or "Not provided"], ["Industry", b["industry"]], ["Constitution", b["business_type"]],
                 ["Incorporated", b["incorporation_date"] or "Not provided"], ["Registered address", b["registered_address"] or "Not provided"]],
           header=False)
    if b["directors"]:
        _table(doc, [["Director", "DIN", "Designation"]] + [[d["name"], d["din"] or "—", d["designation"] or "—"] for d in b["directors"]])
    _h(doc, "2. " + SECTIONS[1])
    doc.add_paragraph(c["business_profile"]["description"] or "No business description provided.")
    doc.add_paragraph(f"Declared annual turnover (declared, unverified): {inr(c['business_profile']['declared_annual_turnover'])}")
    _h(doc, "3. " + SECTIONS[2])
    _table(doc, [["Facility", LOAN_TYPES.get(lr["loan_type"], label(lr["loan_type"]))], ["Amount requested", inr(lr["requested_amount"])],
                 ["Tenure requested", f"{lr['tenure_months']} months"], ["Purpose", lr["purpose"]],
                 ["Application date", lr["application_date"]]], header=False)
    _h(doc, "4. " + SECTIONS[3])
    if c["documents"]:
        _table(doc, [["Ref", "Document", "Category", "Pages", "Extraction"]] +
               [[f"DOC-{d['id']}", d["name"], label(d["category"]), d["pages"] or "—", label(d["status"])] for d in c["documents"]])
    _h(doc, "5. " + SECTIONS[4])
    doc.add_paragraph(f"Periods available: {', '.join(c['financial_summary']['periods']) or 'none'}. Tables in ₹ lakh with source pages.")
    _period(doc, c["profit_and_loss"], "6. " + SECTIONS[5])
    _period(doc, c["balance_sheet"], "7. " + SECTIONS[6])
    _period(doc, c["cash_flow"], "8. " + SECTIONS[7])
    _h(doc, "9. " + SECTIONS[8])
    if c["ratios"]:
        _table(doc, [["Ratio", "Value", "Formula", "Interpretation"]] +
               [[r["name"], ratio(r["value"], r["unit"]), r["formula"], r["interpretation"]] for r in c["ratios"]])
    else:
        doc.add_paragraph("Ratios have not been computed.")
    _h(doc, "10. " + SECTIONS[9])
    recon = c["reconciliation"]
    if recon:
        _table(doc, [["Check", "Status", "Source A", "Source B", "Difference"]] +
               [[r["metric"], f"{label(r['status'])} ({r['severity']})", f"{r['source_a'] or '—'}: {inr(r['value_a'], '—')}",
                 f"{r['source_b'] or '—'}: {inr(r['value_b'], '—')}", inr(r["difference"], "—")] for r in recon])
    else:
        doc.add_paragraph("Reconciliation has not been run.")
    _h(doc, "11. " + SECTIONS[10])
    flagged = [r for r in recon if r["status"] in ("potential_discrepancy", "unusual_pattern", "requires_verification")]
    _bullets(doc, [f"{label(r['status'])} — {r['metric']}: {r['description']} (verify: {r['investigation'] or '—'})" for r in flagged],
             "No potential discrepancies identified.")
    doc.add_paragraph("These are potential discrepancies or patterns requiring verification — not findings of fraud.")

    _h(doc, "12. " + SECTIONS[11])
    if risk:
        _table(doc, [["Overall risk score (model)", f"{risk['overall_score']:.1f} / 100"], ["Risk category (model)", risk["risk_category"]],
                     ["Confidence indicator", f"{risk['confidence_level']} — {risk['confidence_score']:.0f}/100"],
                     ["Model", risk["model_label"]]], header=False)
        doc.add_paragraph(risk["summary"])
        _bullets(doc, [f"[{f['severity'].upper()}] {f['title']} — {f.get('detail', '')}" for f in risk["red_flags"]], "No red flags.")
        doc.add_paragraph("Missing information:")
        _bullets(doc, missing_items(c), "No missing items recorded.")
        _h(doc, "13. " + SECTIONS[12])
        _table(doc, [["Dimension", "Weight", "Score", "Data", "Explanation"]] +
               [[d["name"], f"{d['weight']:.0f}%", f"{d['score']:.0f}", label(d["status"]), d["explanation"]] for d in risk["dimensions"]])
        _h(doc, "14. " + SECTIONS[13])
        for v in (risk["five_cs"] or {}).values():
            _h(doc, v["name"], 2)
            doc.add_paragraph(v["assessment"])
            _bullets(doc, [f"Strength — {x}" for x in v["strengths"]] + [f"Risk — {x}" for x in v["risks"]], "No scored factors.")
    else:
        doc.add_paragraph("A credit-risk assessment has not been generated.")

    if rec:
        rc = rec["repayment_capacity"] or {}
        _h(doc, "15. " + SECTIONS[14])
        _table(doc, [["Cash available for debt service", f"{inr(rc.get('cash_available_for_debt_service'))} ({rc.get('basis') or '—'})"],
                     ["Existing annual debt service", inr(rc.get("existing_annual_debt_service"))],
                     ["Minimum DSCR (policy)", f"{rc.get('min_dscr')}x"], ["Proposed DSCR", ratio(rc.get("proposed_dscr"), "x")]], header=False)
        _h(doc, "16. " + SECTIONS[15])
        _bullets(doc, [f"{label(x.get('type'))}: {x.get('description')} [{label(x.get('status'))}]" for x in rec["collateral_requirements"]])
        _h(doc, "17. " + SECTIONS[16])
        _table(doc, [["Requested", inr(rec["requested_amount"])], ["Recommended (model)", inr(rec["recommended_amount"])],
                     ["Outcome (model)", rec["outcome_label"]]], header=False)
        doc.add_paragraph((rec["explanation"] or {}).get("why_amount", ""))
        _h(doc, "18. " + SECTIONS[17])
        doc.add_paragraph((rec["explanation"] or {}).get("why_rate", ""))
        _h(doc, "19. " + SECTIONS[18])
        doc.add_paragraph(f"Requested {rec['requested_tenure_months']} months; recommended {rec['recommended_tenure_months'] or 'n/a'} months. "
                          f"{rec['tenure_rationale'] or ''}")
        _h(doc, "20. " + SECTIONS[19])
        _bullets(doc, rec["conditions_precedent"] + rec["repayment_considerations"] + rec["critical_warnings"])
    _h(doc, "21. " + SECTIONS[20])
    _bullets(doc, [f"{o['title']} ({o['observed_on']}): {o['notes']}" for o in c["observations"]] +
             [f"Note [{n['section']}] by {n['author'] or 'analyst'}: {n['body']}" for n in c["analyst_notes"]],
             "No analyst observations recorded.")
    _h(doc, "22. " + SECTIONS[21])
    if c["review"]["decisions"]:
        _table(doc, [["Decision", "Reviewer", "Date", "Justification"]] +
               [[label(d["decision"]), d["reviewer"] or "—", d["at"][:10], d["comments"]] for d in c["review"]["decisions"]])
    doc.add_paragraph("Pending authorised human review. This memo does not constitute approval." if c["review"]["pending"]
                      else "Recorded human review decision(s) shown above.")
    _h(doc, "23. " + SECTIONS[22])
    _bullets(doc, evidence_references(c), "No sources recorded.")
    _h(doc, "24. " + SECTIONS[23])
    _bullets(doc, c["disclaimers"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
