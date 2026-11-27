#!/usr/bin/env bash
# Verify or restore an Intelli-Credit backup made by scripts/backup.sh.
#   scripts/restore.sh BACKUP_DIR                    # SAFE: restore into a scratch DB, compare row counts, drop it
#   TARGET=dev scripts/restore.sh BACKUP_DIR         # same, against the local development container
#   CONFIRM_REPLACE_LIVE=yes scripts/restore.sh BACKUP_DIR --replace-live   # overwrite the live DB (destructive)
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${1:?usage: scripts/restore.sh BACKUP_DIR [--replace-live]}"; MODE="${2:-verify}"
(cd "$DIR" && sha256sum -c SHA256SUMS --quiet) && echo "checksums ok"
DB="${POSTGRES_DB:-intellicredit}"; USER_="${POSTGRES_USER:-intelli}"
if [ "${TARGET:-compose}" = dev ]; then psql_() { docker exec -i intellicredit-dev-db psql -U intelli "$@"; }; restore_() { docker exec -i intellicredit-dev-db pg_restore -U intelli "$@"; }
else psql_() { docker compose exec -T db psql -U "$USER_" "$@"; }; restore_() { docker compose exec -T db pg_restore -U "$USER_" "$@"; }; fi
COUNTS="select 'companies='||(select count(*) from companies)||' applications='||(select count(*) from credit_applications)||' documents='||(select count(*) from uploaded_documents)||' audit='||(select count(*) from audit_logs)"
if [ "$MODE" = "--replace-live" ]; then
  [ "${CONFIRM_REPLACE_LIVE:-}" = yes ] || { echo "Refusing: set CONFIRM_REPLACE_LIVE=yes to overwrite the live database." >&2; exit 2; }
  restore_ --clean --if-exists --no-owner -d "$DB" < "$DIR/intellicredit.dump"
  echo "Live database restored. Restore documents with: tar -xzf $DIR/storage.tar.gz (into the storage volume)."
  exit 0
fi
SCRATCH="${DB}_restore_check"
psql_ -d postgres -qc "DROP DATABASE IF EXISTS ${SCRATCH} WITH (FORCE)" -c "CREATE DATABASE ${SCRATCH}"
restore_ --no-owner --exit-on-error -d "$SCRATCH" < "$DIR/intellicredit.dump"
GOT="$(psql_ -d "$SCRATCH" -tAc "$COUNTS")"
POL="$(psql_ -d "$SCRATCH" -tAc "select count(*) from pg_policies")"
TRG="$(psql_ -d "$SCRATCH" -tAc "select count(*) from pg_trigger where tgname like '%append_only'")"
REV="$(psql_ -d "$SCRATCH" -tAc "select version_num from alembic_version")"
psql_ -d postgres -qc "DROP DATABASE ${SCRATCH} WITH (FORCE)"
tar -tzf "$DIR/storage.tar.gz" > /dev/null && echo "storage archive readable ($(tar -tzf "$DIR/storage.tar.gz" | grep -c -v '/$') files)"
echo "expected: $(cat "$DIR/ROWCOUNTS")"
echo "restored: ${GOT}   (rls policies=${POL}, append-only triggers=${TRG}, migration=${REV})"
[ "$GOT" = "$(cat "$DIR/ROWCOUNTS" | sed 's/^rows: //')" ] && [ "$POL" = 31 ] && [ "$TRG" = 2 ] && echo "RESTORE VERIFIED" || { echo "RESTORE MISMATCH"; exit 1; }
