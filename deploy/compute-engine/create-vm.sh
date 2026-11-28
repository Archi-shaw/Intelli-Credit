#!/usr/bin/env bash
# Intelli-Credit on Google Compute Engine — creates the VM and its network. Run on YOUR machine (gcloud CLI).
#
# SAFE BY DEFAULT: DRY_RUN=1 prints every command without executing it. With DRY_RUN=0 it creates BILLED
# resources (VM, static IP, persistent disk, snapshots). Review docs/DEPLOYMENT_COMPUTE_ENGINE.md first.
#
#   PROJECT_ID=my-project deploy/compute-engine/create-vm.sh                    # dry run (prints the plan)
#   PROJECT_ID=my-project DRY_RUN=0 deploy/compute-engine/create-vm.sh          # create (idempotent)
#
# What it creates (nothing existing is modified or deleted):
#   - a dedicated VPC "${PREFIX}-net" + subnet, so the default network's broad rules (SSH/RDP from 0.0.0.0/0) never apply
#   - firewall: tcp/80,443 from anywhere; tcp/22 only from Google IAP (35.235.240.0/20) and, optionally, ADMIN_CIDR
#   - a regional static external IP
#   - an Ubuntu 24.04 LTS e2-standard-4 VM, 50 GB balanced persistent disk, Shielded VM, OS Login, deletion protection
#   - a daily snapshot schedule for the disk (SNAPSHOT_RETENTION_DAYS, default 7)
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"                 # Mumbai
ZONE="${ZONE:-${REGION}-a}"
PREFIX="${PREFIX:-ic}"
VM="${VM_NAME:-${PREFIX}-vm}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-4}"   # 4 vCPU, 16 GB
DISK_SIZE="${DISK_SIZE:-50GB}"
DISK_TYPE="${DISK_TYPE:-pd-balanced}"           # or pd-ssd
NETWORK="${PREFIX}-net"; SUBNET="${PREFIX}-subnet"; SUBNET_RANGE="${SUBNET_RANGE:-10.20.0.0/24}"
ADDRESS="${PREFIX}-ip"; TAG="${PREFIX}-web"
ADMIN_CIDR="${ADMIN_CIDR:-}"                    # e.g. 203.0.113.7/32 for direct SSH; blank = IAP only
SNAPSHOT_RETENTION_DAYS="${SNAPSHOT_RETENTION_DAYS:-7}"
DRY_RUN="${DRY_RUN:-1}"

run() { echo "+ $*"; if [ "$DRY_RUN" = "0" ]; then "$@"; fi; }
exists() { [ "$DRY_RUN" = "0" ] && "$@" >/dev/null 2>&1; }

echo "== Intelli-Credit Compute Engine (project=${PROJECT_ID} zone=${ZONE} vm=${VM} dry_run=${DRY_RUN})"
if [ "$DRY_RUN" = "0" ]; then
  gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . || { echo "gcloud is not authenticated (gcloud auth login)" >&2; exit 1; }
  [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null)" = "True" ] \
    || { echo "Billing is not enabled for ${PROJECT_ID} (or you lack billing.resourceAssociations.list)" >&2; exit 1; }
fi
run gcloud config set project "$PROJECT_ID"
run gcloud services enable compute.googleapis.com iap.googleapis.com

echo "== Network (dedicated VPC; no default-network rules apply)"
exists gcloud compute networks describe "$NETWORK" || run gcloud compute networks create "$NETWORK" --subnet-mode=custom
exists gcloud compute networks subnets describe "$SUBNET" --region="$REGION" || \
  run gcloud compute networks subnets create "$SUBNET" --network="$NETWORK" --region="$REGION" --range="$SUBNET_RANGE"

echo "== Firewall: public HTTP/HTTPS only; SSH through IAP (and ADMIN_CIDR if set)"
exists gcloud compute firewall-rules describe "${PREFIX}-allow-web" || \
  run gcloud compute firewall-rules create "${PREFIX}-allow-web" --network="$NETWORK" --direction=INGRESS \
    --action=ALLOW --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags="$TAG"
exists gcloud compute firewall-rules describe "${PREFIX}-allow-ssh-iap" || \
  run gcloud compute firewall-rules create "${PREFIX}-allow-ssh-iap" --network="$NETWORK" --direction=INGRESS \
    --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags="$TAG"
if [ -n "$ADMIN_CIDR" ]; then
  exists gcloud compute firewall-rules describe "${PREFIX}-allow-ssh-admin" || \
    run gcloud compute firewall-rules create "${PREFIX}-allow-ssh-admin" --network="$NETWORK" --direction=INGRESS \
      --action=ALLOW --rules=tcp:22 --source-ranges="$ADMIN_CIDR" --target-tags="$TAG"
fi

echo "== Static external IP"
exists gcloud compute addresses describe "$ADDRESS" --region="$REGION" || \
  run gcloud compute addresses create "$ADDRESS" --region="$REGION" --network-tier=PREMIUM

echo "== VM: Ubuntu 24.04 LTS, ${MACHINE_TYPE}, ${DISK_SIZE} ${DISK_TYPE}"
exists gcloud compute instances describe "$VM" --zone="$ZONE" || \
  run gcloud compute instances create "$VM" --zone="$ZONE" --machine-type="$MACHINE_TYPE" \
    --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
    --boot-disk-size="$DISK_SIZE" --boot-disk-type="$DISK_TYPE" --boot-disk-auto-delete \
    --network="$NETWORK" --subnet="$SUBNET" --address="$ADDRESS" --tags="$TAG" \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata=enable-oslogin=TRUE --scopes=logging-write,monitoring-write \
    --deletion-protection

echo "== Daily disk snapshots (${SNAPSHOT_RETENTION_DAYS} days)"
exists gcloud compute resource-policies describe "${PREFIX}-daily-snapshots" --region="$REGION" || \
  run gcloud compute resource-policies create snapshot-schedule "${PREFIX}-daily-snapshots" --region="$REGION" \
    --daily-schedule --start-time=21:00 --max-retention-days="$SNAPSHOT_RETENTION_DAYS" \
    --on-source-disk-delete=keep-auto-snapshots
run gcloud compute disks add-resource-policies "$VM" --zone="$ZONE" --resource-policies="${PREFIX}-daily-snapshots"

IP="$( [ "$DRY_RUN" = "0" ] && gcloud compute addresses describe "$ADDRESS" --region="$REGION" --format='value(address)' || echo '<static-ip>')"
cat <<EOF

== Done. External IP: ${IP}
Next:
  1. Point an A record for your domain at ${IP} (and wait until 'dig +short <domain>' returns it).
  2. SSH in:   gcloud compute ssh ${VM} --zone=${ZONE} --tunnel-through-iap
  3. On the VM: follow docs/DEPLOYMENT_COMPUTE_ENGINE.md (bootstrap-vm.sh, generate-env.sh, deploy.sh, setup-nginx.sh).
EOF
