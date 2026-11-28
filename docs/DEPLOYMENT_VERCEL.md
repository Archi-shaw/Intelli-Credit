# Deploying Intelli-Credit on Vercel Hobby (portfolio demonstration)

This deployment runs the **whole application on Vercel's free Hobby plan** with a free managed PostgreSQL database
(Neon). There is no Celery worker, no Redis server and no persistent disk. Processing is the same code that the
Docker stack runs: document extraction, OCR, GST/bank reconciliation, ratios, risk scoring, the Five Cs, the ML
challenger and CAM PDF/DOCX rendering. It is triggered by authenticated requests instead of a background worker.

> [!IMPORTANT]
> **Hobby is for personal, non-commercial use.** Use synthetic demonstration data only, never real borrower
> documents. For anything beyond a demo, use the Docker Compose deployment
> ([DEPLOYMENT_COMPUTE_ENGINE.md](DEPLOYMENT_COMPUTE_ENGINE.md)).

## Architecture

```text
Browser ──HTTPS──► Vercel project "intellicredit-web" (Next.js, region bom1)
                     │  /api/*  rewrite (proxied, first-party cookie)
                     ▼
                   Vercel project "intellicredit-api" (FastAPI as one Python function, region bom1, max 300 s)
                     │  TLS, app role intellicredit_app (row-level security), no connection pool (DB_POOL=null)
                     ▼
                   Neon PostgreSQL 16 + pgvector (ap-south-1)
                     tables: application data · processing_jobs · stored_files · rate_limit_buckets · login_failures
```

