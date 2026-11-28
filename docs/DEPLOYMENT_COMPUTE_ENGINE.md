# Deploying Intelli-Credit on Google Compute Engine

One Ubuntu 24.04 VM runs the existing Docker Compose production stack (`docker-compose.yml` +
`docker-compose.prod.yml`). nginx on the host terminates HTTPS with a Let's Encrypt certificate and proxies to the
Next.js frontend, the only published service (on `127.0.0.1:3080`). This is the recommended target for a
demonstration deployment. The Cloud Run / Cloud SQL / Memorystore package in [`deploy/gcp/`](../deploy/gcp/README.md)
is the managed, horizontally scalable alternative.

> [!NOTE]
> **Status:** the scripts in [`deploy/compute-engine/`](../deploy/compute-engine/) have been rehearsed locally:
> the production overlay ran behind nginx 1.24 with TLS (a self-signed certificate) and the verification scripts
> passed (see [BUILD_STATUS.md](BUILD_STATUS.md)). `create-vm.sh` has only been run as a dry run, and no VM,
> domain or Let's Encrypt certificate has been created yet.

```text
Internet ──443/80──► nginx (host, TLS, Let's Encrypt) ──► 127.0.0.1:3080 frontend (Next.js, proxies /api/*)
                                                                     │  Docker network (private)
                                          api (FastAPI :8000) ◄──────┘
                                            │        │
                          db (PostgreSQL 16 + pgvector, RLS)   redis (queue, rate limits) ◄── worker (Celery)
                          volumes: pgdata · redisdata · storage (uploaded documents, shared by api + worker)
```

| Item | Value |
|---|---|
| Region / zone | `asia-south1` (Mumbai) / `asia-south1-a` |
| Machine | `e2-standard-4` (4 vCPU, 16 GB RAM) |
| Disk | 50 GB balanced persistent disk (`DISK_TYPE=pd-ssd` for SSD), daily snapshots kept for 7 days |
| OS | Ubuntu 24.04 LTS, Shielded VM, OS Login, deletion protection |
| Network | dedicated VPC `ic-net`; ingress only tcp/80 and tcp/443 from anywhere, tcp/22 only from Google IAP (`35.235.240.0/20`) or your `ADMIN_CIDR` |
| Public | nginx on 80 (redirects to HTTPS, serves ACME challenges) and 443 |
| Private | PostgreSQL, Redis and the API are not published at all; the frontend is published on loopback only |

## 1. Prerequisites

- A Google Cloud project with **billing enabled**, and your approval of the costs (§13).
- The `gcloud` CLI on your machine, authenticated: `gcloud auth login`.
- IAM: `roles/compute.admin`, `roles/iap.tunnelResourceAccessor` and `roles/compute.osAdminLogin` (OS Login with
  sudo), plus `roles/serviceusage.serviceUsageAdmin` to enable APIs — or Project Owner.
- A domain name whose DNS you control (an `A` record will point at the VM). Let's Encrypt cannot issue for a bare IP,
  and the session cookie is `Secure`-only, so **sign-in only works over HTTPS**.

```bash
gcloud auth list
```

```bash
gcloud config get-value project
```

```bash
gcloud billing projects describe PROJECT_ID --format='value(billingEnabled)'
```

```bash
gcloud compute instances list --filter="zone ~ asia-south1"
```

## 2. Create the VM

`create-vm.sh` prints its plan unless `DRY_RUN=0` is set. It only creates resources that don't exist yet, and never
modifies or deletes existing ones.

```bash
PROJECT_ID=my-project deploy/compute-engine/create-vm.sh
```

```bash
PROJECT_ID=my-project DRY_RUN=0 deploy/compute-engine/create-vm.sh
```

Optional variables: `REGION`, `ZONE`, `VM_NAME`, `MACHINE_TYPE`, `DISK_SIZE`, `DISK_TYPE`, `ADMIN_CIDR` (for example
`203.0.113.7/32` to also allow direct SSH from your IP) and `SNAPSHOT_RETENTION_DAYS`. The script prints the static IP
when it finishes. Create the DNS record now (`A credit.example.com → <static IP>`), then check that it resolves:

```bash
dig +short credit.example.com
```

The equivalent manual steps are the `gcloud` commands that the dry run prints: a custom-mode VPC and subnet, two (or
three) firewall rules targeting the `ic-web` tag, a regional static address, the instance, and a snapshot schedule
attached to its disk.

## 3. Install Docker, nginx and certbot on the VM

```bash
gcloud compute ssh ic-vm --zone=asia-south1-a --tunnel-through-iap
```

