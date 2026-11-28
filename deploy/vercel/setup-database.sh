#!/usr/bin/env bash
# Prepare the managed PostgreSQL database (e.g. Neon) used by the Vercel deployment. Run on YOUR machine, from the
# repository, with the backend virtualenv installed (README "Option B"). Nothing here runs on Vercel.
#
#   1. migrations (as the database owner)          alembic upgrade head
#   2. the RLS-restricted application role          python -m app.cli create-app-role
#   3. optional synthetic demo seed (SEED=1)        python -m app.cli seed   (documents stored in PostgreSQL)
#
# Required environment (never commit these; the script prints no secret values):
#   MIGRATION_DATABASE_URL  owner connection, DIRECT (non-pooled) host, sslmode=require
#   APP_DB_PASSWORD         password for the application role (>= 12 chars)
#   DATABASE_URL            application-role connection the API will use (pooled host is fine) — needed for SEED=1
#   DEMO_PASSWORD           only with SEED=1: password for the synthetic demo users
#   DB_APP_ROLE             optional, default intellicredit_app
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/backend"
PY="${PYTHON:-.venv/bin/python}"
: "${MIGRATION_DATABASE_URL:?set MIGRATION_DATABASE_URL (database owner, direct connection)}"
: "${APP_DB_PASSWORD:?set APP_DB_PASSWORD (>= 12 characters)}"
case "$MIGRATION_DATABASE_URL" in *sslmode=*) ;; *) echo "warning: MIGRATION_DATABASE_URL has no sslmode (managed databases usually need sslmode=require)" >&2 ;; esac

echo "== Migrations"
DATABASE_URL="$MIGRATION_DATABASE_URL" MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" "$PY" -m alembic upgrade head
echo "== Application role (${DB_APP_ROLE:-intellicredit_app}, row-level security enforced)"
DATABASE_URL="$MIGRATION_DATABASE_URL" MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" \
  "$PY" -m app.cli create-app-role --password "$APP_DB_PASSWORD"

if [ "${SEED:-0}" = 1 ]; then
  : "${DATABASE_URL:?set DATABASE_URL (application role) to seed}"
  : "${DEMO_PASSWORD:?set DEMO_PASSWORD to seed demo users}"
  echo "== Synthetic demo seed (processing runs here, on this machine; files are stored in PostgreSQL)"
  SYNTHETIC_DIR="$(mktemp -d)"; trap 'rm -rf "$SYNTHETIC_DIR"' EXIT  # generated demo files; repository untouched
  SYNTHETIC_DATA_DIR="$SYNTHETIC_DIR" \
  ENVIRONMENT=production COOKIE_SECURE=true STORAGE_BACKEND=database JOB_BACKEND=sync RATE_LIMIT_BACKEND=memory \
    DB_POOL=null DATABASE_URL="$DATABASE_URL" MIGRATION_DATABASE_URL="$MIGRATION_DATABASE_URL" \
    SECRET_KEY="${SECRET_KEY:-$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')}" \
    DEMO_PASSWORD="$DEMO_PASSWORD" "$PY" -m app.cli seed | grep -v -i password
fi
echo "Database ready."