| Concern | Docker Compose | Vercel Hobby |
|---|---|---|
| Background jobs | Celery worker + Redis (`JOB_BACKEND=celery`) | **`JOB_BACKEND=request`**: jobs stay `queued` in PostgreSQL. The web app calls `POST /assessments/{id}/jobs/run-next`, which claims one job atomically and runs one attempt **inside that request** |
| Documents and CAM files | Docker volume (`STORAGE_BACKEND=local`) | **`STORAGE_BACKEND=database`**: bytes in `stored_files` |
| Rate limits and login lockout | Redis token buckets | **`RATE_LIMIT_BACKEND=postgres`**: the same all-or-nothing token buckets, in one transaction, using database time |
| Database connections | Pooled | **`DB_POOL=null`**: one connection per session, no server-side prepared statements (works with Neon's pooler) |

### How request-driven processing works

1. An upload (or **Start processing**, re-processing, or **Generate CAM**) creates a `processing_jobs` row with
   status `queued`. Nothing runs in the background, so a Vercel function never has work it could lose when it is
   frozen.
2. While the Documents or CAM tab shows queued or processing work, each status poll (every 1.5 s) also calls
   `POST /api/v1/assessments/{id}/jobs/run-next`, with at most one call in flight per tab. That request:
   - checks the caller's organisation (other organisations get 404);
   - atomically claims the oldest runnable job (`UPDATE … WHERE status IN ('queued','retrying')`), so concurrent
     calls never run a job twice;
   - executes **one attempt** of the same handler the Celery worker uses, and records status, progress, result or
     error on the job row.
3. A failed attempt leaves the job `retrying`, and the next poll tries again, up to `JOB_MAX_ATTEMPTS` (3). After
   that it is marked `failed`, shown in the UI, and can be retried with **Re-process**.
4. If a function dies mid-job (timeout or crash), the job stays `running`. After `JOB_STALE_MINUTES` (6 on Vercel,
   longer than the 300 s function limit) it becomes claimable again.
5. If the tab is closed, jobs simply wait in `queued` and continue on the next visit. `POST /api/v1/jobs/{id}/run`
   runs one specific job.

With `JOB_BACKEND=celery`, `thread` or `sync`, these endpoints only report the pending count. Docker Compose and
local development behave exactly as before.

## Measured cost of every operation

The table below was measured on 2026-11-29. The API ran in a container limited like a Hobby function (1 vCPU, 2 GB
RAM), with `JOB_BACKEND=request`, `STORAGE_BACKEND=database`, `RATE_LIMIT_BACKEND=postgres` and `DB_POOL=null`,
against PostgreSQL 16 with a non-superuser owner (as on Neon). All data was synthetic. Each processing step is one
`run-next` request.

| Operation | Input | Time | Container peak memory |
|---|---|---:|---:|
| Cold start to `/ready` | — | 3.3 s | 79 MB |
| Upload (any file) | ≤ 0.4 MB | 0.1–0.2 s | — |
| Process text PDF (financial statements, 3 pages) | 46 KB | 1.6 s | 167 MB |
| Process GST CSV / bank CSV / XLSX / DOCX | 2–37 KB | 0.3–0.4 s each | 174 MB |
| Process image (OCR, 1 page) | 91 KB PNG | 8.9 s | 568 MB |
| Process scanned PDF (OCR, 3 pages) | 238 KB | 22 s | 881 MB |
| Process scanned PDF (OCR, 10 pages, 200 dpi) | 373 KB | 66 s | 893 MB |
| Reconciliation / ratios | — | 0.1 s | — |
| Risk assessment + Five Cs + recommendation + ML challenger | — | 2.3 s | — |
| Assistant question (extractive; no LLM key) | — | 0.1 s | — |
| CAM generation (PDF + DOCX) | — | 1.7 s | — |
| CAM PDF / DOCX download | 77 KB / 45 KB | 0.1 s | — |

### Operations that can exceed Hobby limits

| Limit (Vercel Hobby) | What exceeds it | Behaviour |
|---|---|---|
| **120 s proxied-request timeout** (web → API rewrite) | Scanned PDFs over roughly **15–18 pages** (OCR at about 6.5 s per page on 1 vCPU) | The browser's `run-next` call gets a 504, but the API function keeps working up to its own limit. The status poll shows the result once the job finishes |
| **300 s function duration** | Scanned PDFs over roughly **40 pages** | The attempt is cut off. After 6 minutes the job is reclaimed and retried, and after 3 attempts it is marked `failed` with an error. Split the document, or use the Docker deployment |
| **4.5 MB request body** | Uploads over 4 MB | `MAX_UPLOAD_MB=4`, and the web app refuses larger files before sending them. The 25 MB limit applies only to Docker deployments |
| **2 GB memory** | Not reached (peak 893 MB with OCR) | — |
| **Assistant / research with providers** | LLM calls of up to `LLM_TIMEOUT_SECONDS` (90 s) plus SDK retries can exceed the 120 s proxy limit | Keep `LLM_TIMEOUT_SECONDS` at 60 or lower on Vercel. Without keys these calls are local and take about 0.1 s |
| **500 MB Python bundle** | The runtime dependencies are about 600 MB uncompressed (OpenCV, ONNX Runtime, SciPy, PyMuPDF, NumPy) | Relies on Vercel *Large Functions* (up to 5 GB; on by default for new projects, `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` otherwise). Their availability on Hobby is not confirmed in Vercel's documentation, so check the first deployment |
| **Active CPU (4 h/month typical Hobby allotment)** | About 2,200 OCR pages per month | Plenty for a demo |

The OCR timings above depend on a fix made for this deployment. ONNX Runtime sized its thread pool from the host's
core count, so under a 1-CPU quota one page took **90 s instead of 12 s**. OCR threads now follow the cgroup CPU
quota (`OCR_THREADS` overrides). This also speeds up the CPU-limited Docker worker.

## Prerequisites

- A Vercel account (Hobby) and the Vercel CLI: `npm i -g vercel`, then `vercel login`. You sign in yourself.
- A PostgreSQL 16 database with pgvector. Free options: **Neon** created directly at neon.tech, or through Vercel
  Marketplace (Storage → Neon), in region `aws-ap-south-1` (Mumbai) to match the functions' `bom1` region.
- This repository with the backend virtualenv installed locally (README → Option B), used to migrate and seed the
  database.

## 1. Database

Create the Neon project and database. From its dashboard, copy two connection strings:

- the **owner, direct** connection (host without `-pooler`), for migrations;
- the **pooled** host, which the API uses with the application role.

Convert both to the `postgresql+psycopg://` scheme and keep `?sslmode=require`.

Generate an application-role password locally (never commit it):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Run migrations, create the RLS-restricted role and (optionally) seed the synthetic demo:

```bash
MIGRATION_DATABASE_URL='postgresql+psycopg://OWNER:PASSWORD@ep-xxx.ap-south-1.aws.neon.tech/neondb?sslmode=require' APP_DB_PASSWORD='<app role password>' DATABASE_URL='postgresql+psycopg://intellicredit_app:<app role password>@ep-xxx-pooler.ap-south-1.aws.neon.tech/neondb?sslmode=require' DEMO_PASSWORD='<choose one>' SEED=1 deploy/vercel/setup-database.sh
```

The seed processes the three synthetic cases on your machine and stores their documents in PostgreSQL. Rehearsed
against PostgreSQL 16 with a non-superuser owner, it takes about 14 s.

## 2. API project

```bash
cd backend && vercel link --project intellicredit-api
```

Set the environment variables (Production) in the Vercel dashboard or with `vercel env add NAME production`:

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` |
| `SECRET_KEY` | 48+ random characters (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `DATABASE_URL` | the **application role**, pooled host (from step 1) |
| `JOB_BACKEND` | `request` |
| `STORAGE_BACKEND` | `database` |
| `RATE_LIMIT_BACKEND` | `postgres` |
| `DB_POOL` | `null` |
| `JOB_STALE_MINUTES` | `6` |
| `MAX_UPLOAD_MB` | `4` |
| `OCR_THREADS` | `1` |
| `LLM_TIMEOUT_SECONDS` | `60` |
| `COOKIE_SECURE` | `true` |
| `ALLOW_REGISTRATION` | `false` |
| `CORS_ORIGINS` | `https://<web project domain>` |
| `CLIENT_IP_HEADER` | `x-real-ip` (Vercel sets it; check that audit-log IPs are real client addresses after deploying) |
| Optional | `LLM_PROVIDER=anthropic` + `LLM_API_KEY`, `EMBEDDING_PROVIDER=voyage` + `EMBEDDING_API_KEY`, `SEARCH_PROVIDER` + its key ([PROVIDERS.md](PROVIDERS.md)) |

The API refuses to start on Vercel with `STORAGE_BACKEND=local`, a background `JOB_BACKEND`, or Redis rate limits
without a Redis URL, as well as with any of the existing insecure production settings.

```bash
vercel deploy --prod
```

`backend/pyproject.toml` (with `uv.lock`) is the runtime dependency set for Vercel. It leaves out Celery, Redis,
pytest and the training-only scikit-learn and matplotlib, and uses headless OpenCV. `backend/vercel.json` sets
region `bom1`, `maxDuration` 300 s and the files excluded from the bundle. Docker keeps using `requirements.txt`.

## 3. Web project

```bash
cd frontend && vercel link --project intellicredit-web
```

| Variable | Value |
|---|---|
| `BACKEND_URL` | `https://<api project domain>` (read at build time; the browser never calls it directly) |
| `NEXT_PUBLIC_MAX_UPLOAD_MB` | `4` |

```bash
vercel deploy --prod
```

Then set `CORS_ORIGINS` on the API project to the web domain and redeploy the API.

## 4. Verify

```bash
cd backend && DEMO_PASSWORD='<seed password>' .venv/bin/python scripts/verify_stack.py --base https://<web domain>
```

```bash
cd backend && DEMO_PASSWORD='<seed password>' .venv/bin/python scripts/security_probe.py --base https://<web domain> --env-file /nonexistent
```

`verify_stack.py` drives request-mode jobs itself, the same way the web app does. Also check in the browser:
login and logout, an XLSX/DOCX/PNG upload, the reviewer decision, and that audit-log IP addresses are real client
addresses (see `CLIENT_IP_HEADER`).

## Operations

- **Logs:** Vercel dashboard → project → Logs (Hobby keeps runtime logs for 1 hour).
- **Backups:** Neon keeps point-in-time history (the free tier's window is short); for a portable copy:
  `pg_dump "<owner direct URL>" -Fc > intellicredit.dump`. Documents are inside the dump (`stored_files`).
- **Updates:** push to the branch connected to the projects, or run `vercel deploy --prod` in each directory.
  Run `deploy/vercel/setup-database.sh` (without `SEED=1`) first whenever a release adds a migration.
- **Rollback:** Vercel dashboard → Deployments → Promote a previous deployment. Migrations only move forward.
- **Costs:** Vercel Hobby and Neon's free tier cost nothing within their allotments. Optional AI provider calls are
  billed by those providers.