On the VM:

```bash
git clone https://github.com/Archi-shaw/Intelli-Credit.git ~/intelli-credit
```

```bash
cd ~/intelli-credit && sudo deploy/compute-engine/bootstrap-vm.sh
```

`bootstrap-vm.sh` installs Docker Engine with the Compose v2 and buildx plugins from Docker's official apt
repository, plus nginx, certbot and git. It also enables unattended security upgrades, caps container logs at
5 × 20 MB per container, adds your user to the `docker` group and creates `/var/backups/intellicredit`. Log out and
back in so the group change takes effect.

## 4. Environment

```bash
cd ~/intelli-credit && deploy/compute-engine/generate-env.sh credit.example.com
```

This writes `.env` with mode `600`, generates `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and
`DEMO_PASSWORD` (none of them are printed), and refuses to overwrite an existing `.env`. Changing database
passwords after the volume has been initialised locks the application out of the database. The file also sets
`COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`, so every `docker compose` command in this directory uses
the production overlay, including the backup and restore scripts.

| Variable | Production value | Notes |
|---|---|---|
| `PUBLIC_ORIGIN` | `https://credit.example.com` | Becomes `CORS_ORIGINS` |
| `SECRET_KEY` | generated (64 chars) | Signs sessions; the API refuses to start if it is a default or shorter than 32 chars |
| `POSTGRES_PASSWORD` | generated | Database owner; used only by `migrate` (`MIGRATION_DATABASE_URL`) |
| `APP_DB_PASSWORD` | generated | Non-superuser `intellicredit_app` role used by the API and worker (`DATABASE_URL`), so row-level security applies |
| `ALLOW_REGISTRATION` | `false` | Accounts are created by an organisation admin |
| `COOKIE_SECURE` | forced `true` by the overlay | HTTPS-only session cookie |
| `DATABASE_URL`, `MIGRATION_DATABASE_URL` | assembled by `docker-compose.prod.yml` | Don't set them in `.env` |
| `IC_WEB_BIND` / `IC_WEB_PORT` | `127.0.0.1` / `3080` | Loopback only; nginx proxies to it |
| `DEMO_PASSWORD` | generated | Only used by the optional demo seed |
| `LLM_PROVIDER`, `LLM_API_KEY` | `none` / blank | `anthropic` plus a key to enable Claude answers; otherwise answers are extractive and cited |
| `EMBEDDING_PROVIDER`, `EMBEDDING_API_KEY` | `local-hash` / blank | `voyage` plus a key for semantic retrieval |
| `SEARCH_PROVIDER`, `SEARCH_API_KEY` (or `TAVILY_API_KEY` / `BRAVE_API_KEY` / `SERPAPI_API_KEY`) | `none` / blank | `tavily`, `brave` or `serpapi`; without one, research is recorded as *not configured* |

All provider keys are optional. The supported names and behaviour are in [PROVIDERS.md](PROVIDERS.md). Edit
`.env` with `nano .env` (never `cat` it into logs), then re-run `deploy.sh`.

## 5. Deploy

```bash
cd ~/intelli-credit && deploy/compute-engine/deploy.sh
```

`deploy.sh` performs these steps in order:

1. Checks that `.env` exists, has mode 600, selects the production overlay and is git-ignored.
2. Validates the Compose configuration and refuses to continue if any service other than the frontend publishes a
   port, or if the frontend publishes on anything other than loopback.
3. Builds the images.
4. Starts the stack. `migrate` runs `alembic upgrade head` as the database owner and then (re)creates the
   RLS-restricted application role. The api, worker and frontend wait for it to finish.
5. Waits until every service is healthy, then prints `/api/v1/ready`.

Every long-running service has `restart: unless-stopped`, and Docker is enabled at boot, so the stack comes back
by itself after a reboot.

## 6. nginx and HTTPS

The DNS record must already resolve to the VM.

```bash
sudo deploy/compute-engine/setup-nginx.sh credit.example.com you@example.com
```

Phase 1 installs a temporary HTTP-only site that only answers ACME challenges, then obtains the certificate with
`certbot certonly --webroot`. Phase 2 installs
[`nginx/intellicredit.conf.template`](../deploy/compute-engine/nginx/intellicredit.conf.template), which does the
following:

- Redirects HTTP to HTTPS.
- Accepts TLS 1.2 and 1.3 only, and sends HSTS.
- Does not disclose the nginx version.
- Sets `client_max_body_size 26m`, which is `MAX_UPLOAD_MB` (25) plus the 1 MB multipart allowance the API accepts.
- Sets `proxy_read_timeout 180s`, matching the frontend's `/api` proxy timeout. Assistant chat and research call
  external providers synchronously.
