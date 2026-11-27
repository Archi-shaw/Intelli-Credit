# Operations guide

## Rate limiting (distributed)

The limiter lives in `backend/app/core/ratelimit.py`. Every API request is checked against Redis token buckets
that all API instances share:

| Request | Buckets (defaults) |
|---|---|
| Unauthenticated | per client IP — `RATE_LIMIT_IP` (300/minute) |
| `POST /auth/login`, `/auth/register` | per client IP — `RATE_LIMIT_AUTH` (20/minute) |
| Authenticated | per user `RATE_LIMIT_USER` (600/minute) **and** per organisation `RATE_LIMIT_ORG` (3000/minute) |
| AI: assistant chat, research, provider tests | + per user `RATE_LIMIT_AI_USER` (20/minute), per org `RATE_LIMIT_AI_ORG` (100/minute) |
| Processing: uploads, re-processing, reconciliation, assessment, recommendation, CAM, start | + per user `RATE_LIMIT_PROCESSING_USER` (60/minute), per org `RATE_LIMIT_PROCESSING_ORG` (300/minute) |

- **Atomicity:** one Lua script refills and checks all buckets of a request and consumes only if every bucket has a
  token (all-or-nothing), using Redis server time. Verified: 40 concurrent requests spread over two API instances with
  a 10/minute limit admitted exactly 10; 80 concurrent checks against a 20-token bucket admitted exactly 20.
- **Identity:** user and organisation come from the signature-verified session token (no DB lookup); client IP from
  `X-Forwarded-For` only when the peer is in `TRUSTED_PROXIES`, using the right-most untrusted hop (not spoofable).
- **Responses:** `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` and a JSON error naming the
  scope; allowed responses carry the limit headers too.
- **Redis failure (`RATE_LIMIT_FAIL_MODE`):** `local` (default) keeps enforcing the same limits in each process, so
  the effective limit becomes N × instances during an outage; `open` admits everything; `closed` returns 503. A
  circuit breaker (`RATE_LIMIT_BREAKER_SECONDS`) avoids a connection timeout on every request while Redis is down; a
  warning is logged at most every 30 s. A Redis reply slower than `RATE_LIMIT_REDIS_TIMEOUT_MS` (default 300) counts
  as unavailable.
- **Failed-login lockout** (5 failures per e-mail + IP in 15 minutes) uses the same Redis, with SHA-256-hashed keys
  (no e-mail addresses in Redis), and falls back to per-process tracking on Redis errors.
- Tests: `backend/tests/test_rate_limit.py` (two real uvicorn instances sharing Redis; atomicity; fallback modes).

> [!WARNING]
> **Redis outage behaviour.** With the default `RATE_LIMIT_FAIL_MODE=local`, each API process enforces the limits on
> its own while Redis is unreachable, so the effective limit becomes the configured limit × the number of API
> instances. Use `closed` if over-admission is unacceptable (requests then fail with 503 during the outage), and add
> edge rate limiting (e.g. Cloud Armor) in front of multi-instance deployments.

## Cleaning up duplicated statements (legacy data)

Before the consolidation fix (see `docs/BUILD_STATUS.md` §3), two documents finishing at once could store an
application's statements twice. Reads now use one statement per period, so the duplicates are harmless, but they
should be removed before adding a unique constraint. Known affected records: development database application 11
and Compose application 4 (both synthetic test data). Procedure — nothing is deleted until step 4:

1. Back up: `TARGET=dev scripts/backup.sh` (or `scripts/backup.sh` for Compose) and verify it with `scripts/restore.sh`.
2. List affected applications:
   `select application_id, period_label, count(*) from financial_statements group by 1,2 having count(*) > 1;`
3. Confirm the copies are identical (same `line_items`, `source_document_ids`, `conflicts`) for each period.
4. Rebuild through the application instead of hand-deleting rows: re-process any document of the application
   (Documents tab → Re-process, or `POST /api/v1/documents/{id}/reprocess`) or call
   `POST /api/v1/assessments/{id}/financials/extract`. Consolidation takes the per-application lock, deletes the
   application's statements and writes exactly one per period.
5. Re-run step 2 (expect no rows), then re-run the risk assessment if a corrected version is wanted — the original
   assessment stays in the history as recorded.

## Backup and restore (Docker Compose / local)

```bash
scripts/backup.sh
```

```bash
scripts/restore.sh backups/<timestamp>
```

```bash
CONFIRM_REPLACE_LIVE=yes scripts/restore.sh backups/<timestamp> --replace-live
```

`backup.sh` writes a `pg_dump` (custom format), the private document storage (`storage.tar.gz`), row counts and
SHA-256 checksums. `restore.sh` without flags is non-destructive: it verifies checksums, restores into a scratch
database, compares row counts, RLS policy count (31), append-only triggers (2) and migration revision, then drops the
scratch database. Use `TARGET=dev` for the local development containers. Restore drills are verified (see
`docs/BUILD_STATUS.md`). Cloud backups (Cloud SQL PITR, bucket versioning) are in `deploy/gcp/README.md`.

## Logs

- `LOG_FORMAT=json` (default; Cloud Logging compatible: `severity`, `stack_trace` and the Error Reporting type on
  exceptions) or `text` for local development. Each request line carries method, path, status, duration, client IP and
  a request id (also returned as `x-request-id`). Bodies, document contents, tokens and secrets are never logged.
- `docker compose logs -f api worker`

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/ready` returns 503 with `migrations: not_applied` | Migrations not run | `docker compose up migrate` or `cd backend && .venv/bin/alembic upgrade head` |
| Every query returns no rows / `new row violates row-level security policy` | App connects without a tenant context, or a script uses the app role without `rls_bypass` | Use the API (sets the context per request); management scripts must use `system_session()` |
| Login works with the owner account but tenant data is invisible to `psql` as `intellicredit_app` | Expected: RLS hides rows without `app.current_org_id` | Query as the owner for maintenance |
| Documents stay *Queued* | No worker running (`JOB_BACKEND=celery`) or Redis down | `docker compose ps worker`; `python -m app.cli recover-jobs` re-queues orphaned jobs |
| Worker log `No module named ...` | Started outside `backend/` with an old image | Rebuild the image; the worker module pins the backend path itself |
| 429 responses while testing | Rate limits (shared across instances) | Raise the `RATE_LIMIT_*` values in `.env`, or set `RATE_LIMIT_ENABLED=false` for load tests |
| `rate-limit store unavailable` warnings | Redis unreachable | Check `REDIS_URL`; requests continue under `RATE_LIMIT_FAIL_MODE` |
| Assistant says answers are extractive | No LLM configured | Set `LLM_PROVIDER=anthropic` and a key; test it in Settings → API configuration |
| Research shows *provider not configured* | No search key | Set `SEARCH_PROVIDER` and that provider's key |
| Challenger card shows *unavailable* | No artifact, or trained with a different feature set | `cd backend && .venv/bin/python -m app.cli ml-train` |
| API refuses to start in production | Insecure settings (default `SECRET_KEY`, `COOKIE_SECURE=false`, dev DB password) | Fix the variables named in the log line |
| After a host reboot the API is unhealthy | Database container not running | All Compose services restart automatically (`restart: unless-stopped`); for the dev containers run `docker start intellicredit-dev-db intellicredit-dev-redis` |
| Log line `duplicate consolidated statements ignored` | Application processed before the consolidation race fix | Harmless (one statement per period is used); re-process any of its documents to rewrite its statements, then re-run the assessment |
| Browser E2E tests time out on first run | The dev server compiles routes on demand | `npm run test:e2e` uses a production build on :3100 by design |
