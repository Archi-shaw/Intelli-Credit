#!/bin/sh
# Usage: docker-entrypoint.sh api | worker | worker-http | migrate | seed | <any command>
set -e
case "$1" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${API_WORKERS:-1}" --no-server-header
    ;;
  worker)
    exec celery -A app.workers.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --concurrency="${WORKER_CONCURRENCY:-2}" -Q intellicredit
    ;;
  worker-http)
    # Celery worker plus a tiny HTTP health endpoint on $PORT (for platforms such as Cloud Run services).
    celery -A app.workers.celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --concurrency="${WORKER_CONCURRENCY:-2}" -Q intellicredit &
    exec python -m app.workers.health_server --pid $! --port "${PORT:-8080}"
    ;;
  migrate)
    # Runs as the database owner (MIGRATION_DATABASE_URL), then (re)creates the non-superuser app role.
    alembic upgrade head
    python -m app.cli create-app-role
    ;;
  seed)
    python -m app.cli seed ${SEED_ARGS:-}
    ;;
  *)
    exec "$@"
    ;;
esac
