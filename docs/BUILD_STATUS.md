# Intelli-Credit — build status

_Last updated: 2026-11-27 (production-readiness phase)._ The application runs end to end — FastAPI backend, Next.js
frontend, PostgreSQL (pgvector + row-level security), Celery/Redis background processing, CAM generation — and is
verified under Docker Compose. Everything below states what was actually executed; items that are **not** verified
are listed in §6.

## 1. Production-readiness phase — summary

| Area | Status |
|---|---|
| Docker Compose v2 | **Verified**: build, migrations, seed, full workflow, persistence across `down`/`up`, restart policies, port exposure |
| External providers (Claude, Voyage, Tavily, Brave, SerpAPI) | **Hardened and contract-tested**; live invalid-credential path verified for all five; **successful live calls not tested — no credentials** |
| ML challenger (LightGBM) | **Implemented, trained and evaluated on synthetic data**; display-only, never used for decisions |
| Distributed rate limiting (Redis) | **Implemented and verified** with two real API processes sharing Redis |
| Cloud deployment (Google Cloud) | **Prepared and dry-run only — not deployed, no cloud URL** (no credentials or billing authorisation) |
| Security and regression testing | **Passed** (§4, §5); five defects found and fixed (§3) |
| Data integrity | Pre-change backup taken and restore-verified; every pre-existing record still present and unchanged (§5.3) |

## 2. Work done in this phase

### 2.1 Audit and backup (before any change)

- Backup `backups/20261127-150552/` (git-ignored): `pg_dump` of the development database (companies 9, applications
  8, documents 24, audit rows 138), document storage, a tarball of the working tree (preserving uncommitted changes),
  SHA-256 checksums and a baseline test log (60 backend, 16 frontend unit, 7 browser end-to-end tests passing).
- New `scripts/backup.sh` / `scripts/restore.sh`. Restore drill: `TARGET=dev scripts/restore.sh backups/20261127-150552`
  restored into a scratch database, matched row counts, 31 RLS policies, 2 append-only triggers and revision 0002,
  then dropped the scratch copy → **RESTORE VERIFIED**. The live database was never replaced.

### 2.2 Docker Compose

- The host's Docker Engine 29.1.3 had no Compose or buildx plugin and `sudo` was unavailable. Installed the official
  **Compose v5.8.0** and **buildx v0.39.1** release binaries into `~/.docker/cli-plugins` (user-only; SHA-256 verified
  against the published checksums; engine untouched).
- Verified: `docker compose config` (development, seed profile, production overlay with placeholder secrets; the
  production overlay **fails without required secrets** as intended); `docker compose up -d --build` → db, redis,
  api, worker, frontend **healthy**, migrate exited 0, `/ready` reports database, migrations 0002, Redis and storage
  ok; `docker compose --profile seed run --rm seed` → three synthetic cases.
- `backend/scripts/verify_stack.py` (new) drives 16 checks through the web proxy — login, three uploads, invalid file
  rejected, background processing, reconciliation, assessment and recommendation (≤ requested), CAM generation,
  PDF/DOCX download, PDF amount = database amount, audit trail, cross-organisation 404, anonymous 401 → **PASS** on
  the final images (repeated after every rebuild in this phase).
- Persistence: `docker compose down` then `up` kept 4 companies, 12 documents, 2 CAMs and 49 audit rows; CAM PDF and
  original document were still downloadable.
- After a host reboot the API came up without its database: `db` and `redis` had no restart policy. Fixed
  (`restart: unless-stopped` on every long-running service).
- Exposure: development — web `127.0.0.1:3080`, API `127.0.0.1:8080`, PostgreSQL `127.0.0.1:5435`, Redis not
  published; production overlay — only the web app on `127.0.0.1:3080` (for a TLS reverse proxy).

### 2.3 External providers ([PROVIDERS.md](PROVIDERS.md))

- Claude: structured outputs (`output_config.format` JSON schema) for assistant answers, with cited references
  checked against the supplied sources; timeouts mapped (retryable); refusal and truncation handling.
- Voyage: batching (128), `input_type` document/query, retry with backoff honouring `Retry-After`, dimension checks.
- Search: per-provider keys, 20 s timeouts, one retry on timeouts/5xx, no retry on 429, strict parsing, Brave's
  HTTP 422 invalid-token response recognised (found by a live test).