- Overwrites `X-Forwarded-For` with the real client address, so per-IP rate limits and the audit log can't be
  spoofed. The API trusts only the Docker network hops (`TRUSTED_PROXIES=172.16.0.0/12`).

Renewal runs from certbot's systemd timer, and a deploy hook reloads nginx. The script ends with a renewal dry run.
Add `STAGING=1` to test against the Let's Encrypt staging CA first; its certificates are not trusted by browsers.

If you raise `MAX_UPLOAD_MB` in `.env`, raise `client_max_body_size` to match.

## 7. Verify

From your own machine (not the VM), with the backend virtualenv installed:

```bash
DOMAIN=credit.example.com SKIP_WORKFLOW=1 deploy/compute-engine/verify.sh
```

This checks the following:

- The certificate chain verifies.
- HTTP redirects to HTTPS.
- The HSTS and nosniff headers are present, and the server version is hidden.
- `/ready` returns 200.
- Self-registration is refused.
- Ports 3000, 3080, 5432–5435, 6379, 6380, 8000, 8001 and 8080 are **closed** from the internet.

After seeding the synthetic demo (§8), run the full verification:

```bash
DOMAIN=credit.example.com DEMO_PASSWORD='<from .env on the VM>' deploy/compute-engine/verify.sh
```

The full run adds these checks:

- **Session cookie flags:** `Secure`, `HttpOnly` and `SameSite=Lax`.
- **`verify_stack.py`:** login, upload of the synthetic PDF and CSV documents, background processing, rejection of an
  invalid file, reconciliation, risk assessment and recommendation, CAM PDF and DOCX generation and download, CAM
  figures against the database, the audit trail, cross-organisation 404s and an anonymous 401.
- **`security_probe.py`:** 42 live checks. It ends with about 26 failed logins, so logins from your IP are throttled
  for about a minute afterwards.

Manual checks the scripts do not cover:

- A browser login and logout.
- Upload of an XLSX, a DOCX and a PNG/JPG (synthetic only).
- The AI assistant, if a provider is configured.
- A reviewer decision with justification.

Use **synthetic demonstration data only**. Never upload real customer documents to a demo deployment.

## 8. Optional demo seeding

```bash
docker compose run --rm seed
```

The seed creates two synthetic organisations and pushes three synthetic borrower cases through the real pipeline.
It runs with the production settings, so it refuses to create demo users unless `DEMO_PASSWORD` is set (which
`generate-env.sh` does). The demo accounts are listed in the README. On any deployment that is not a demo, create
real users instead: an admin can add users in **Settings → Users**.

## 9. Migrations

Migrations run automatically on every `deploy.sh` and `docker compose up`. To run them by hand, or to check the
current revision:

```bash
docker compose run --rm migrate
```

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version_num from alembic_version"'
```

`/api/v1/ready` reports `migrations: not_applied` when the database is behind the code.

## 10. Logs and troubleshooting

```bash
docker compose ps -a
```

```bash
docker compose logs -f --tail=200 api worker
```

```bash
sudo tail -f /var/log/nginx/error.log
```

| Symptom | Fix |
|---|---|
| `502 Bad Gateway` from nginx | The frontend isn't up: `docker compose ps`, `docker compose logs frontend api` |
| Login seems to succeed but you're bounced back to the login page | The site was opened over plain HTTP, or `PUBLIC_ORIGIN` doesn't match the URL. The cookie is Secure-only |
| `413 Request Entity Too Large` | The file is over 25 MB (the application limit) |
| `504` on an assistant question | The provider took longer than 180 s; check `docker compose logs api` |
| certbot fails | DNS doesn't point at the VM yet, or tcp/80 is blocked; `dig +short <domain>` and the `ic-allow-web` rule |
| API refuses to start | The log line names the insecure setting (`SECRET_KEY`, `COOKIE_SECURE`, …) |
| Documents stay *Queued* | `docker compose ps worker`; `docker compose exec api python -m app.cli recover-jobs` |

More troubleshooting is in [OPERATIONS.md](OPERATIONS.md).

## 11. Backup and restore

There are two layers of backup:

1. **Disk snapshots:** daily, kept for 7 days, created by `create-vm.sh`. They are crash-consistent and recover the
   whole VM.
2. **Application backups:** a `pg_dump` of the database plus the document storage, with checksums:

```bash
echo "30 2 * * * $USER $HOME/intelli-credit/deploy/compute-engine/backup-cron.sh >> /var/backups/intellicredit/cron.log 2>&1" | sudo tee /etc/cron.d/intellicredit-backup
```

```bash
deploy/compute-engine/backup-cron.sh
```

Backups go to `/var/backups/intellicredit/<timestamp>/` and are kept for 14 days (`KEEP_DAYS`). To also keep a copy
off the VM, set `BACKUP_BUCKET=gs://<private bucket>` in the cron line and give the VM write access to that bucket
(a service account with `roles/storage.objectCreator` on the bucket, and the `storage-rw` scope).

