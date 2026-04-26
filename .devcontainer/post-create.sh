#!/usr/bin/env bash
set -euo pipefail

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Install k3d and tilt
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh | bash

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

echo "Dev environment ready. Run: tilt up"
