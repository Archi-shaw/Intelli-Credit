# Deploying Intelli-Credit to Google Cloud

> **Status: prepared, not deployed.** No cloud credentials or billing authorisation were available when this was
> written, so nothing has been created and no cloud URL exists. `deploy.sh` has been syntax-checked and run in its
> default dry-run mode (it prints all 42 commands); the commands themselves have not been executed against a real
> project. Run it once in dry-run mode, review, then deploy. The resources below are **billed**.

## Architecture

```
                 Internet (HTTPS, Google-managed TLS)
                              │
                     ┌────────▼────────┐  Cloud Run  ic-web (public)
                     │ Next.js web app │  proxies /api/* server-side
                     └────────┬────────┘
             Direct VPC egress│(all traffic)        VPC ic-vpc (10.10.0.0/24, Private Google Access)
        ┌─────────────────────▼──────────────────────────────────────────────┐
        │  Cloud Run ic-api (ingress: internal)     Cloud Run ic-worker      │
        │  FastAPI, RLS app role, rate limiting     Celery + health endpoint │
        │        │            │        │              │        │      │      │
        │        │            │        └──────┬───────┘        │      │      │
        │  Cloud SQL PG 16   Memorystore     Cloud Storage bucket (FUSE mount │
        │  + pgvector        Redis 7         /app/storage), private, versioned│
        │  private IP only   private IP only                                  │
        └────────────────────────────────────────────────────────────────────┘
        Secret Manager (keys, DB URLs)   Cloud Logging / Error Reporting / Monitoring   Cloud Run job ic-migrate
```

| Component | Service | Key settings |
|---|---|---|
| Web | Cloud Run `ic-web` | public HTTPS, min 1 instance, `BACKEND_URL` baked at build (Cloud Build config in this folder) |
| API | Cloud Run `ic-api` | **internal ingress**, gen2, document bucket mounted at `/app/storage`, `ENVIRONMENT=production`, Secure cookies |
| Worker | Cloud Run `ic-worker` | `worker-http` mode (Celery + health endpoint), CPU always allocated, internal |
| Migrations | Cloud Run job `ic-migrate` | `alembic upgrade head` as the DB owner, then creates the RLS-restricted `intellicredit_app` role |
| Database | Cloud SQL PostgreSQL 16 (`ic-pg`) | private IP only, `ENCRYPTED_ONLY`, regional HA, daily backups (14 kept), PITR (7 days), deletion protection; pgvector via the migration |
| Queue / rate limits | Memorystore Redis 7 (`ic-redis`) | private service access, AUTH, standard tier |
| Documents | Cloud Storage `<project>-ic-documents` | uniform access, public access prevention enforced, object versioning; only the backend service account has access |
| Secrets | Secret Manager | `SECRET_KEY`, DB URLs, app-role password, Redis URL, optional provider keys; readable only by `ic-backend` |
| Observability | Cloud Logging, Error Reporting, Monitoring | JSON logs with `severity` and `stack_trace`; uptime check on `/api/v1/ready` |

Data residency: the default region is `asia-south1` (Mumbai). Change `REGION` if your policy requires otherwise.

## Prerequisites

1. A Google Cloud project with **billing enabled** and someone authorised to approve the costs (Cloud SQL HA,
   Memorystore standard tier and always-on Cloud Run instances are the main drivers — estimate them in the
   Google Cloud pricing calculator before deploying; this repository does not quote prices).
