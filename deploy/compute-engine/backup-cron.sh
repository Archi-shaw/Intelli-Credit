#!/usr/bin/env bash
# Nightly backup wrapper for the VM: database dump + document storage via scripts/backup.sh, local retention,
# optional copy to a private Cloud Storage bucket. Install with:
#   echo "30 2 * * * $USER $(pwd)/deploy/compute-engine/backup-cron.sh >> /var/backups/intellicredit/cron.log 2>&1" \
#     | sudo tee /etc/cron.d/intellicredit-backup
# Environment: BACKUP_ROOT (default /var/backups/intellicredit), KEEP_DAYS (default 14),
#              BACKUP_BUCKET (optional, e.g. gs://my-project-ic-backups; the VM needs write access to it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/intellicredit}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
umask 077
cd "$ROOT"   # so `docker compose` picks up .env (COMPOSE_FILE=... production overlay)
OUT="$BACKUP_ROOT/$STAMP" scripts/backup.sh
if [ -n "${BACKUP_BUCKET:-}" ]; then
  gcloud storage cp -r "$BACKUP_ROOT/$STAMP" "${BACKUP_BUCKET%/}/"
fi
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$KEEP_DAYS" -exec rm -rf {} +
echo "$(date -Is) backup $STAMP ok"
