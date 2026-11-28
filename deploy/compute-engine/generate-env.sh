#!/usr/bin/env bash
# Write the production .env (repository root) with freshly generated secrets. Secrets are never printed.
# Refuses to overwrite an existing .env — rotating database passwords on an initialised volume locks the app out.
#
#   deploy/compute-engine/generate-env.sh credit.example.com
#
# Afterwards edit .env only for the optional provider keys (LLM_*, EMBEDDING_*, SEARCH_*); see docs/PROVIDERS.md.
set -euo pipefail
DOMAIN="${1:?usage: generate-env.sh <domain>  (e.g. credit.example.com)}"
case "$DOMAIN" in http://*|https://*|*/*) echo "pass the bare host name, e.g. credit.example.com" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
[ -e "$ENV_FILE" ] && { echo "$ENV_FILE already exists; not overwriting (move it away first if you really mean to)." >&2; exit 1; }
gen() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }

umask 077
cat > "$ENV_FILE" <<EOF
# Intelli-Credit production settings — generated $(date -u +%Y-%m-%dT%H:%M:%SZ) by generate-env.sh.
# NEVER commit this file (it is git-ignored). Permissions: 600. Documentation: .env.example, docs/PROVIDERS.md.

# Every 'docker compose' command in this directory uses the production overlay (also scripts/backup.sh).
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml

# ---- Core (required)
PUBLIC_ORIGIN=https://${DOMAIN}
SECRET_KEY=$(gen 48)
ALLOW_REGISTRATION=false
# The frontend is published on loopback only; nginx terminates TLS in front of it.
IC_WEB_BIND=127.0.0.1
IC_WEB_PORT=3080

# ---- PostgreSQL (owner account: migrations only) and the RLS-restricted application role
POSTGRES_USER=intelli
POSTGRES_DB=intellicredit
POSTGRES_PASSWORD=$(gen 32)
DB_APP_ROLE=intellicredit_app
APP_DB_PASSWORD=$(gen 32)
# DATABASE_URL / MIGRATION_DATABASE_URL are assembled by docker-compose.prod.yml from the values above.

# ---- Optional demo seed (synthetic data only): docker compose run --rm seed
DEMO_PASSWORD=$(gen 18)

# ---- Optional providers (blank = built-in fallbacks; see docs/PROVIDERS.md)
LLM_PROVIDER=none
LLM_API_KEY=
LLM_MODEL=claude-opus-5-5
LLM_EFFORT=medium
EMBEDDING_PROVIDER=local-hash
EMBEDDING_MODEL=hash-384
EMBEDDING_API_KEY=
SEARCH_PROVIDER=none
SEARCH_API_KEY=
TAVILY_API_KEY=
BRAVE_API_KEY=
SERPAPI_API_KEY=
EOF
chmod 600 "$ENV_FILE"
echo "Wrote $ENV_FILE (mode 600) for https://${DOMAIN}. Secrets were generated and not displayed."
echo "Show the demo password only when you need it: grep ^DEMO_PASSWORD= $ENV_FILE"
