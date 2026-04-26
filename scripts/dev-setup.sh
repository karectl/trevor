#!/usr/bin/env bash
set -euo pipefail

# Check prerequisites
command -v docker >/dev/null || { echo "Docker required"; exit 1; }
command -v k3d >/dev/null || { echo "k3d required"; exit 1; }
command -v tilt >/dev/null || { echo "Tilt required"; exit 1; }
command -v helm >/dev/null || { echo "Helm required"; exit 1; }
command -v uv >/dev/null || { echo "uv required"; exit 1; }

# Python deps
uv sync

# Create k3d cluster with local registry
k3d cluster create trevor-dev \
  --registry-create trevor-registry:0.0.0.0:5005 \
  --port "8000:80@loadbalancer" \
  --agents 1 \
  --wait

# Create namespace
kubectl create namespace trevor-dev --dry-run=client -o yaml | kubectl apply -f -

echo "Dev cluster ready. Run: tilt up"