- Status UI and API: *not configured / configured / verified / error / local*, last test, last success, last
  failure (redacted), administrator test button, results in the append-only audit log.
- Tests: 24 mocked-HTTP contract tests + 10 provider tests. **Live** (`IC_LIVE_TESTS=1`, re-run at the end of this
  phase): invalid credentials rejected and correctly reported by all five real APIs (5 passed); the six
  successful-call tests **skipped — credentials missing**. Nothing is labelled as live-integrated.

### 2.4 ML challenger ([ML_CHALLENGER.md](ML_CHALLENGER.md))

- Synthetic labelled dataset `data/ml/synthetic_credit_v1.csv` (7,630 snapshots, 4,000 borrowers, 591 defaults,
  SHA-256 `2426bfbb…2e85d`) with dataset card; deterministic generator and pipeline (`ml-generate-data`, `ml-train`).
- LightGBM 4.7.0 with monotone constraints, borrower-level 60/20/20 split, early stopping, calibration chosen by
  cross-validation, threshold chosen on validation, TreeSHAP, bootstrap intervals, baselines (logistic regression and
  the production scorecard).
- Synthetic test split: LightGBM ROC-AUC 0.830 / PR-AUC 0.320 / Brier 0.0569; logistic 0.824 / 0.352 / 0.0586;
  scorecard 0.814 / 0.334 / 0.0584 — **comparable, not better**; intervals overlap. Not evidence of real-world
  performance.
- Artifact `backend/models/challenger/challenger-lgbm-20261127-12af7e39/` (`approved_for_decisions: false`), shown
  in a separate "experimental — not used for decisions" card; a test proves it never changes recommendations.

### 2.5 Distributed rate limiting ([OPERATIONS.md](OPERATIONS.md#rate-limiting-distributed))

Redis token buckets (atomic Lua, all-or-nothing across buckets) per IP, login IP, user, organisation, AI and
processing endpoints; trusted-proxy client IPs; `429` with `Retry-After`; fail modes local/open/closed with a circuit
breaker; failed-login lockout shared through Redis (hashed keys). Tests (10): 80 concurrent checks on a 20-token
bucket admit exactly 20; two real uvicorn processes sharing Redis enforce one per-user limit (40 concurrent requests
→ exactly 10 admitted), per-IP, login and AI limits; outage behaviour per fail mode; shared lockout.

### 2.6 Cloud deployment (prepared, not deployed — [deploy/gcp/README.md](../deploy/gcp/README.md))

`deploy/gcp/deploy.sh` (dry-run by default; prints 42 commands), `cloudbuild-frontend.yaml`, `verify.sh`: Cloud Run
web (public) / API (internal ingress) / worker, Cloud SQL PostgreSQL 16 on a private IP (SSL only, HA, backups,
PITR, deletion protection), Memorystore Redis (private, AUTH), private versioned Cloud Storage bucket, Secret
Manager, Cloud Logging/Error Reporting-compatible JSON logs, uptime check. `bash -n` and the dry run pass; **no
command has been executed against a real project** (no `gcloud` credentials or billing authorisation).

## 3. Defects found and fixed in this phase

| # | Defect | Found by | Fix | Regression test |
|---|---|---|---|---|
| 1 | **Concurrent consolidation duplicated financial statements.** Two documents of one application finishing together (Celery, or the thread pool) each rebuilt the statements; both inserts survived, and the duplicated FY2023-24 statement was read as the *prior* year (revenue growth 0 % instead of 9.15 %). The same documents then scored 62.4 / ₹48 L instead of 66.2 / ₹70 L. Seen once in the Compose run and once in an earlier browser end-to-end run. Related races: concurrent assessment runs failed with a unique-version violation, concurrent reconciliation runs shared a run number, and a document was shown *completed* before its statements were committed. | Comparing repeated `verify_stack.py` results | Per-application PostgreSQL advisory transaction lock taken before reading, in every writer of derived data (consolidation, persisted ratios, reconciliation, assessment, document deletion); a document is marked completed in the same transaction as its consolidation; readers ignore duplicates left by the bug | `tests/test_concurrency.py` (5 tests; all failed before the fix) |
| 2 | NUL characters in request bodies or filters, and `Infinity` in numeric fields, returned **500** | Security probe | Request models reject NUL and non-finite numbers (422); a `DataError` backstop returns 422 without echoing the value | `test_nul_characters_and_non_finite_numbers_are_rejected_not_500` |
| 3 | SQL errors in logs included bound parameter values (customer data); integrity-error logs quoted row values such as e-mail addresses | Reading the log of defect 2 | `hide_parameters=True` on the engine; integrity errors log the constraint name only | `test_sql_errors_never_include_bound_values` |
| 4 | Brave's invalid-key response (HTTP 422) reported as a generic error | Live invalid-credential test | Recognised as an authentication error | Contract test with the recorded response |
| 5 | Compose `db`/`redis` did not restart after a host reboot | Reboot during verification | `restart: unless-stopped` | Compose re-verified |

