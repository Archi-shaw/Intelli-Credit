#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 VM for Intelli-Credit: Docker Engine + Compose v2 (official Docker apt repository),
# nginx, certbot, git, automatic security updates and Docker log rotation. Idempotent.
#
#   sudo deploy/compute-engine/bootstrap-vm.sh [deploy-user]     # deploy-user defaults to $SUDO_USER
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
DEPLOY_USER="${1:-${SUDO_USER:-}}"
. /etc/os-release
[ "${ID}-${VERSION_ID}" = "ubuntu-24.04" ] || echo "WARNING: written for Ubuntu 24.04, found ${PRETTY_NAME}" >&2

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git nginx certbot unattended-upgrades python3

echo "== Docker Engine + Compose plugin (download.docker.com)"
install -m 0755 -d /etc/apt/keyrings
if [ ! -s /etc/apt/keyrings/docker.asc ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Bounded container logs (the default json-file driver never rotates) — protects the 50 GB disk.
if [ ! -f /etc/docker/daemon.json ]; then
  cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "20m", "max-file": "5"}
}
EOF
fi
systemctl enable --now docker containerd
systemctl restart docker

if [ -n "$DEPLOY_USER" ] && id "$DEPLOY_USER" >/dev/null 2>&1; then
  usermod -aG docker "$DEPLOY_USER"
  echo "Added ${DEPLOY_USER} to the docker group (log out and back in to use docker without sudo)."
fi

echo "== nginx + ACME webroot"
install -d -m 0755 /var/www/certbot
rm -f /etc/nginx/sites-enabled/default
systemctl enable --now nginx
systemctl reload nginx

echo "== Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Backup directory"
install -d -m 0700 /var/backups/intellicredit
[ -n "$DEPLOY_USER" ] && chown "$DEPLOY_USER" /var/backups/intellicredit

docker --version
docker compose version
nginx -v
certbot --version
echo "Bootstrap complete."
