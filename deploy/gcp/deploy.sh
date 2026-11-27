#!/usr/bin/env bash
# Intelli-Credit on Google Cloud — Cloud Run (web, API, worker, migrate job), Cloud SQL for PostgreSQL 16 (pgvector,
# private IP), Memorystore for Redis (private IP), private Cloud Storage bucket for documents, Secret Manager.
#
# SAFE BY DEFAULT: DRY_RUN=1 prints every command without executing it. Creating these resources incurs charges.
# Review deploy/gcp/README.md, confirm billing/authorisation, then run with DRY_RUN=0.
#
#   PROJECT_ID=my-project REGION=asia-south1 deploy/gcp/deploy.sh                 # dry run (prints the plan)
#   PROJECT_ID=my-project REGION=asia-south1 DRY_RUN=0 deploy/gcp/deploy.sh       # create / update
#
# Secrets are read from the environment of the operator (never written to disk or to this repository):
#   IC_SECRET_KEY, IC_DB_OWNER_PASSWORD, IC_APP_DB_PASSWORD  (generated if unset, printed only as "generated")
#   optional: LLM_API_KEY, EMBEDDING_API_KEY, TAVILY_API_KEY, BRAVE_API_KEY, SERPAPI_API_KEY
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"           # Mumbai; data residency for Indian borrower data
DRY_RUN="${DRY_RUN:-1}"
PREFIX="${PREFIX:-ic}"
TAG="${TAG:-$(git -C "$(dirname "$0")/../.." rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
NETWORK="${PREFIX}-vpc"; SUBNET="${PREFIX}-run"; SUBNET_RANGE="${SUBNET_RANGE:-10.10.0.0/24}"
SQL_INSTANCE="${PREFIX}-pg"; SQL_TIER="${SQL_TIER:-db-custom-2-7680}"; DB_NAME=intellicredit
REDIS_INSTANCE="${PREFIX}-redis"
BUCKET="${BUCKET:-${PROJECT_ID}-${PREFIX}-documents}"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/intellicredit"
SA_BACKEND="${PREFIX}-backend@${PROJECT_ID}.iam.gserviceaccount.com"
SA_WEB="${PREFIX}-web@${PROJECT_ID}.iam.gserviceaccount.com"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

run() { echo "+ $*"; if [ "$DRY_RUN" = "0" ]; then "$@"; fi; }
exists() { [ "$DRY_RUN" = "0" ] && "$@" >/dev/null 2>&1; }
gen() { python3 -c 'import secrets; print(secrets.token_urlsafe(36))'; }
secret() {  # secret NAME VALUE — create or add a new version; value passed on stdin only
  local name="${PREFIX}-$1" value="$2"
  echo "+ gcloud secrets create/versions add ${name} (value from environment, not shown)"
  if [ "$DRY_RUN" = "0" ]; then
    gcloud secrets describe "$name" >/dev/null 2>&1 || gcloud secrets create "$name" --replication-policy=automatic
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=-
  fi
}

echo "== Intelli-Credit GCP deployment (project=${PROJECT_ID} region=${REGION} tag=${TAG} dry_run=${DRY_RUN})"
run gcloud config set project "$PROJECT_ID"
run gcloud services enable run.googleapis.com sqladmin.googleapis.com redis.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com compute.googleapis.com servicenetworking.googleapis.com \
  storage.googleapis.com logging.googleapis.com monitoring.googleapis.com clouderrorreporting.googleapis.com

echo "== Network: private VPC + private services access (Cloud SQL / Memorystore get private IPs only)"
exists gcloud compute networks describe "$NETWORK" || run gcloud compute networks create "$NETWORK" --subnet-mode=custom
exists gcloud compute networks subnets describe "$SUBNET" --region="$REGION" || \
  run gcloud compute networks subnets create "$SUBNET" --network="$NETWORK" --region="$REGION" --range="$SUBNET_RANGE" \
    --enable-private-ip-google-access
exists gcloud compute addresses describe "${PREFIX}-psa" --global || \
  run gcloud compute addresses create "${PREFIX}-psa" --global --purpose=VPC_PEERING --prefix-length=16 --network="$NETWORK"
run gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com --ranges="${PREFIX}-psa" --network="$NETWORK"

echo "== Cloud SQL for PostgreSQL 16 (private IP only, encrypted connections, HA, daily backups + PITR)"
exists gcloud sql instances describe "$SQL_INSTANCE" || \
  run gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --edition=ENTERPRISE --tier="$SQL_TIER" \
    --region="$REGION" --network="projects/${PROJECT_ID}/global/networks/${NETWORK}" --no-assign-ip \
    --availability-type=REGIONAL --storage-auto-increase --backup-start-time=20:30 --enable-point-in-time-recovery \
    --retained-backups-count=14 --retained-transaction-log-days=7 --ssl-mode=ENCRYPTED_ONLY --deletion-protection
