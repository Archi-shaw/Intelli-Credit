"""End-to-end smoke test of a running Intelli-Credit deployment (through the web proxy, like a browser).

Usage:
  .venv/bin/python scripts/verify_stack.py --base http://localhost:3080
  DEMO_PASSWORD=... .venv/bin/python scripts/verify_stack.py --base https://credit.example.com

Requires the demo seed (analyst@meridian.example and admin@northwind.example). Creates one synthetic borrower and
assessment, uploads the synthetic "inconsistent" case documents, waits for background processing, runs
reconciliation and the credit assessment, generates the CAM, verifies the PDF figures against the API's records,
checks the audit trail and tenant isolation. Exits non-zero on the first failed check.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("IC_BASE_URL", "http://localhost:3080"))
    ap.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", "IntelliDemo2026!"))
    ap.add_argument("--case", default="inconsistent", choices=["healthy", "inconsistent", "stressed"])
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    api = args.base.rstrip("/") + "/api/v1"
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(("  ok    " if cond else "  FAIL  ") + label)
        if not cond:
            failures.append(label)

    def client(email: str) -> httpx.Client:
        c = httpx.Client(base_url=api, headers={"x-ic-client": "web"}, timeout=60, follow_redirects=False)
        r = c.post("/auth/login", json={"email": email, "password": args.password})
        r.raise_for_status()
        return c

    print(f"Verifying {args.base}")
    ready = httpx.get(args.base.rstrip("/") + "/api/v1/ready", timeout=10)
    check(ready.status_code == 200 and ready.json().get("status") == "ready", f"readiness {ready.json().get('checks')}")
    a = client("analyst@meridian.example")
    check("ic_session" in a.cookies, "session cookie issued through the proxy")
    co = a.post("/borrowers", json={"name": f"Verification Traders {int(time.time())} (Synthetic)", "industry": "Textiles",
                                    "business_type": "Private Limited"}).json()
    appl = a.post("/assessments", json={"company_id": co["id"], "loan_type": "working_capital", "requested_amount": 15000000,
                                        "tenure_months": 12, "purpose": "Deployment verification (synthetic)",
                                        "collateral": [{"type": "inventory", "value": 14000000}]}).json()
    case_dir = REPO / "data" / "synthetic" / args.case
    for f in sorted(case_dir.glob(f"{args.case}_*")):
        r = a.post(f"/assessments/{appl['id']}/documents", files={"file": (f.name, f.read_bytes())}, data={"category": "auto"})
        check(r.status_code == 201, f"upload {f.name}")
    bogus = a.post(f"/assessments/{appl['id']}/documents", files={"file": ("fake.pdf", b"not a pdf")}, data={"category": "auto"})
    check(bogus.status_code == 422, "invalid file rejected")
    t = time.time()
    while time.time() - t < args.timeout:
        docs = a.get(f"/assessments/{appl['id']}/documents").json()["items"]
        if docs and all(d["processing_status"] in ("completed", "failed") for d in docs):
            break
        time.sleep(1)
    check(all(d["processing_status"] == "completed" for d in docs), f"background processing ({time.time() - t:.1f}s): "
          + ", ".join(f"{d['category']}={d['processing_status']}" for d in docs))
    items = a.post(f"/assessments/{appl['id']}/reconciliation").json()["items"]
    gst = next((i for i in items if i["check_type"] == "gst_vs_bank"), None)
    check(gst is not None and gst["status"] != "insufficient_data", f"reconciliation gst_vs_bank: {gst and gst['status']} {gst and gst['difference_pct']}")
    risk = a.post(f"/assessments/{appl['id']}/risk-assessment").json()
    rec = risk["recommendation"]
    check(rec["recommended_amount"] <= rec["requested_amount"], f"recommendation {rec['outcome']} {rec['recommended_amount']:.0f} <= {rec['requested_amount']:.0f}")
    ch = risk["assessment"].get("challenger")
    if ch:
        print(f"        challenger: {ch.get('status')} p={ch.get('probability')} version={ch.get('model_version')}")
    cam = a.post(f"/assessments/{appl['id']}/cam").json()["report"]
    t = time.time()
    while time.time() - t < args.timeout:
        cam = a.get(f"/cam/{cam['id']}").json()
        if cam["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    check(cam["status"] == "completed", f"CAM generation ({time.time() - t:.1f}s) {cam.get('error') or ''}")
    pdf, docx = a.get(f"/cam/{cam['id']}/pdf"), a.get(f"/cam/{cam['id']}/docx")
    check(pdf.status_code == 200 and pdf.content[:4] == b"%PDF", f"CAM PDF download ({len(pdf.content)} bytes)")
    check(docx.status_code == 200 and docx.content[:2] == b"PK", f"CAM DOCX download ({len(docx.content)} bytes)")
    try:
        import fitz

        text = "".join(p.get_text() for p in fitz.open(stream=pdf.content, filetype="pdf"))
        amt = rec["recommended_amount"]
        expected = f"₹{amt / 1e7:,.2f} Cr" if amt >= 1e7 else f"₹{amt / 1e5:,.2f} L"
        check(expected in text, f"PDF shows the recommended amount from the database ({expected})")
    except ImportError:
        print("        (PyMuPDF not installed; PDF text check skipped)")
    actions = {e["action"] for e in a.get(f"/assessments/{appl['id']}/audit").json()["items"]}
    need = {"assessment.created", "document.uploaded", "document.processed", "reconciliation.run", "assessment.generated",
            "cam.generated", "cam.downloaded"}
    check(need <= actions, f"audit trail ({len(actions)} action types)")
    other = client("admin@northwind.example")
    check(other.get(f"/assessments/{appl['id']}").status_code == 404 and other.get(f"/cam/{cam['id']}/pdf").status_code == 404,
          "another organisation receives 404 for the assessment and its CAM")
    anon = httpx.get(f"{api}/cam/{cam['id']}/pdf", timeout=10)
    check(anon.status_code == 401, "unauthenticated CAM download rejected")
    print("PASS" if not failures else f"FAILED: {len(failures)} check(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
