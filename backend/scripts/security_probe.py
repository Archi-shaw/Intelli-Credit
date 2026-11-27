"""Live security probes against a running Intelli-Credit stack, through the web proxy like a browser.

Usage:
  .venv/bin/python scripts/security_probe.py --base http://localhost:3080 [--env-file ../.env]

Needs the demo seed (Meridian and Northwind organisations). Checks authentication, CSRF, RBAC, tenant isolation,
private document downloads, injection and NUL-byte handling on every string filter, secret exposure, error
envelopes, security headers and login rate limiting. Prints PASS/FAIL per check and never prints secret values.

The last checks send ~26 failed logins for a non-existent account: no real user is locked out, but logins from
this client IP are throttled for up to a minute afterwards.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import httpx

DEFAULT_PASSWORD = "IntelliDemo2026!"  # the documented development default (README → Demo accounts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("IC_BASE_URL", "http://localhost:3080"))
    ap.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", DEFAULT_PASSWORD))
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"),
                    help="server env file whose secret values must never appear in API responses (skipped if absent)")
    args = ap.parse_args()
    api = args.base.rstrip("/") + "/api/v1"
    fails: list[str] = []

    def check(ok: bool, label: str) -> None:
        print(("PASS  " if ok else "FAIL  ") + label)
        if not ok:
            fails.append(label)

    def login(email: str) -> tuple[httpx.Client, httpx.Response]:
        c = httpx.Client(base_url=api, headers={"x-ic-client": "web"}, timeout=30)
        for _ in range(10):  # honour Retry-After if an earlier run drained the login bucket
            r = c.post("/auth/login", json={"email": email, "password": args.password})
            if r.status_code != 429:
                break
            time.sleep(int(r.headers.get("retry-after", "3")))
        r.raise_for_status()
        return c, r

    secrets: list[str] = []
    env_path = Path(args.env_file)
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip("\"'")
                if len(v) >= 8 and k.strip() != "ENVIRONMENT" and not v.startswith("http"):
                    secrets.append(v)

    print(f"Probing {args.base}")
    analyst, r = login("analyst@meridian.example")
    cookie = r.headers.get("set-cookie", "").lower()
    check("httponly" in cookie and "samesite=lax" in cookie, "session cookie is HttpOnly + SameSite=Lax")

    anon = httpx.Client(base_url=api, timeout=15)
    for path in ["/borrowers", "/assessments", "/dashboard", "/audit-logs", "/system/integrations", "/users", "/policy"]:
        check(anon.get(path).status_code == 401, f"anonymous GET {path} -> 401")

    no_csrf = httpx.Client(base_url=api, timeout=15, cookies=analyst.cookies)
    r = no_csrf.post("/borrowers", json={"name": "CSRF probe (Synthetic)", "industry": "Textiles",
                                         "business_type": "Private Limited"})
    check(r.status_code == 403, f"cookie-authenticated mutation without the x-ic-client header -> {r.status_code}")

    reviewer, _ = login("reviewer@meridian.example")
    check(reviewer.post("/borrowers", json={"name": "RBAC probe (Synthetic)", "industry": "Textiles",
                                            "business_type": "Private Limited"}).status_code == 403,
          "reviewer cannot create borrowers -> 403")
    check(analyst.post("/users", json={"email": "x@meridian.example", "full_name": "X", "password": "Str0ngPassw0rd!",
                                       "role": "admin"}).status_code == 403, "analyst cannot create users -> 403")
    check(analyst.put("/policy", json={}).status_code in (403, 422), "analyst cannot change the scoring policy")
    check(analyst.post("/system/integrations/llm/test").status_code == 403, "analyst cannot run provider tests -> 403")

    other, _ = login("admin@northwind.example")
    mine = analyst.get("/borrowers").json()["items"]
    theirs = other.get("/borrowers").json()["items"]
    check(bool(mine) and not ({b["id"] for b in mine} & {b["id"] for b in theirs}),
          "borrower lists are disjoint between organisations")
    check(other.get(f"/borrowers/{mine[0]['id']}").status_code == 404, "other organisation gets 404 for a borrower")
    aid = analyst.get("/assessments").json()["items"][0]["id"]
    for p in ["", "/documents", "/audit", "/risk-assessment", "/cam"]:
        check(other.get(f"/assessments/{aid}{p}").status_code == 404, f"other organisation GET /assessments/{{id}}{p} -> 404")
    docs = analyst.get(f"/assessments/{aid}/documents").json()["items"]
    if docs:
        did = docs[0]["id"]
        check(other.get(f"/documents/{did}/file").status_code == 404, "other organisation cannot download a document")
        check(anon.get(f"/documents/{did}/file").status_code == 401, "anonymous document download -> 401")
        f = analyst.get(f"/documents/{did}/file")
        check(f.status_code == 200 and "sandbox" in f.headers.get("content-security-policy", "")
              and f.headers.get("x-content-type-options") == "nosniff", "document download: CSP sandbox + nosniff")

    theirs_names = {t["name"] for t in theirs}
    for q in ["' OR 1=1 --", '" OR ""="', "%' UNION SELECT email FROM users --", "1; DROP TABLE companies; --",
              "%", "_", "Northwind", "a" * 101, "x\x00y"]:
        rs = [analyst.get("/borrowers", params={"q": q}), analyst.get("/borrowers", params={"industry": q}),
              analyst.get("/assessments", params={"q": q, "status": q}), analyst.get("/audit-logs", params={"action": q[:80]})]
        codes = [x.status_code for x in rs]
        leak = any(x.status_code == 200 and any(i.get("name") in theirs_names or i.get("company_name") in theirs_names
                                                for i in x.json().get("items", [])) for x in rs)
        check(all(c in (200, 422) for c in codes) and not leak, f"filter probe {q[:24]!r}: {codes}, no 500, no other-tenant rows")
    r = analyst.post("/borrowers", json={"name": "Nul\x00Probe", "industry": "Textiles", "business_type": "Private Limited"})
    check(r.status_code == 422, f"NUL character in a JSON body -> {r.status_code}")
    check(analyst.get("/borrowers").status_code == 200, "tables intact after the probes")

    admin, _ = login("admin@meridian.example")
    blobs = [admin.get(p).text for p in ["/system/integrations", "/system/models", "/organization", "/auth/me", "/policy",
                                         "/dashboard"]]
    if secrets:
        check(not any(s in b for s in secrets for b in blobs),
              f"no server secret value in admin configuration responses ({len(secrets)} values checked)")
    else:
        print("SKIP  secret-value check (no env file)")
    check(not re.search(r"(sk-ant|tvly-|api[_-]?key\"?\s*[:=]\s*\"[^\"]{6,})", blobs[0], re.I),
          "integration status contains no key material")

    e1 = analyst.post("/borrowers", content=b"{not json", headers={"content-type": "application/json"})
    check(e1.status_code == 422 and "Traceback" not in e1.text and "sqlalchemy" not in e1.text.lower(),
          "malformed JSON -> 422 envelope, no stack trace")
    e2 = analyst.get("/assessments/999999999")
    check(e2.status_code == 404 and "Traceback" not in e2.text, "unknown id -> 404 envelope")

    h = httpx.get(args.base.rstrip("/") + "/login", timeout=15).headers
    check(h.get("x-frame-options", "").upper() in ("SAMEORIGIN", "DENY") and h.get("x-content-type-options") == "nosniff",
          "web pages send X-Frame-Options + nosniff")
    ah = analyst.get("/auth/me").headers
    check(ah.get("x-content-type-options") == "nosniff" and "x-request-id" in ah, "API responses send nosniff + request id")

    codes = [anon.post("/auth/login", json={"email": "probe@nowhere.example", "password": "wrong-password"},
                       headers={"x-ic-client": "web"}).status_code for _ in range(26)]
    check(429 in codes and set(codes) <= {401, 429}, f"login brute force is throttled: {codes.count(401)}x401, {codes.count(429)}x429")
    r = anon.post("/auth/login", json={"email": "probe@nowhere.example", "password": "x"}, headers={"x-ic-client": "web"})
    check(r.status_code == 429 and int(r.headers.get("retry-after", "0")) >= 1, "429 carries Retry-After")

    print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