exists gcloud sql databases describe "$DB_NAME" --instance="$SQL_INSTANCE" || \
  run gcloud sql databases create "$DB_NAME" --instance="$SQL_INSTANCE"
OWNER_PW="${IC_DB_OWNER_PASSWORD:-$(gen)}"; APP_PW="${IC_APP_DB_PASSWORD:-$(gen)}"; SECRET_KEY_VALUE="${IC_SECRET_KEY:-$(gen)}"
echo "+ gcloud sql users set-password postgres --instance=${SQL_INSTANCE} --password=<from IC_DB_OWNER_PASSWORD or generated>"
[ "$DRY_RUN" = "0" ] && gcloud sql users set-password postgres --instance="$SQL_INSTANCE" --password="$OWNER_PW"
SQL_IP="$( [ "$DRY_RUN" = "0" ] && gcloud sql instances describe "$SQL_INSTANCE" --format='value(ipAddresses[0].ipAddress)' || echo '<sql-private-ip>')"

echo "== Memorystore for Redis 7 (private service access, AUTH enabled)"
exists gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" || \
  run gcloud redis instances create "$REDIS_INSTANCE" --region="$REGION" --size=1 --tier=standard --redis-version=redis_7_2 \
    --network="projects/${PROJECT_ID}/global/networks/${NETWORK}" --connect-mode=PRIVATE_SERVICE_ACCESS --enable-auth
REDIS_HOST="$( [ "$DRY_RUN" = "0" ] && gcloud redis instances describe "$REDIS_INSTANCE" --region="$REGION" --format='value(host)' || echo '<redis-private-ip>')"
REDIS_AUTH="$( [ "$DRY_RUN" = "0" ] && gcloud redis instances get-auth-string "$REDIS_INSTANCE" --region="$REGION" --format='value(authString)' || echo '<auth>')"

echo "== Private document bucket (uniform access, public access prevention, versioning)"
exists gcloud storage buckets describe "gs://${BUCKET}" || \
  run gcloud storage buckets create "gs://${BUCKET}" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
run gcloud storage buckets update "gs://${BUCKET}" --versioning

echo "== Service accounts (least privilege)"
exists gcloud iam service-accounts describe "$SA_BACKEND" || run gcloud iam service-accounts create "${PREFIX}-backend" --display-name="Intelli-Credit API/worker"
exists gcloud iam service-accounts describe "$SA_WEB" || run gcloud iam service-accounts create "${PREFIX}-web" --display-name="Intelli-Credit web"
run gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member="serviceAccount:${SA_BACKEND}" --role=roles/storage.objectAdmin
for role in roles/logging.logWriter roles/monitoring.metricWriter roles/cloudtrace.agent roles/errorreporting.writer; do
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_BACKEND}" --role="$role" --condition=None
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${SA_WEB}" --role="$role" --condition=None
done

echo "== Secrets (Secret Manager; only the backend service account can read them)"
secret secret-key "$SECRET_KEY_VALUE"
secret db-url "postgresql+psycopg://intellicredit_app:${APP_PW}@${SQL_IP}:5432/${DB_NAME}?sslmode=require"
secret migration-db-url "postgresql+psycopg://postgres:${OWNER_PW}@${SQL_IP}:5432/${DB_NAME}?sslmode=require"
secret app-db-password "$APP_PW"
secret redis-url "redis://:${REDIS_AUTH}@${REDIS_HOST}:6379/0"
SECRETS="SECRET_KEY=${PREFIX}-secret-key:latest,DATABASE_URL=${PREFIX}-db-url:latest,MIGRATION_DATABASE_URL=${PREFIX}-migration-db-url:latest,APP_DB_PASSWORD=${PREFIX}-app-db-password:latest,REDIS_URL=${PREFIX}-redis-url:latest"
for opt in LLM_API_KEY EMBEDDING_API_KEY TAVILY_API_KEY BRAVE_API_KEY SERPAPI_API_KEY; do
  if [ -n "${!opt:-}" ]; then
    name="$(echo "$opt" | tr 'A-Z_' 'a-z-')"; secret "$name" "${!opt}"; SECRETS="${SECRETS},${opt}=${PREFIX}-${name}:latest"
  fi
done
for s in secret-key db-url migration-db-url app-db-password redis-url; do
  run gcloud secrets add-iam-policy-binding "${PREFIX}-${s}" --member="serviceAccount:${SA_BACKEND}" --role=roles/secretmanager.secretAccessor
done

echo "== Images (Artifact Registry + Cloud Build)"
exists gcloud artifacts repositories describe intellicredit --location="$REGION" || \
  run gcloud artifacts repositories create intellicredit --repository-format=docker --location="$REGION"
run gcloud builds submit "$ROOT/backend" --tag "${REPO}/backend:${TAG}"

PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-https://${PREFIX}-web-${PROJECT_ID}.${REGION}.run.app}"
COMMON_ENV="ENVIRONMENT=production,COOKIE_SECURE=true,ALLOW_REGISTRATION=${ALLOW_REGISTRATION:-false},LOG_FORMAT=json,JOB_BACKEND=celery,RATE_LIMIT_BACKEND=redis,RATE_LIMIT_FAIL_MODE=local,STORAGE_DIR=/app/storage,CORS_ORIGINS=${PUBLIC_ORIGIN},TRUSTED_PROXIES=169.254.0.0/16;${SUBNET_RANGE};35.191.0.0/16;130.211.0.0/22,LLM_PROVIDER=${LLM_PROVIDER:-none},EMBEDDING_PROVIDER=${EMBEDDING_PROVIDER:-local-hash},SEARCH_PROVIDER=${SEARCH_PROVIDER:-none}"
COMMON_ENV="^@^${COMMON_ENV//,/@}"   # gcloud alternate delimiter: values contain commas (CIDR list uses ';' -> converted below)
COMMON_ENV="${COMMON_ENV//;/,}"
VPC=(--network="$NETWORK" --subnet="$SUBNET")
VOL=(--execution-environment=gen2 --add-volume="name=documents,type=cloud-storage,bucket=${BUCKET}" --add-volume-mount="volume=documents,mount-path=/app/storage")

echo "== Database migrations (Cloud Run job; runs as the database owner, then creates the RLS-restricted app role)"
run gcloud run jobs deploy "${PREFIX}-migrate" --image="${REPO}/backend:${TAG}" --region="$REGION" --service-account="$SA_BACKEND" \
  "${VPC[@]}" --vpc-egress=private-ranges-only --set-secrets="$SECRETS" --set-env-vars="$COMMON_ENV" --args=migrate --max-retries=0 --task-timeout=900
run gcloud run jobs execute "${PREFIX}-migrate" --region="$REGION" --wait

echo "== API (internal ingress only; reachable from the web service through the VPC)"
run gcloud run deploy "${PREFIX}-api" --image="${REPO}/backend:${TAG}" --region="$REGION" --service-account="$SA_BACKEND" \
  --ingress=internal --allow-unauthenticated "${VPC[@]}" --vpc-egress=private-ranges-only "${VOL[@]}" --args=api --port=8000 \
  --set-secrets="$SECRETS" --set-env-vars="$COMMON_ENV" --cpu=2 --memory=2Gi --min-instances=1 --max-instances=10 \
  --concurrency=40 --timeout=300
API_URL="$( [ "$DRY_RUN" = "0" ] && gcloud run services describe "${PREFIX}-api" --region="$REGION" --format='value(status.url)' || echo "https://${PREFIX}-api-<hash>.${REGION}.run.app")"

echo "== Worker (Celery + health endpoint; CPU always allocated, internal only)"
run gcloud run deploy "${PREFIX}-worker" --image="${REPO}/backend:${TAG}" --region="$REGION" --service-account="$SA_BACKEND" \
  --ingress=internal --no-allow-unauthenticated "${VPC[@]}" --vpc-egress=private-ranges-only "${VOL[@]}" --args=worker-http \
  --set-secrets="$SECRETS" --set-env-vars="$COMMON_ENV" --no-cpu-throttling --cpu=2 --memory=4Gi --min-instances=1 --max-instances=3

echo "== Web (public HTTPS; proxies /api to the internal API through the VPC)"
run gcloud builds submit "$ROOT/frontend" --config="$ROOT/deploy/gcp/cloudbuild-frontend.yaml" \
  --substitutions="_IMAGE=${REPO}/frontend:${TAG},_BACKEND_URL=${API_URL}"
run gcloud run deploy "${PREFIX}-web" --image="${REPO}/frontend:${TAG}" --region="$REGION" --service-account="$SA_WEB" \
  --ingress=all --allow-unauthenticated "${VPC[@]}" --vpc-egress=all-traffic --port=3000 --cpu=1 --memory=1Gi \
  --min-instances=1 --max-instances=10
WEB_URL="$( [ "$DRY_RUN" = "0" ] && gcloud run services describe "${PREFIX}-web" --region="$REGION" --format='value(status.url)' || echo "$PUBLIC_ORIGIN")"

echo "== Monitoring: uptime check on the public readiness endpoint"
run gcloud monitoring uptime create "${PREFIX}-ready" --resource-type=uptime-url \
  --resource-labels="host=${WEB_URL#https://},project_id=${PROJECT_ID}" --path=/api/v1/ready --protocol=https --period=5

echo "== Done. Web: ${WEB_URL}   API (internal): ${API_URL}"
echo "   Next: deploy/gcp/verify.sh (end-to-end check + logs). Optional demo data: gcloud run jobs deploy ${PREFIX}-seed ... --args=seed"