Also: the rate limiter's Redis timeout is now configurable (`RATE_LIMIT_REDIS_TIMEOUT_MS`, default 300 ms; a test
was flaky under heavy CPU load), and a blank `CHALLENGER_MODEL_DIR` no longer resolves against the working directory.

**Records affected by defect 1 (left unchanged):** development database application 11 (*E2E Textile Traders
MWYIFZCU*, synthetic test data) and Compose application 4 (verification data) contain duplicated statements and an
assessment computed from them (62.4). Reads now use one statement per period; re-running the assessment creates a
corrected new version. A database unique constraint on (application, period) was **not** added, because it would
require deleting those duplicate rows from existing databases (a destructive migration, not done without approval).

## 4. Security verification

- Automated: `tests/test_auth_security.py` (26) — unauthenticated access, sessions, RBAC, segregation of duties,
  cross-tenant 404s, raw-SQL RLS probes as the app role, append-only audit, file validation, secret handling,
  production settings guard, trusted proxies, NUL/non-finite input, hidden SQL parameters; plus the rate-limit and
  workflow suites.
- Live against the Compose stack: `backend/scripts/security_probe.py` (new) — **42/42 passed**: HttpOnly + SameSite
  cookie, 7 anonymous endpoints 401, CSRF header enforced, RBAC (reviewer/analyst), disjoint tenants and 404s for
  borrowers, assessments, documents, audit, risk, CAM and file downloads, CSP sandbox + nosniff on downloads,
  injection/wildcard/over-length/NUL probes on every string filter (no 500, no cross-tenant rows), no server secret
  in admin configuration responses, error envelopes without stack traces, security headers, login brute force
  throttled with `Retry-After`.
- Client bundle (`.next/static`, 38 files): no `.env` secret value, no API-key patterns, no credentialed DB/Redis URL.
- Network exposure: see §2.2; nothing listens on a public interface.

## 5. Final regression (2026-11-27, after all fixes)

| Check | Command | Result |
|---|---|---|
| Backend | `cd backend && .venv/bin/python -m pytest` | **111 passed, 11 skipped** (live tests, opt-in) — AI engine 9, extraction regressions 2, workflow 15, auth/security 26, providers 10, provider contracts 24, rate limiting 10, challenger 8, concurrency 5, worker 2 |
| Live providers | `IC_LIVE_TESTS=1 … pytest -m live` | 5 passed (invalid credentials, real APIs), 6 skipped (credentials missing) |
| Frontend unit/component | `cd frontend && npm test` | **19 passed** (adds the challenger card) |
| Type check / production build | `npm run typecheck`, `npx next build` | passed / passed (10 routes) |
| Browser end-to-end (Playwright, production build) | `npm run test:e2e` | **7 passed** |
| Compose workflow | `scripts/verify_stack.py --base http://localhost:3080` | **PASS**; four consecutive runs after the concurrency fix gave identical results (66.2, ₹70 L) with no duplicate statements |
| Security probe | `scripts/security_probe.py` | **42/42** |
| Migrations | `alembic current` / `alembic check` | 0002 (head) / no drift |

### 5.3 Data integrity

The pre-change backup was restored into a scratch database and compared row by row (MD5 of each full row) with the
live development database: all baseline rows of `companies`, `credit_applications`, `uploaded_documents`,
`audit_logs`, `extracted_financial_metrics`, `risk_assessments`, `loan_recommendations`, `review_decisions`,
`cam_reports` and `organizations` are present and **unchanged**; in `users` only `last_login_at`/`updated_at` of the
three demo accounts used by the tests changed. Changes are additions from test runs only. The scratch database was
dropped. Missing financial values remain missing (never zero-filled) in statements, ratios and the challenger.

