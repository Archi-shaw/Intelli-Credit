"""Authentication, RBAC, organisation isolation (API and database level) and secret handling."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from tests.conftest import PASSWORD, login, make_assessment, make_borrower, register, upload

PROTECTED = [("get", "/api/v1/auth/me"), ("get", "/api/v1/borrowers"), ("post", "/api/v1/borrowers"),
             ("get", "/api/v1/assessments"), ("get", "/api/v1/dashboard"), ("get", "/api/v1/audit-logs"),
             ("get", "/api/v1/documents/1/file"), ("get", "/api/v1/cam/1/pdf"), ("get", "/api/v1/users"),
             ("get", "/api/v1/system/integrations"), ("post", "/api/v1/assessments/1/chat")]


@pytest.mark.parametrize("method,url", PROTECTED)
def test_unauthenticated_requests_are_rejected(client, method, url):
    r = client.post(url, json={}) if method == "post" else client.get(url)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_register_login_me_logout(client):
    api = register(client, "owner@gamma.example", "Gamma Finance")
    me = api.get("/api/v1/auth/me").json()
    assert me["role"] == "admin" and me["organization"]["name"] == "Gamma Finance"
    assert "hashed_password" not in str(me)
    assert api.post("/api/v1/auth/logout").status_code == 200
    r = api.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_revoked"


def test_login_failures_and_validation(client):
    register(client, "x@delta.example", "Delta")
    bad = client.post("/api/v1/auth/login", json={"email": "x@delta.example", "password": "wrong-password1"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials"
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@delta.example", "password": "whatever123"})
    assert unknown.status_code == 401 and unknown.json()["error"]["message"] == bad.json()["error"]["message"]
    weak = client.post("/api/v1/auth/register", json={"email": "y@delta.example", "full_name": "Y", "password": "short",
                                                      "organization_name": "D2"})
    assert weak.status_code == 422
    dup = client.post("/api/v1/auth/register", json={"email": "x@delta.example", "full_name": "Xy", "password": PASSWORD,
                                                     "organization_name": "Delta 2"})
    assert dup.status_code == 409


def test_login_lockout_after_repeated_failures(client):
    register(client, "lock@eps.example", "Eps")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "lock@eps.example", "password": "wrong-password1"})
    r = client.post("/api/v1/auth/login", json={"email": "lock@eps.example", "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "login_locked"


def test_cookie_session_requires_csrf_header_for_mutations(client):
    r = client.post("/api/v1/auth/register", json={"email": "c@zeta.example", "full_name": "Cee", "password": PASSWORD,
                                                   "organization_name": "Zeta"})
    assert "ic_session" in r.cookies or client.cookies.get("ic_session")
    assert client.get("/api/v1/auth/me").status_code == 200  # cookie works for reads
    body = {"name": "Cookie Co", "industry": "Retail", "business_type": "Partnership"}
    no_header = client.post("/api/v1/borrowers", json=body)
    assert no_header.status_code == 403 and no_header.json()["error"]["code"] == "csrf_check_failed"
    ok = client.post("/api/v1/borrowers", json=body, headers={"x-ic-client": "web"})
    assert ok.status_code == 201
    client.cookies.clear()


def test_role_permissions(org_a):
    analyst, reviewer, admin = org_a["analyst"], org_a["reviewer"], org_a["admin"]
    assert reviewer.post("/api/v1/borrowers", json={"name": "No", "industry": "X1", "business_type": "LLP"}).status_code == 403
    assert analyst.post("/api/v1/users", json={"email": "z@alpha.example", "full_name": "Zed", "password": PASSWORD,
                                               "role": "admin"}).status_code == 403
    assert analyst.put("/api/v1/policy", json={"name": "x", "config": {}, "change_note": "nope"}).status_code == 403
    users = admin.get("/api/v1/users").json()["items"]
    rev = next(u for u in users if u["role"] == "reviewer")
    assert analyst.patch(f"/api/v1/users/{rev['id']}", json={"role": "admin"}).status_code == 403
    me_admin = next(u for u in users if u["role"] == "admin")
    assert admin.patch(f"/api/v1/users/{me_admin['id']}", json={"role": "analyst"}).status_code == 422


def test_role_change_takes_effect_and_deactivation_blocks_access(client, org_a):
    admin = org_a["admin"]
    users = admin.get("/api/v1/users").json()["items"]
    analyst = next(u for u in users if u["role"] == "analyst")
    assert admin.patch(f"/api/v1/users/{analyst['id']}", json={"is_active": False}).status_code == 200
    assert org_a["analyst"].get("/api/v1/borrowers").status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "analyst@alpha.example", "password": PASSWORD})
    assert r.status_code == 401


def test_organisation_isolation_via_api(org_a, org_b, synthetic_files):
    a = org_a["analyst"]
    b = org_b["admin"]
    company = make_borrower(a)
    app_ = make_assessment(a, company["id"])
    doc = upload(a, app_["id"], synthetic_files["healthy"]["gst_returns"], "gst_returns").json()["document"]
    # B cannot see or touch A's records — 404 (no existence disclosure), lists are empty.
    for url in (f"/api/v1/borrowers/{company['id']}", f"/api/v1/assessments/{app_['id']}",
                f"/api/v1/documents/{doc['id']}", f"/api/v1/documents/{doc['id']}/file",
                f"/api/v1/documents/{doc['id']}/extraction", f"/api/v1/assessments/{app_['id']}/reconciliation",
                f"/api/v1/assessments/{app_['id']}/risk-assessment", f"/api/v1/assessments/{app_['id']}/cam"):
        assert b.get(url).status_code == 404, url
    assert b.patch(f"/api/v1/borrowers/{company['id']}", json={"name": "Hijacked"}).status_code == 404
    assert b.delete(f"/api/v1/documents/{doc['id']}").status_code == 404
    assert b.post(f"/api/v1/assessments/{app_['id']}/chat", json={"question": "What was the revenue?"}).status_code == 404
    assert b.post("/api/v1/assessments", json={"company_id": company["id"], "loan_type": "term_loan",
                                               "requested_amount": 100000, "tenure_months": 12,
                                               "purpose": "cross-tenant attempt"}).status_code == 404
    assert b.get("/api/v1/borrowers").json()["total"] == 0
    assert b.get("/api/v1/assessments").json()["total"] == 0
    assert b.get("/api/v1/dashboard").json()["totals"]["borrowers"] == 0
    assert all(e["application_id"] != app_["id"] for e in b.get("/api/v1/audit-logs").json()["items"])


def test_row_level_security_enforced_in_database(org_a, org_b):
    """Even a raw SQL query through the application role only sees the current organisation's rows."""
    import os

    make_borrower(org_a["analyst"])
    make_borrower(org_b["admin"], pan="AAACB9999B", gstin="29AAACB9999B1Z1", name="Beta Borrower")
    org_a_id = org_a["admin"].data["organization"]["id"]
    eng = create_engine(os.environ["DATABASE_URL"], future=True)
    with eng.begin() as c:
        assert c.execute(text("SELECT count(*) FROM companies")).scalar() == 0  # no tenant context → nothing
        c.execute(text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_a_id)})
        names = c.execute(text("SELECT name FROM companies")).scalars().all()
        assert names == ["Test Fabrication Pvt Ltd"]
        with pytest.raises(Exception):  # WITH CHECK: cannot write rows into another tenant
            c.execute(text("INSERT INTO companies (organization_id, name, industry, business_type, is_synthetic, created_at, updated_at) "
                           "VALUES (:o, 'x', 'y', 'z', false, now(), now())"), {"o": org_a_id + 1})
    eng.dispose()