2. `gcloud` CLI installed and authenticated: `gcloud auth login` and `gcloud auth application-default login`.
3. Roles for the operator: Project Owner, or Editor + Security Admin + Service Networking Admin.
4. Optional provider keys exported in your shell (never committed): `LLM_API_KEY`, `EMBEDDING_API_KEY`,
   `TAVILY_API_KEY`, `BRAVE_API_KEY`, `SERPAPI_API_KEY` (with `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `SEARCH_PROVIDER`).

## Deploy

```bash
PROJECT_ID=my-project REGION=asia-south1 deploy/gcp/deploy.sh
```

```bash
PROJECT_ID=my-project REGION=asia-south1 DRY_RUN=0 deploy/gcp/deploy.sh
```

The first command prints the plan. The second creates or updates everything: it is idempotent for resources that
already exist, builds both images with Cloud Build, runs migrations as a Cloud Run job, then deploys the API, worker
and web services. Generated secrets (`SECRET_KEY`, DB owner and app-role passwords) go straight into Secret Manager;
supply `IC_SECRET_KEY`, `IC_DB_OWNER_PASSWORD` and `IC_APP_DB_PASSWORD` yourself to control them.

Optional demo data (synthetic, clearly labelled) — only for a demonstration project:

```bash
gcloud run jobs deploy ic-seed --image=REGION-docker.pkg.dev/PROJECT/intellicredit/backend:TAG --region=REGION --service-account=ic-backend@PROJECT.iam.gserviceaccount.com --network=ic-vpc --subnet=ic-run --vpc-egress=private-ranges-only --set-secrets=SECRET_KEY=ic-secret-key:latest,DATABASE_URL=ic-db-url:latest,MIGRATION_DATABASE_URL=ic-migration-db-url:latest --set-env-vars=DEMO_PASSWORD=choose-a-strong-one --execution-environment=gen2 --add-volume=name=documents,type=cloud-storage,bucket=PROJECT-ic-documents --add-volume-mount=volume=documents,mount-path=/app/storage --args=seed
```

## Verify

```bash
PROJECT_ID=my-project REGION=asia-south1 WEB_URL=https://ic-web-xxxx.asia-south1.run.app DEMO_PASSWORD=<seed password> deploy/gcp/verify.sh
```

`verify.sh` checks that Cloud SQL and Redis have no public endpoints, the bucket blocks public access, the API is
internal-only, runs the end-to-end workflow script (`backend/scripts/verify_stack.py`: login, upload, background
processing, reconciliation, assessment, CAM PDF/DOCX download, figures vs database, audit trail, tenant isolation)
through the public URL, and prints recent errors from Cloud Logging.

After the first deployment, confirm in the audit log that client IPs are the users' public addresses (not
`169.254.x.x`); if not, adjust `TRUSTED_PROXIES` to the proxy hops shown in the API logs.

## HTTPS and custom domains

Cloud Run serves `*.run.app` over Google-managed TLS. For a custom domain, put a global external Application Load
Balancer (serverless NEG → `ic-web`) with a Google-managed certificate in front, and attach a Cloud Armor policy
(WAF rules and edge rate limiting complement the application's Redis rate limiter). Then set `PUBLIC_ORIGIN` and
redeploy so `CORS_ORIGINS` matches.

## Backup and recovery

| What | How | RPO / RTO (indicative) |
|---|---|---|
| Database | Automated daily backups (14 retained) + point-in-time recovery (7 days of WAL) | RPO minutes (PITR); RTO ~1 hour for a clone |
| Documents | Object versioning + soft delete on the bucket | deleted/overwritten objects recoverable |
| Configuration | Secret Manager versions; images tagged by git SHA in Artifact Registry | redeploy any previous tag |

Restore drills (run quarterly):

```bash
gcloud sql instances clone ic-pg ic-pg-restore-test --point-in-time="2026-11-27T10:00:00Z"
```

```bash
gcloud sql export sql ic-pg gs://PROJECT-ic-backups/intellicredit-$(date +%F).sql.gz --database=intellicredit
```

Point a staging copy of the API at the clone and run `verify.sh` against it. Locally, `scripts/backup.sh` and
`scripts/restore.sh` do the same for the Docker Compose stack (see the main README).

## Rollback and teardown

```bash
gcloud run services update-traffic ic-api --region=REGION --to-revisions=PREVIOUS_REVISION=100
```

Teardown deletes billed resources; Cloud SQL has deletion protection and must be unprotected first. Export the
database and bucket before tearing down anything that holds real data.

## Not yet verified in a real project

- That every `gcloud` flag in `deploy.sh` is accepted by the current CLI (validate with `--help` if a step fails).
- Cloud Storage FUSE latency for large PDFs (the app writes each upload once and reads it during processing).
- Client-IP attribution behind the Cloud Run front ends (see the note under *Verify*).