Restore drill (non-destructive: restores into a scratch database, verifies row counts, the 31 RLS policies, the
2 append-only triggers and the migration revision, then drops the scratch database):

```bash
scripts/restore.sh /var/backups/intellicredit/<timestamp>
```

Replacing the live database (destructive; stop the api and worker first):

```bash
CONFIRM_REPLACE_LIVE=yes scripts/restore.sh /var/backups/intellicredit/<timestamp> --replace-live
```

Documents are restored by extracting `storage.tar.gz` into the `storage` volume:

```bash
docker compose exec -T api tar -C /app -xzf - < /var/backups/intellicredit/<timestamp>/storage.tar.gz
```

## 12. Updating, rollback and restarts

Update to the latest `main`:

```bash
cd ~/intelli-credit && git pull --ff-only && deploy/compute-engine/deploy.sh
```

Deploy a specific tag or commit:

```bash
GIT_REF=<tag-or-commit> deploy/compute-engine/deploy.sh
```

**Rollback:** take a backup first (`deploy/compute-engine/backup-cron.sh`), then deploy the previous commit with
`GIT_REF=<previous commit>`. Migrations only move forward. If a release added a migration, restore the pre-update
backup instead of running old code against a newer schema.

Restart commands:

```bash
docker compose restart api worker
```

```bash
docker compose down && docker compose up -d
```

`docker compose down` keeps the volumes (database, Redis and documents). **Never add `-v`**: it deletes the
database and every uploaded document.

## 13. Monthly cost considerations

Indicative on-demand prices for `asia-south1`, in USD, before taxes and credits. **Confirm them in the
[Google Cloud pricing calculator](https://cloud.google.com/products/calculator) before approving.**

| Item | Approx. / month | Basis |
|---|---:|---|
| e2-standard-4 VM, running 24×7 | ~$118 | ~$0.161/hour on demand; E2 machines get no sustained-use discount. A 1-year commitment is ~$74, a 3-year commitment ~$53 |
| 50 GB balanced persistent disk | ~$6 | ~$0.12/GB-month (pd-ssd costs roughly 1.7× as much) |
| Static external IPv4 attached to the VM | ~$3.65 | $0.005/hour |
| Disk snapshots, 7 days retained | ~$1–3 | Incremental; depends on how much data changes |
| Internet egress (Premium tier) | usage-based | ~$0.12/GB; a demo with a few users stays at a few GB |
| **Total** | **≈ $130/month** | Stopping the VM when it isn't in use leaves only disk, IP and snapshots (≈ $11/month) |

Sources: third-party price aggregator [gcloud-compute.com](https://gcloud-compute.com/e2-standard-4.html) (VM and
disk) and Google's published external IP rate. Optional provider APIs (Anthropic, Voyage, search) are billed
separately by those providers.

Ways to reduce cost: `e2-standard-2` (8 GB RAM) is enough for a light demo, and stopping the VM outside demo hours
cuts the VM charge:

```bash
gcloud compute instances stop ic-vm --zone=asia-south1-a
```

## 14. Security checklist (what this deployment preserves)

- PostgreSQL and Redis are reachable only on the private Docker network, and the API port is not published.
- The firewall allows only 80/443 publicly. SSH is reachable only through IAP or your `ADMIN_CIDR`, and the dedicated
  VPC means the default network's permissive rules never apply.
- `ENVIRONMENT=production`: the API refuses insecure settings, the session cookie is Secure, HttpOnly and
  SameSite=Lax, and the API docs are disabled.
- Registration is disabled.
- The API and worker connect as the non-superuser `intellicredit_app` role, so PostgreSQL row-level security applies
  on top of the API's organisation scoping.
- The audit log is append-only, enforced by database triggers.
- Redis-backed rate limiting runs per IP, login, user, organisation, AI endpoint and processing endpoint.
- Secrets live only in `.env` (mode 600, git-ignored) and are never printed by the scripts.