def test_audit_log_is_append_only(org_a):
    import os

    eng = create_engine(os.environ["DATABASE_URL"], future=True)
    with eng.connect() as c:
        c.execute(text("SELECT set_config('app.rls_bypass', 'on', false)"))
        assert c.execute(text("SELECT count(*) FROM audit_logs")).scalar() > 0
        with pytest.raises(Exception, match="append-only"):
            c.execute(text("UPDATE audit_logs SET summary = 'tampered'"))
        c.rollback()
        c.execute(text("SELECT set_config('app.rls_bypass', 'on', false)"))
        with pytest.raises(Exception, match="append-only"):
            c.execute(text("DELETE FROM audit_logs"))
    eng.dispose()


def test_integration_status_never_exposes_secrets(org_a, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-ant-secret-value-123")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    body = org_a["admin"].get("/api/v1/system/integrations").text
    assert "sk-ant-secret-value-123" not in body
    assert '"api_key_present":true' in body.replace(" ", "")
    me = org_a["admin"].get("/api/v1/auth/me").text
    assert settings.SECRET_KEY not in me


def test_health_and_readiness(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/v1/health").status_code == 200
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    assert r.json()["checks"]["database"] == "ok" and r.json()["checks"]["migrations"] == "0003"


def test_production_settings_guard():
    from app.core.config import Settings

    s = Settings(ENVIRONMENT="production", SECRET_KEY="change-me-in-production-please-use-a-long-random-string")
    assert s.production_problems()
    ok = Settings(ENVIRONMENT="production", SECRET_KEY="x" * 40, COOKIE_SECURE=True,
                  DATABASE_URL="postgresql://u:strong@db/x")
    assert ok.production_problems() == []


def test_client_ip_honours_only_trusted_proxies():
    from starlette.requests import Request

    from app.core.netutil import client_ip

    def req(peer, xff=None):
        headers = [(b"x-forwarded-for", xff.encode())] if xff else []
        return Request({"type": "http", "client": (peer, 1234), "headers": headers, "method": "GET", "path": "/"})

    assert client_ip(req("127.0.0.1", "203.0.113.9")) == "203.0.113.9"  # via the trusted Next.js proxy
    assert client_ip(req("127.0.0.1", "1.1.1.1, 203.0.113.9")) == "203.0.113.9"  # spoofed left-most hop ignored
    assert client_ip(req("198.51.100.7", "203.0.113.9")) == "198.51.100.7"  # untrusted peer: header ignored
    assert client_ip(req("127.0.0.1")) == "127.0.0.1"


def test_nul_characters_and_non_finite_numbers_are_rejected_not_500(org_a, caplog):
    """PostgreSQL cannot store NUL characters: bodies are rejected by validation, query parameters by the backstop;
    neither may surface as a 500, and the rejected value is never logged."""
    an = org_a["analyst"]
    marker = "Nul\x00Probe-7f3a"
    r = an.post("/api/v1/borrowers", json={"name": marker, "industry": "Textiles", "business_type": "Private Limited"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    co = make_borrower(an)
    r = an.post("/api/v1/assessments", json={"company_id": co["id"], "loan_type": "term_loan", "requested_amount": 1e6,
                                             "tenure_months": 12, "purpose": "Probe", "collateral": [
                                                 {"type": "property", "description": "a\x00b", "value": 1}]})
    assert r.status_code == 422, "nested models are covered too"
    body = ('{"company_id": %d, "loan_type": "term_loan", "requested_amount": 1000000, "tenure_months": 12, '
            '"purpose": "Probe", "collateral": [{"type": "property", "value": Infinity}]}' % co["id"])
    r = an.post("/api/v1/assessments", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422, "non-finite numbers are rejected"
    with caplog.at_level("WARNING"):
        for url, params in (("/api/v1/borrowers", {"q": marker}), ("/api/v1/borrowers", {"industry": marker}),
                            ("/api/v1/assessments", {"q": marker}), ("/api/v1/audit-logs", {"action": marker})):
            r = an.get(url, params=params)
            assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_value", (url, r.status_code)
            assert "7f3a" not in r.text
    assert "7f3a" not in caplog.text, "rejected values must not be logged"
    assert an.get("/api/v1/borrowers", params={"q": "' OR 1=1 --"}).json()["items"] == [], "search is parameterised"


def test_sql_errors_never_include_bound_values():
    from app.core.database import engine

    assert engine.hide_parameters, "SQLAlchemy must not format parameter values into errors or logs"
