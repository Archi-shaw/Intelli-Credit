"""Borrower CRUD, document validation/processing, analysis, CAM, review and audit — through the HTTP API."""
from __future__ import annotations

import io

import fitz
import pytest
from docx import Document

from tests.conftest import make_assessment, make_borrower, upload


# ------------------------------------------------------------------ borrowers

def test_borrower_crud_search_and_validation(org_a):
    api = org_a["analyst"]
    c = make_borrower(api, directors=[{"name": "Test Director", "din": "01234567"}])
    assert c["directors"][0]["din"] == "01234567"
    make_borrower(api, name="Other Traders LLP", pan="AAAFO1111A", gstin="07AAAFO1111A1Z3", industry="Trading")
    assert api.get("/api/v1/borrowers?q=fabrication").json()["total"] == 1
    assert api.get("/api/v1/borrowers?industry=Trading").json()["total"] == 1
    upd = api.patch(f"/api/v1/borrowers/{c['id']}", json={"headquarters": "Nashik"})
    assert upd.status_code == 200 and upd.json()["headquarters"] == "Nashik"
    dup = api.post("/api/v1/borrowers", json={"name": "Dup", "industry": "X1", "business_type": "LLP", "pan": "AAACT1234Q"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_pan"
    bad = api.post("/api/v1/borrowers", json={"name": "Bad", "industry": "X1", "business_type": "LLP", "pan": "123"})
    assert bad.status_code == 422 and "PAN" in bad.json()["error"]["message"]
    extra = api.post("/api/v1/borrowers", json={"name": "Bad", "industry": "X1", "business_type": "LLP", "evil": 1})
    assert extra.status_code == 422


def test_assessment_create_update_and_validation(org_a):
    api = org_a["analyst"]
    c = make_borrower(api)
    a = make_assessment(api, c["id"])
    assert a["reference"].startswith("IC-") and a["status"] == "documents_pending"
    assert api.patch(f"/api/v1/assessments/{a['id']}", json={"tenure_months": 48}).json()["tenure_months"] == 48
    bad = api.post("/api/v1/assessments", json={"company_id": c["id"], "loan_type": "crypto", "requested_amount": 1,
                                                "tenure_months": 12, "purpose": "invalid type"})
    assert bad.status_code == 422
    neg = api.post("/api/v1/assessments", json={"company_id": c["id"], "loan_type": "term_loan", "requested_amount": -5,
                                                "tenure_months": 12, "purpose": "negative amount"})
    assert neg.status_code == 422
    st = api.get(f"/api/v1/assessments/{a['id']}/status").json()
    assert len(st["workflow"]["steps"]) == 9


# ------------------------------------------------------------------ documents

def test_file_validation_rejects_bad_uploads(org_a):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    url = f"/api/v1/assessments/{a['id']}/documents"
    cases = [("evil.exe", b"MZ\x90\x00binary", "unsupported_type"),
             ("statement.pdf", b"PK\x03\x04 not really a pdf", "corrupt_file"),
             ("fake.pdf", b"just some text pretending", "type_mismatch"),
             ("empty.csv", b"", "empty_file"),
             ("../../etc/passwd.csv", b"\x00\x01\x02binary", "type_mismatch")]
    for name, data, code in cases:
        r = api.post(url, files={"file": (name, data)}, data={"category": "auto"})
        assert r.status_code == 422, (name, r.text)
        assert r.json()["error"]["code"] == code, (name, r.json())
    r = api.post(url, files={"file": ("x.csv", b"a,b\n1,2\n")}, data={"category": "not_a_category"})
    assert r.status_code == 422


def test_upload_size_limit(org_a, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    big = b"date,description,debit,credit\n" + b"01/04/2023,x,1,\n" * 90_000
    r = api.post(f"/api/v1/assessments/{a['id']}/documents", files={"file": ("big.csv", big)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_too_large"


def test_upload_processing_extraction_download_delete(org_a, synthetic_files):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    r = upload(api, a["id"], synthetic_files["healthy"]["financial_statements"])  # auto-classified
    assert r.status_code == 201, r.text
    doc = api.get(f"/api/v1/documents/{r.json()['document']['id']}").json()
    assert doc["processing_status"] == "completed" and doc["category"] == "financial_statements"
    assert doc["latest_job"]["status"] == "completed" and doc["page_count"] == 3
    ext = api.get(f"/api/v1/documents/{doc['id']}/extraction").json()
    rev = next(m for m in ext["metrics"] if m["metric_key"] == "revenue_from_operations" and m["period_label"] == "FY2023-24")
    assert rev["value"] == 246040000.0 and rev["page_number"] == 1 and rev["unit"] == "lakh" and rev["confidence"] >= 0.8
    missing = [m for m in ext["metrics"] if m["value"] == 0 and m["raw_text"] not in ("0.00", "0")]
    assert not missing, "missing values must never be converted to zero"
    dup = upload(api, a["id"], synthetic_files["healthy"]["financial_statements"])
    assert dup.status_code == 409
    f = api.get(f"/api/v1/documents/{doc['id']}/file")
    assert f.status_code == 200 and f.content.startswith(b"%PDF") and "attachment" in f.headers["content-disposition"]
    assert api.delete(f"/api/v1/documents/{doc['id']}").status_code == 200
    assert api.get(f"/api/v1/documents/{doc['id']}").status_code == 404
    assert org_a["reviewer"].delete(f"/api/v1/documents/{doc['id']}").status_code == 403


def test_metric_correction_feeds_statements(org_a, synthetic_files):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    upload(api, a["id"], synthetic_files["healthy"]["financial_statements"], "financial_statements")
    metrics = api.get(f"/api/v1/assessments/{a['id']}/metrics").json()["items"]
    pat = next(m for m in metrics if m["metric_key"] == "pat" and m["period_label"] == "FY2023-24")
    r = api.patch(f"/api/v1/metrics/{pat['id']}", json={"verification_status": "corrected", "corrected_value": 2e7,
                                                         "correction_note": "Per signed accounts"})
    assert r.status_code == 200 and r.json()["effective_value"] == 2e7
    periods = api.get(f"/api/v1/assessments/{a['id']}/financials").json()["periods"]
    latest = next(p for p in periods if p["period_label"] == "FY2023-24")
    assert latest["line_items"]["pat"]["value"] == 2e7 and latest["line_items"]["pat"]["verified"]


# ------------------------------------------------------------------ analysis workflow

def _process_case(api, files, key):
    case = files[key]["case"]
    comp = {k: v for k, v in case.company.items() if k not in ("directors", "promoters")}
    comp["incorporation_date"] = comp["incorporation_date"].isoformat()
    company = make_borrower(api, **comp)
    appl = {k: v for k, v in case.application.items()}
    a = make_assessment(api, company["id"], **appl)
    for cat in ("financial_statements", "bank_statements", "gst_returns"):
        r = upload(api, a["id"], files[key][cat], cat)
        assert r.status_code == 201, r.text
    return a


def test_reconciliation_flags_gst_vs_bank_discrepancy(org_a, synthetic_files):
    api = org_a["analyst"]
    a = _process_case(api, synthetic_files, "inconsistent")
    assert api.get(f"/api/v1/assessments/{a['id']}").json()["status"] == "under_analysis"
    items = api.post(f"/api/v1/assessments/{a['id']}/reconciliation").json()["items"]
    by = {i["check_type"]: i for i in items}
    g = by["gst_vs_bank"]
    assert g["status"] == "potential_discrepancy" and g["severity"] == "high"
    assert g["source_a_value"] and g["source_b_value"] and g["difference"] is not None
    assert {d["name"] for d in g["source_documents"]} >= {"inconsistent_gst_returns_FY2023-24.csv"}
    assert by["circular_transactions"]["status"] == "unusual_pattern"
    assert "fraud" not in " ".join(i["description"].lower() for i in items)
    # analyst review of a finding
    rr = api.patch(f"/api/v1/assessments/{a['id']}/reconciliation/{g['id']}",
                   json={"review_status": "under_review", "comment": "Requested bank statements for other accounts."})
    assert rr.status_code == 200 and rr.json()["review_status"] == "under_review"
    listed = api.get(f"/api/v1/assessments/{a['id']}/reconciliation").json()
    assert next(i for i in listed["items"] if i["id"] == g["id"])["comment_count"] == 1


def test_assessment_requires_processed_documents(org_a):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    r = api.post(f"/api/v1/assessments/{a['id']}/risk-assessment")
    assert r.status_code == 412


@pytest.mark.parametrize("key,expected", [("healthy", {"approve", "conditional_approval"}), ("stressed", {"reject"})])
def test_risk_and_recommendation(org_a, synthetic_files, key, expected):
    api = org_a["analyst"]
    a = _process_case(api, synthetic_files, key)
    api.post(f"/api/v1/assessments/{a['id']}/reconciliation")
    r = api.post(f"/api/v1/assessments/{a['id']}/risk-assessment")
    assert r.status_code == 201, r.text
    asmt, rec = r.json()["assessment"], r.json()["recommendation"]
    assert len(asmt["dimensions"]) == 9 and set(asmt["five_cs"]) == {"character", "capacity", "capital", "collateral", "conditions"}
    assert 0 <= asmt["overall_score"] <= 100 and asmt["confidence_level"] in ("High", "Medium", "Low")
    assert rec["outcome"] in expected, rec["explanation"]
    assert rec["recommended_amount"] <= rec["requested_amount"]
    assert rec["explanation"]["headline"] and rec["disclaimer"]
    if key == "stressed":
        assert any(f["title"] == "Negative net worth" for f in asmt["red_flags"])
        assert rec["recommended_amount"] == 0
    again = api.post(f"/api/v1/assessments/{a['id']}/risk-assessment").json()
    assert again["assessment"]["version"] == 2 and again["recommendation"]["recommended_amount"] == rec["recommended_amount"]


def test_due_diligence_research_and_observations(org_a):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    out = api.post(f"/api/v1/assessments/{a['id']}/research").json()
    assert out["provider"] == "none" and out["findings"] == 0 and out["statuses"] == ["not_configured"]
    res = api.get(f"/api/v1/assessments/{a['id']}/research").json()
    assert res["queries"] and all(q["status"] == "not_configured" for q in res["queries"])
    obs = {"category": "factory_inspection", "sub_category": "factory_utilization", "title": "Site visit",
           "notes": "Factory found operating at 40% capacity."}
    preview = api.post(f"/api/v1/assessments/{a['id']}/observations/interpret", json=obs).json()
    assert preview["severity"] == "medium" and preview["numeric_value"] == 40
    saved = api.post(f"/api/v1/assessments/{a['id']}/observations", json=obs)
    assert saved.status_code == 201 and saved.json()["risk_impact"] == "negative"
    rv = org_a["reviewer"].patch(f"/api/v1/observations/{saved.json()['id']}", json={"reviewer_status": "verified"})
    assert rv.json()["reviewer_status"] == "verified"


def test_assistant_cites_sources_and_admits_unknowns(org_a, synthetic_files):
    api = org_a["analyst"]
    a = _process_case(api, synthetic_files, "inconsistent")
    api.post(f"/api/v1/assessments/{a['id']}/reconciliation")
    r = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What was the revenue in FY 2023-24?"}).json()
    ans = r["answer"]
    assert ans["method"] == "extractive" and ans["grounded"]
    metric = next(c for c in ans["citations"] if c["type"] == "metric")
    assert metric["value"] == 98520000.0 and metric["page_number"] == 1 and metric["document_name"].endswith(".pdf")
    assert "9.85 crore" in ans["content"]
    g = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "What differences exist between GST and bank credits?",
                                                               "session_id": r["session_id"]}).json()
    assert any(c["type"] == "reconciliation" for c in g["answer"]["citations"])
    unk = api.post(f"/api/v1/assessments/{a['id']}/chat", json={"question": "Zxqv blorp wibble?"}).json()["answer"]
    assert "could not be verified" in unk["content"].lower() and not unk["grounded"]
    hist = api.get(f"/api/v1/chat/sessions/{r['session_id']}").json()
    assert len(hist["messages"]) == 4


# ------------------------------------------------------------------ CAM, review, audit (end-to-end via API)

def test_end_to_end_cam_review_and_audit(org_a, synthetic_files):
    analyst, reviewer = org_a["analyst"], org_a["reviewer"]
    a = _process_case(analyst, synthetic_files, "healthy")
    analyst.post(f"/api/v1/assessments/{a['id']}/reconciliation")
    analyst.post(f"/api/v1/assessments/{a['id']}/comments", json={"body": "Collateral valuation report requested.",
                                                                   "section": "recommendation"})
    rec = analyst.post(f"/api/v1/assessments/{a['id']}/risk-assessment").json()["recommendation"]

    gen = analyst.post(f"/api/v1/assessments/{a['id']}/cam")
    assert gen.status_code == 202
    rep = analyst.get(f"/api/v1/cam/{gen.json()['report']['id']}").json()
    assert rep["status"] == "completed" and rep["has_pdf"] and rep["has_docx"]
    content = rep["content"]
    assert content["recommendation"]["recommended_amount"] == rec["recommended_amount"]
    pdf = analyst.get(f"/api/v1/cam/{rep['id']}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    text = "".join(p.get_text() for p in fitz.open(stream=pdf.content, filetype="pdf"))
    # Figures in the PDF match the database records
    rev_lakh = f"{246040000 / 1e5:,.2f}"
    assert rev_lakh in text  # revenue (₹ lakh) from the extracted statement
    amt = rec["recommended_amount"]
    assert (f"₹{amt / 1e7:,.2f} Cr" if amt >= 1e7 else f"₹{amt / 1e5:,.2f} L") in text
    for heading in ("Borrower details", "GST and bank reconciliation findings", "Five Cs of Credit", "Repayment capacity",
                    "Indicative interest-rate calculation", "Limitations and disclaimers", "Pending authorised human review"):
        assert heading in text, heading
    assert "Sahyadri Precision Components" in text  # API-created borrower: not flagged synthetic, no banner
    docx = analyst.get(f"/api/v1/cam/{rep['id']}/docx")
    assert docx.status_code == 200
    doc_text = "\n".join(p.text for p in Document(io.BytesIO(docx.content)).paragraphs)
    assert "Credit Appraisal Memo" in doc_text and "Limitations and disclaimers" in doc_text

    # Human review: preparer cannot approve; reviewer can; approval capped at requested amount.
    assert analyst.post(f"/api/v1/assessments/{a['id']}/decision",
                        json={"decision": "approved", "comments": "Approving my own work"}).status_code == 403
    too_much = reviewer.post(f"/api/v1/assessments/{a['id']}/decision",
                             json={"decision": "approved", "comments": "Approved with higher limit", "final_amount": 9e9})
    assert too_much.status_code == 422
    d = reviewer.post(f"/api/v1/assessments/{a['id']}/decision",
                      json={"decision": "approved", "comments": "Approved subject to conditions precedent."})
    assert d.status_code == 201 and d.json()["final_amount"] == rec["recommended_amount"]
    final = reviewer.get(f"/api/v1/assessments/{a['id']}/final").json()
    assert final["final_status"] == "approved" and final["is_final"]
    assert reviewer.post(f"/api/v1/assessments/{a['id']}/decision",
                         json={"decision": "rejected", "comments": "Changing my mind later"}).status_code == 412
    assert analyst.post(f"/api/v1/assessments/{a['id']}/risk-assessment").status_code == 412

    actions = {e["action"] for e in analyst.get(f"/api/v1/assessments/{a['id']}/audit").json()["items"]}
    for expected in ("assessment.created", "document.uploaded", "document.processed", "reconciliation.run",
                     "assessment.generated", "cam.generated", "cam.downloaded", "review.approved", "comment.added"):
        assert expected in actions, expected
    history = analyst.get(f"/api/v1/assessments/{a['id']}/cam").json()["items"]
    assert len(history) == 1
    dash = analyst.get("/api/v1/dashboard").json()["totals"]
    assert dash["borrowers"] == 1 and dash["completed_assessments"] == 1


def test_cam_without_assessment_marks_missing(org_a):
    api = org_a["analyst"]
    a = make_assessment(api, make_borrower(api)["id"])
    rep = api.post(f"/api/v1/assessments/{a['id']}/cam").json()["report"]
    content = api.get(f"/api/v1/cam/{rep['id']}").json()["content"]
    assert content["risk"] is None and content["recommendation"] is None
    assert all(not r["available"] for r in content["profit_and_loss"]["rows"])
    text = "".join(p.get_text() for p in fitz.open(stream=api.get(f"/api/v1/cam/{rep['id']}/pdf").content, filetype="pdf"))
    assert "has not been generated" in text and "Not available" in text


def test_seeded_synthetic_cam_is_labelled(db_clean):
    """The seed pipeline marks demo borrowers synthetic and the CAM carries the banner on every page."""
    from app.core.database import system_session
    from app.models import CAMReport
    from app.seed.demo import seed
    from app.core import storage

    out = seed()
    assert len(out["assessments"]) == 3
    db = system_session()
    try:
        rep = db.query(CAMReport).one()
        text = "".join(p.get_text() for p in fitz.open(stream=storage.read_bytes(rep.pdf_path), filetype="pdf"))
        assert text.count("SYNTHETIC DEMONSTRATION DATA") >= 3
        assert rep.content["meta"]["is_synthetic"]
    finally:
        db.close()
