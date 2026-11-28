#!/usr/bin/env bash
# Verify a Compute Engine deployment from OUTSIDE the VM (your machine): TLS certificate, HTTP->HTTPS redirect,
# security headers, closed internal ports, then the end-to-end workflow and the live security probe.
#
#   DOMAIN=credit.example.com DEMO_PASSWORD=... deploy/compute-engine/verify.sh
#   SKIP_WORKFLOW=1 DOMAIN=credit.example.com deploy/compute-engine/verify.sh      # network/TLS checks only
#
# The workflow and probe need the synthetic demo seed and create synthetic records. The probe ends with ~26 failed
# logins for a non-existent account, so logins from your IP are throttled for about a minute afterwards.
set -uo pipefail
: "${DOMAIN:?set DOMAIN}"
HOST="${CHECK_HOST:-$DOMAIN}"        # IP or name used for the port checks
HTTPS_PORT="${HTTPS_PORT:-443}"; HTTP_PORT="${HTTP_PORT:-80}"   # non-default only for local rehearsals
BASE="https://${DOMAIN}"; [ "$HTTPS_PORT" = 443 ] || BASE="https://${DOMAIN}:${HTTPS_PORT}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-$ROOT/backend/.venv/bin/python}"
fail=0
ok()   { echo "  ok    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }

echo "== TLS certificate for ${DOMAIN}"
if out="$(echo | openssl s_client -connect "${DOMAIN}:${HTTPS_PORT}" -servername "$DOMAIN" -verify_return_error 2>/dev/null)"; then
  echo "$out" | openssl x509 -noout -subject -issuer -enddate | sed 's/^/        /'
  ok "certificate chain verifies"
else
  bad "certificate does not verify"
fi

echo "== HTTP and headers"
code="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://${DOMAIN}:${HTTP_PORT}/login")"
case "$code" in 301\ https://*) ok "http redirects to https ($code)";; *) bad "http -> https redirect ($code)";; esac
hdrs="$(curl -sSI "${BASE}/login")" || bad "https request failed"
echo "$hdrs" | grep -qi '^strict-transport-security:' && ok "HSTS header" || bad "HSTS header missing"
echo "$hdrs" | grep -qi '^x-content-type-options: nosniff' && ok "nosniff header" || bad "nosniff header missing"
echo "$hdrs" | grep -qi '^server: nginx/' && bad "nginx version disclosed" || ok "server version not disclosed"
rcode="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/v1/ready")"
[ "$rcode" = 200 ] && ok "readiness 200" || bad "readiness returned $rcode"
reg="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' -H 'x-ic-client: web' \
  -d '{"email":"registration-probe@example.com","password":"Probe-password-2026","full_name":"Registration Probe","organization_name":"Registration Probe Org"}' "${BASE}/api/v1/auth/register")"
[ "$reg" = 403 ] || [ "$reg" = 404 ] && ok "self-registration disabled ($reg)" || bad "registration endpoint returned $reg"

echo "== Internal ports must be closed from the internet (${HOST})"
for port in 3000 3080 5432 5433 5435 6379 6380 8000 8001 8080; do
  if timeout 4 bash -c "exec 3<>/dev/tcp/${HOST}/${port}" 2>/dev/null; then bad "port ${port} is reachable"; else ok "port ${port} closed"; fi
done

if [ -z "${SKIP_WORKFLOW:-}" ]; then
  : "${DEMO_PASSWORD:?set DEMO_PASSWORD (the seeded demo password) or SKIP_WORKFLOW=1}"
  echo "== Session cookie flags"
  setc="$(curl -sS -o /dev/null -D - -H 'content-type: application/json' -H 'x-ic-client: web' \
    --data-binary @- "${BASE}/api/v1/auth/login" <<<"{\"email\":\"analyst@meridian.example\",\"password\":\"${DEMO_PASSWORD}\"}" \
    | grep -i '^set-cookie: ic_session=' | tr 'A-Z' 'a-z')"
  for flag in secure httponly 'samesite=lax'; do
    case "$setc" in *"; $flag"*) ok "session cookie: $flag";; *) bad "session cookie missing $flag";; esac
  done
  echo "== End-to-end workflow (backend/scripts/verify_stack.py)"
  (cd "$ROOT/backend" && DEMO_PASSWORD="$DEMO_PASSWORD" "$PY" scripts/verify_stack.py --base "$BASE") || fail=1
  # PROBE_ENV_FILE: a file holding only the secret lines of the server .env (e.g. SECRET_KEY, *_PASSWORD, *_API_KEY);
  # the probe fails if any of those values appears in an API response. Plain settings would cause false positives.
  echo "== Live security probe (backend/scripts/security_probe.py)"
  (cd "$ROOT/backend" && DEMO_PASSWORD="$DEMO_PASSWORD" "$PY" scripts/security_probe.py --base "$BASE" \
     --env-file "${PROBE_ENV_FILE:-/nonexistent}") || fail=1
fi

[ "$fail" = 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit "$fail"
