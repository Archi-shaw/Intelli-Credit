#!/usr/bin/env bash
# Post-deployment verification for Intelli-Credit on Google Cloud.
#   PROJECT_ID=my-project REGION=asia-south1 WEB_URL=https://ic-web-xxxx.asia-south1.run.app deploy/gcp/verify.sh
# Needs: gcloud (authenticated), backend/.venv (for the end-to-end script), the demo seed (or adapt the accounts).
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"; : "${WEB_URL:?set WEB_URL}"
REGION="${REGION:-asia-south1}"; PREFIX="${PREFIX:-ic}"; ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
fail=0
check() { if eval "$2"; then echo "  ok    $1"; else echo "  FAIL  $1"; fail=1; fi; }

echo "== Infrastructure exposure"
check "Cloud SQL has no public IP" \
  "[ -z \"\$(gcloud sql instances describe ${PREFIX}-pg --project $PROJECT_ID --format='value(ipAddresses.filter(type=PRIMARY).ipAddress)')\" ]"
check "Cloud SQL requires encrypted connections" \
  "[ \"\$(gcloud sql instances describe ${PREFIX}-pg --project $PROJECT_ID --format='value(settings.ipConfiguration.sslMode)')\" = ENCRYPTED_ONLY ]"
check "Memorystore reachable only via private service access" \
  "[ \"\$(gcloud redis instances describe ${PREFIX}-redis --region $REGION --project $PROJECT_ID --format='value(connectMode)')\" = PRIVATE_SERVICE_ACCESS ]"
check "Document bucket enforces public access prevention" \
  "[ \"\$(gcloud storage buckets describe gs://${PROJECT_ID}-${PREFIX}-documents --format='value(public_access_prevention)')\" = enforced ]"
check "API ingress is internal-only" \
  "[ \"\$(gcloud run services describe ${PREFIX}-api --region $REGION --project $PROJECT_ID --format='value(metadata.annotations.\"run.googleapis.com/ingress\")')\" = internal ]"
check "API is not reachable from the internet" \
  "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -m 10 \"\$(gcloud run services describe ${PREFIX}-api --region $REGION --project $PROJECT_ID --format='value(status.url)')/health\")\" != 200 ]"

echo "== End-to-end workflow through the public web URL"
"$ROOT/backend/.venv/bin/python" "$ROOT/backend/scripts/verify_stack.py" --base "$WEB_URL" || fail=1

echo "== Recent errors in Cloud Logging (last 30 minutes)"
gcloud logging read "resource.type=\"cloud_run_revision\" AND resource.labels.service_name:\"${PREFIX}-\" AND severity>=ERROR AND timestamp>=\"$(date -u -d '-30 min' +%Y-%m-%dT%H:%M:%SZ)\"" \
  --project "$PROJECT_ID" --limit 20 --format='table(timestamp,resource.labels.service_name,jsonPayload.msg)' || true
echo "== Worker and API instances"
gcloud run services list --region "$REGION" --project "$PROJECT_ID" --format='table(metadata.name,status.url,status.conditions[0].status)'
[ "$fail" = 0 ] && echo "VERIFIED" || { echo "VERIFICATION FAILED"; exit 1; }