## 6. Known limitations / not verified

- **Successful live calls to Claude, Voyage, Tavily, Brave and SerpAPI are not tested** — no credentials. Add keys,
  then run `scripts/check_providers.py --live` and the live tests (see PROVIDERS.md).
- **No cloud deployment.** The GCP package is dry-run only; see deploy/gcp/README.md → "Not yet verified".
- **The challenger is trained on synthetic labels** and must not be used for lending decisions; real, governed
  outcome data and independent model validation are required first.
- No database-level unique constraint on statement periods (see §3); prevention relies on the advisory lock, which
  every writer uses.
- While Redis is unreachable, `RATE_LIMIT_FAIL_MODE=local` enforces limits per process (effective limit × number
  of instances).
- Background jobs are at-least-once (late acks, retries, recovery) and idempotent per document; exactly-once
  execution is not claimed.
- Starlette spools multipart uploads before the per-file limit applies when a client omits `Content-Length`; the
  request-size middleware covers normal browsers.
- Synthetic research fixtures exist only for demo borrowers; real borrowers need a configured search provider.
- The Anthropic SDK is pinned at 1.17.0; 2.x exists and needs a changelog review before upgrading.

---

## Earlier phase (initial completion) — kept for reference

### Audit at the start of that phase

| Area | State found | Action |
|---|---|---|
| Config, DB setup, security helpers, deps, errors, storage, audit helper | Present, working | Kept; extended (RLS context, logging, rate limits, trusted proxies, path normalisation, production guard) |
| ORM models | 35 tables, consistent | Kept; added `review_comments.entity_type/entity_id`, `reconciliations.review_status/reviewed_by_id` |
| `ai_engine/` | Present; 9 regression tests passing | Kept; fixed a catastrophic-backtracking (ReDoS) bug in the trailing-number regex (regression test added) |
| `app/main.py`, routes, services (except audit), worker | Missing | Implemented |
| Alembic migrations, seed, synthetic data | Missing | Implemented |
| CAM PDF/DOCX generators | Missing | Implemented |
| Frontend | Dependencies only | Implemented (Next.js 15 / React 19 / Tailwind) |
| Docker, `.env.example`, README | Missing | Implemented |

### Implemented in that phase

- **Backend**: FastAPI with lifespan checks, versioned API (`/api/v1`, 78 endpoints incl. `/system/models`), JSON
  error envelope, structured logging with request ids; auth (bcrypt, JWT cookie or bearer, CSRF header, revocation);
  RBAC and segregation of duties; PostgreSQL RLS on 31 tenant tables through a non-superuser role; append-only audit
  and review decisions; document pipeline with background jobs (atomic claim, retries, recovery); statement
  consolidation with conflict tracking; ratios; reconciliation; risk assessment and recommendation (versioned);
  research; observations; RAG assistant with citations and extractive fallback; CAM builder; Alembic `0001` + `0002`
  (reversible, no drift); CLI; three synthetic demo cases.
- **CAM**: PDF (ReportLab) and DOCX (python-docx), cover + 24 sections, basis labels, evidence references, synthetic
  banners, review status — figures from persisted records only.
- **Frontend**: authentication, dashboard, borrowers, assessment wizard and 9-step workspace (documents, financials,
  reconciliation, risk, recommendation and review, due diligence, assistant, CAM, audit), settings (organisation,
  users, roles, API configuration with live tests, audit log, scoring policy, challenger model card).

### Verification performed in that phase

Backend 60 passed; frontend 16 passed; Playwright 7 passed; production build; migrations up/down/up with no drift;
Celery + Redis path with orphaned-job recovery; both Docker images built and the stack run with equivalent
`docker run` commands (Compose was not available then — since verified, §2.2). Bugs fixed then: ReDoS in the
extractor; Celery pool processes losing the import path; CAM tab crash while generating (plus an error boundary);
client IPs collapsing to the proxy address; CAM preview blocked by `X-Frame-Options: DENY`; tab switches causing
server round-trips; blank/relative storage paths resolving against the working directory.
