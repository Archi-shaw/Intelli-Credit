#!/usr/bin/env bash
# Build and (re)start the Intelli-Credit production stack on the VM, then wait until it is healthy.
# Run from the repository checkout on the VM after generate-env.sh. Safe to re-run for updates.
#
#   deploy/compute-engine/deploy.sh              # build current checkout, migrate, start
#   GIT_REF=v1.2.0 deploy/compute-engine/deploy.sh   # check out a tag/commit first (fast-forward only for branches)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE — run deploy/compute-engine/generate-env.sh <domain> first" >&2; exit 1; }
perm="$(stat -c %a "$ENV_FILE")"
[ "$perm" = 600 ] || [ "$perm" = 400 ] || { echo "$ENV_FILE has mode $perm; run: chmod 600 $ENV_FILE" >&2; exit 1; }
grep -q '^COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml' "$ENV_FILE" \
  || { echo "$ENV_FILE must set COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml" >&2; exit 1; }
git check-ignore -q .env || { echo ".env is not git-ignored — refusing to continue" >&2; exit 1; }

if [ -n "${GIT_REF:-}" ]; then
  git fetch --tags origin
  git checkout "$GIT_REF"
  git symbolic-ref -q HEAD >/dev/null && git pull --ff-only
fi
echo "== Deploying $(git rev-parse --short HEAD) ($(git log -1 --format=%s))"

echo "== Validating Compose configuration"
docker compose config --quiet
# Only the frontend may be published, and only on loopback (nginx proxies to it).
published="$(docker compose config --format json | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
for name, svc in cfg.get("services", {}).items():
    for p in svc.get("ports", []) or []:
        print(name, p.get("host_ip", ""), p.get("published", ""))')"
echo "$published" | sed 's/^/   published: /'
echo "$published" | awk 'NF && !($1=="frontend" && $2=="127.0.0.1") {bad=1} END {exit bad}' \
  || { echo "unexpected published port (only frontend on 127.0.0.1 is allowed)" >&2; exit 1; }

echo "== Building images"
docker compose build --pull

echo "== Starting (migrations run first; api/worker/frontend wait for them)"
docker compose up -d --remove-orphans

echo "== Waiting for health"
for i in $(seq 1 60); do
  unhealthy="$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$1!="migrate" && $2!="healthy" && $2!=""')"
  migrate_exit="$(docker compose ps -a --format '{{.Service}} {{.ExitCode}} {{.State}}' | awk '$1=="migrate"{print $2}')"
  if [ -z "$unhealthy" ] && [ "${migrate_exit:-0}" = 0 ]; then break; fi
  sleep 5
done
docker compose ps -a
[ -z "$unhealthy" ] || { echo "not healthy after 5 minutes: $unhealthy — see: docker compose logs --tail=100" >&2; exit 1; }
[ "${migrate_exit:-0}" = 0 ] || { echo "migrations failed — see: docker compose logs migrate" >&2; exit 1; }

env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
WEB_PORT="$(env_value IC_WEB_PORT)"; ORIGIN="$(env_value PUBLIC_ORIGIN)"
curl -fsS "http://127.0.0.1:${WEB_PORT:-3080}/api/v1/ready" | python3 -m json.tool
echo "== Deployed $(git rev-parse --short HEAD). Open ${ORIGIN:-the site} in a browser, then run deploy/compute-engine/verify.sh from your machine."
