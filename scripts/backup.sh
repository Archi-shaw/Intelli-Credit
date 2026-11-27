#!/usr/bin/env bash
# Back up the Intelli-Credit database (pg_dump custom format) and private document storage, with checksums.
#   scripts/backup.sh                 # Docker Compose stack (services db + api)
#   TARGET=dev scripts/backup.sh      # local development containers (intellicredit-dev-db + backend/storage)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${OUT:-backups/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
DB="${POSTGRES_DB:-intellicredit}"; USER_="${POSTGRES_USER:-intelli}"
COUNTS="select 'companies='||(select count(*) from companies)||' applications='||(select count(*) from credit_applications)||' documents='||(select count(*) from uploaded_documents)||' audit='||(select count(*) from audit_logs)"
case "${TARGET:-compose}" in
  compose)
    docker compose exec -T db pg_dump -U "$USER_" -Fc "$DB" > "$OUT/intellicredit.dump"
    docker compose exec -T db psql -U "$USER_" -d "$DB" -tAc "$COUNTS" > "$OUT/ROWCOUNTS"
    docker compose exec -T api tar -C /app -czf - storage > "$OUT/storage.tar.gz" ;;
  dev)
    docker exec intellicredit-dev-db pg_dump -U intelli -Fc intellicredit > "$OUT/intellicredit.dump"
    docker exec intellicredit-dev-db psql -U intelli -d intellicredit -tAc "$COUNTS" > "$OUT/ROWCOUNTS"
    tar -C backend -czf "$OUT/storage.tar.gz" storage ;;
  *) echo "TARGET must be compose or dev" >&2; exit 2 ;;
esac
(cd "$OUT" && sha256sum intellicredit.dump storage.tar.gz ROWCOUNTS > SHA256SUMS)
echo "Backup written to $OUT ($(cat "$OUT/ROWCOUNTS"))"
