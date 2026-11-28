#!/usr/bin/env bash
# Configure nginx for Intelli-Credit and obtain a Let's Encrypt certificate (HTTP-01, webroot). Idempotent.
# The domain's A record must already point at this VM, and tcp/80 + tcp/443 must be open.
#
#   sudo deploy/compute-engine/setup-nginx.sh credit.example.com admin@example.com
#   sudo STAGING=1 deploy/compute-engine/setup-nginx.sh ...     # Let's Encrypt staging CA (untrusted test cert)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
DOMAIN="${1:?usage: setup-nginx.sh <domain> <email>}"
EMAIL="${2:?usage: setup-nginx.sh <domain> <email>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SITE=/etc/nginx/sites-available/intellicredit
render() { sed "s/__DOMAIN__/${DOMAIN}/g" "$1" > "$SITE"; ln -sf "$SITE" /etc/nginx/sites-enabled/intellicredit; nginx -t; systemctl reload nginx; }

install -d -m 0755 /var/www/certbot
if [ ! -s "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  echo "== Phase 1: HTTP-only site for the ACME challenge"
  render "$HERE/nginx/intellicredit-acme.conf.template"
  certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email \
    --non-interactive ${STAGING:+--test-cert}
fi

echo "== Phase 2: HTTPS site proxying to 127.0.0.1:3080"
render "$HERE/nginx/intellicredit.conf.template"

# Renewal runs from certbot's systemd timer; reload nginx after each renewal so it serves the new certificate.
install -d /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
certbot renew --dry-run --no-random-sleep-on-renew >/dev/null && echo "Certificate renewal dry run: OK"

echo "== Certificate"
openssl x509 -in "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" -noout -subject -issuer -enddate
echo "nginx is serving https://${DOMAIN}"
