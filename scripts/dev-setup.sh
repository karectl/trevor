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

# Create k3d cluster with local registry (skip if already exists).
# Optional: set K3D_LB_HTTP_PORT to publish ingress HTTP from loadbalancer.
if k3d cluster list | grep -q '^trevor-dev\b'; then
  echo "k3d cluster 'trevor-dev' already exists — reusing."
  k3d kubeconfig merge trevor-dev --kubeconfig-merge-default
else
  k3d_args=(
    cluster create trevor-dev
    --registry-create trevor-registry:0.0.0.0:5005
    --agents 1
    --wait
  )

  if [[ -n "${K3D_LB_HTTP_PORT:-}" ]]; then
    k3d_args+=(--port "${K3D_LB_HTTP_PORT}:80@loadbalancer")
    echo "Using k3d loadbalancer HTTP port: ${K3D_LB_HTTP_PORT}"
  fi

  k3d "${k3d_args[@]}"

  # Restart nodes so containerd reloads the registry mirror config that k3d injected.
  echo "Restarting k3s nodes to apply registry config..."
  docker restart k3d-trevor-dev-server-0 k3d-trevor-dev-agent-0
  k3d kubeconfig merge trevor-dev --kubeconfig-merge-default
fi

# Wait for API server and nodes to be Ready before proceeding.
echo "Waiting for cluster to be ready..."
for i in $(seq 1 60); do
  if kubectl get nodes 2>/dev/null | grep -q " Ready"; then
    not_ready=$(kubectl get nodes --no-headers 2>/dev/null | grep -cv " Ready" || true)
    if [[ "$not_ready" -eq 0 ]]; then break; fi
  fi
  sleep 3
done
echo "Cluster ready."

# Create namespace
kubectl create namespace trevor-dev --dry-run=client -o yaml | kubectl apply -f -

echo "Dev cluster ready. Run: tilt up"
