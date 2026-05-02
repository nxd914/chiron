#!/usr/bin/env bash
# Provision a GCE e2-micro VM and deploy the Kinzie daemon.
# Run this once from your local machine after `gcloud auth login`.
#
# Usage:
#   chmod +x deploy/gce_setup.sh
#   ./deploy/gce_setup.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - .env file at repo root with KALSHI_API_KEY, KALSHI_PRIVATE_KEY_PATH, etc.
#   - kalshi_private.pem at path specified in KALSHI_PRIVATE_KEY_PATH

set -euo pipefail

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project)}"
ZONE="${GCP_ZONE:-us-central1-a}"
VM_NAME="kinzie-daemon"
MACHINE_TYPE="e2-small"  # e2-micro works but tight on RAM; e2-small ~$14/mo
DISK_SIZE="20GB"
REPO_DIR="/opt/kinzie"

echo "==> Provisioning VM: $VM_NAME in $PROJECT / $ZONE"

gcloud compute instances create "$VM_NAME" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --boot-disk-size="$DISK_SIZE" \
  --boot-disk-type="pd-standard" \
  --image-family="debian-12" \
  --image-project="debian-cloud" \
  --tags="kinzie-daemon" \
  --metadata=startup-script='#!/bin/bash
    apt-get update -y
    apt-get install -y docker.io docker-compose-v2 git
    systemctl enable docker
    systemctl start docker
  '

echo "==> Waiting 30s for VM to boot..."
sleep 30

echo "==> Uploading source code..."
gcloud compute scp --recurse \
  --zone="$ZONE" \
  --project="$PROJECT" \
  . "${VM_NAME}:${REPO_DIR}" \
  --compress

echo "==> Uploading .env and private key..."
gcloud compute scp \
  --zone="$ZONE" \
  --project="$PROJECT" \
  .env "${VM_NAME}:${REPO_DIR}/.env"

# Upload private key (reads path from .env)
KEYPATH=$(grep KALSHI_PRIVATE_KEY_PATH .env | cut -d= -f2 | tr -d '"' | tr -d "'")
if [[ -f "$KEYPATH" ]]; then
  gcloud compute scp \
    --zone="$ZONE" \
    --project="$PROJECT" \
    "$KEYPATH" "${VM_NAME}:${REPO_DIR}/kalshi_private.pem"
fi

echo "==> Installing systemd service..."
gcloud compute ssh "${VM_NAME}" --zone="$ZONE" --project="$PROJECT" --command="
  sudo cp ${REPO_DIR}/deploy/kinzie.service /etc/systemd/system/kinzie.service
  sudo systemctl daemon-reload
  sudo systemctl enable kinzie
  sudo systemctl start kinzie
  echo 'Service status:'
  sudo systemctl status kinzie --no-pager
"

echo ""
echo "==> Done. Monitor with:"
echo "    gcloud compute ssh $VM_NAME --zone=$ZONE -- sudo journalctl -fu kinzie"
echo ""
echo "==> To check health:"
echo "    gcloud compute ssh $VM_NAME --zone=$ZONE -- sudo docker compose -f ${REPO_DIR}/deploy/docker-compose.yml exec daemon python3 -m research.health_check"
