# Iteration 10 Spec — Local Development Environment

## Goal

A single-command local dev stack that starts trevor and all its dependencies in Kubernetes (k3d/kind) via Tilt. Provide both a devcontainer setup (VS Code / Codespaces / remote) and a bare-metal setup for developers who prefer native tooling.

---

## Scope decisions

| Item | Decision |
|---|---|
| Container orchestration | k3d (default) or kind; Tilt for live-reload |
| Object storage | SeaweedFS (ADR-0013, replaces MinIO) |
| Database | PostgreSQL 16 (bitnami Helm sub-chart) |
| Cache / queue | Redis 7 (bitnami Helm sub-chart) |
| Auth | Keycloak 25+ (quay.io/keycloak/keycloak) |
| Devcontainer | VS Code devcontainer with Docker-outside-of-Docker |
| Bare-metal | uv + k3d + tilt + kubectl + helm installed natively |

---

## Prerequisites

### Common (both setups)

| Tool | Version | Purpose |
|---|---|---|
| Docker | 24+ | Container runtime |
| k3d | 5.7+ | Lightweight k3s-in-Docker |
| kubectl | 1.28+ | Kubernetes CLI |
| Helm | 3.14+ | Chart management |
| Tilt | 0.33+ | Dev loop orchestrator |

### Bare-metal additional

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.13 (from `.python-version`) | Runtime |
| uv | 0.11.2+ | Package manager |

### Devcontainer

All tools pre-installed in the container image. Developer only needs Docker and VS Code with the Dev Containers extension (or a Codespaces-compatible environment).

---

## 1. Devcontainer specification

### File: `.devcontainer/devcontainer.json`

```json
{
  "name": "trevor",
  "image": "mcr.microsoft.com/devcontainers/python:3.13",
  "features": {
    "ghcr.io/devcontainers/features/docker-outside-of-docker:1": {},
    "ghcr.io/devcontainers/features/kubectl-helm-minikube:1": {
      "minikube": "none",
      "helm": "latest",
      "kubectl": "latest"
    }
  },
  "postCreateCommand": ".devcontainer/post-create.sh",
  "forwardPorts": [8000, 8080, 8333, 5432, 6379],
  "customizations": {
    "vscode": {
      "extensions": [
        "charliermarsh.ruff",
        "ms-python.python",
        "redhat.vscode-yaml",
        "tilt-dev.tiltfile"
      ],
      "settings": {
        "python.defaultInterpreterPath": ".venv/bin/python"
      }
    }
  }
}
```

### File: `.devcontainer/post-create.sh`

```bash
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

echo "✔ Dev environment ready. Run: tilt up"
```

### Design decisions

- **Docker-outside-of-Docker** (`docker-outside-of-docker` feature) so the devcontainer shares the host Docker daemon. k3d runs k3s inside Docker containers on the host, accessible from the devcontainer.
- **No Docker-in-Docker** — avoids nested virtualization overhead and storage driver issues.
- Port forwarding: 8000 (trevor), 8080 (Keycloak), 8333 (SeaweedFS S3 gateway), 5432 (PostgreSQL), 6379 (Redis).

---

## 2. Bare-metal setup

### Script: `scripts/dev-setup.sh`

```bash
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
```

### Script: `scripts/dev-teardown.sh`

```bash
#!/usr/bin/env bash
k3d cluster delete trevor-dev
```

---

## 3. Kubernetes dev manifests

All dev-only manifests live in `deploy/dev/`. Tilt applies them.

### `deploy/dev/seaweedfs.yaml`

Single-node SeaweedFS deployment with S3 gateway:

```yaml
# Master + Volume + Filer + S3 Gateway in one pod (dev only)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: seaweedfs
  namespace: trevor-dev
spec:
  replicas: 1
  selector:
    matchLabels:
      app: seaweedfs
  template:
    metadata:
      labels:
        app: seaweedfs
    spec:
      containers:
        - name: seaweedfs
          image: chrislusf/seaweedfs:latest
          args:
            - "server"
            - "-master"
            - "-volume"
            - "-filer"
            - "-s3"
            - "-s3.port=8333"
            - "-s3.config=/etc/seaweedfs/s3.json"
          ports:
            - containerPort: 9333  # master
            - containerPort: 8080  # volume
            - containerPort: 8888  # filer
            - containerPort: 8333  # s3 gateway
          volumeMounts:
            - name: s3-config
              mountPath: /etc/seaweedfs
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
      volumes:
        - name: s3-config
          configMap:
            name: seaweedfs-s3-config
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: seaweedfs-s3-config
  namespace: trevor-dev
data:
  s3.json: |
    {
      "identities": [
        {
          "name": "dev",
          "credentials": [
            {
              "accessKey": "devaccess",
              "secretKey": "devsecret"
            }
          ],
          "actions": ["Admin", "Read", "Write", "List", "Tagging"]
        }
      ]
    }
---
apiVersion: v1
kind: Service
metadata:
  name: seaweedfs
  namespace: trevor-dev
spec:
  selector:
    app: seaweedfs
  ports:
    - name: s3
      port: 8333
      targetPort: 8333
    - name: master
      port: 9333
      targetPort: 9333
    - name: filer
      port: 8888
      targetPort: 8888
```

### `deploy/dev/seaweedfs-buckets-job.yaml`

Init job to create quarantine and release buckets:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: seaweedfs-create-buckets
  namespace: trevor-dev
spec:
  backoffLimit: 5
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: create-buckets
          image: amazon/aws-cli:2.15.0
          env:
            - name: AWS_ACCESS_KEY_ID
              value: "devaccess"
            - name: AWS_SECRET_ACCESS_KEY
              value: "devsecret"
            - name: AWS_DEFAULT_REGION
              value: "us-east-1"
          command:
            - sh
            - -c
            - |
              ENDPOINT="http://seaweedfs:8333"
              # Wait for S3 gateway
              until aws s3api list-buckets --endpoint-url "$ENDPOINT" 2>/dev/null; do
                echo "Waiting for SeaweedFS S3..."
                sleep 2
              done
              aws s3 mb "s3://trevor-quarantine" --endpoint-url "$ENDPOINT" || true
              aws s3 mb "s3://trevor-release" --endpoint-url "$ENDPOINT" || true
              echo "Buckets created."
```

### `deploy/dev/keycloak.yaml`

Dev-mode Keycloak with the `karectl` realm:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: keycloak
  namespace: trevor-dev
spec:
  replicas: 1
  selector:
    matchLabels:
      app: keycloak
  template:
    metadata:
      labels:
        app: keycloak
    spec:
      containers:
        - name: keycloak
          image: quay.io/keycloak/keycloak:25.0
          args: ["start-dev", "--import-realm"]
          env:
            - name: KC_BOOTSTRAP_ADMIN_USERNAME
              value: admin
            - name: KC_BOOTSTRAP_ADMIN_PASSWORD
              value: admin
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: realm-config
              mountPath: /opt/keycloak/data/import
          resources:
            requests:
              cpu: "200m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
      volumes:
        - name: realm-config
          configMap:
            name: keycloak-realm
---
apiVersion: v1
kind: Service
metadata:
  name: keycloak
  namespace: trevor-dev
spec:
  selector:
    app: keycloak
  ports:
    - port: 8080
      targetPort: 8080
```

### `deploy/dev/keycloak-realm.yaml`

ConfigMap with a pre-configured `karectl` realm JSON (trevor client, test users, realm roles):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: keycloak-realm
  namespace: trevor-dev
data:
  karectl-realm.json: |
    {
      "realm": "karectl",
      "enabled": true,
      "clients": [
        {
          "clientId": "trevor",
          "enabled": true,
          "publicClient": true,
          "redirectUris": ["http://localhost:8000/*"],
          "webOrigins": ["http://localhost:8000"],
          "directAccessGrantsEnabled": true
        }
      ],
      "roles": {
        "realm": [
          { "name": "tre_admin", "description": "TRE administrator" },
          { "name": "researcher", "description": "Researcher" },
          { "name": "output_checker", "description": "Output checker" },
          { "name": "senior_checker", "description": "Senior checker" }
        ]
      },
      "users": [
        {
          "username": "admin-user",
          "email": "admin@test.local",
          "firstName": "Admin",
          "lastName": "User",
          "enabled": true,
          "credentials": [{ "type": "password", "value": "password", "temporary": false }],
          "realmRoles": ["tre_admin"]
        },
        {
          "username": "researcher-1",
          "email": "researcher1@test.local",
          "firstName": "Alice",
          "lastName": "Researcher",
          "enabled": true,
          "credentials": [{ "type": "password", "value": "password", "temporary": false }],
          "realmRoles": ["researcher"]
        },
        {
          "username": "checker-1",
          "email": "checker1@test.local",
          "firstName": "Bob",
          "lastName": "Checker",
          "enabled": true,
          "credentials": [{ "type": "password", "value": "password", "temporary": false }],
          "realmRoles": ["output_checker"]
        },
        {
          "username": "checker-2",
          "email": "checker2@test.local",
          "firstName": "Carol",
          "lastName": "Senior",
          "enabled": true,
          "credentials": [{ "type": "password", "value": "password", "temporary": false }],
          "realmRoles": ["output_checker", "senior_checker"]
        }
      ]
    }
```

### `deploy/dev/redis.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: trevor-dev
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports:
            - containerPort: 6379
          resources:
            requests:
              cpu: "50m"
              memory: "64Mi"
            limits:
              cpu: "200m"
              memory: "128Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: trevor-dev
spec:
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
```

### `deploy/dev/postgres.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: trevor-dev
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - name: POSTGRES_DB
              value: trevor
            - name: POSTGRES_USER
              value: trevor
            - name: POSTGRES_PASSWORD
              value: trevor
          ports:
            - containerPort: 5432
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "256Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: trevor-dev
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
```

---

## 4. Tiltfile update

Replace the current placeholder Tiltfile with a complete version that:

1. Builds the trevor image with live_update (sync `src/`).
2. Applies all `deploy/dev/*.yaml` manifests.
3. Deploys the trevor Helm chart with dev overrides.
4. Configures port forwards for all services.
5. Defines resource dependencies (trevor waits for postgres, redis, seaweedfs).

### New Tiltfile structure

```python
# Tiltfile — trevor local dev on k3d/kind

REGISTRY = "localhost:5005"
IMAGE_NAME = REGISTRY + "/trevor"
NAMESPACE = "trevor-dev"

# ── Docker image ─────────────────────────────────────────────────────────────
docker_build(
    IMAGE_NAME,
    ".",
    dockerfile="Dockerfile",
    live_update=[
        sync("src/", "/app/src/"),
    ],
)

# ── Dev infrastructure ────────────────────────────────────────────────────────
k8s_yaml("deploy/dev/postgres.yaml")
k8s_yaml("deploy/dev/redis.yaml")
k8s_yaml("deploy/dev/seaweedfs.yaml")
k8s_yaml("deploy/dev/seaweedfs-buckets-job.yaml")
k8s_yaml("deploy/dev/keycloak-realm.yaml")
k8s_yaml("deploy/dev/keycloak.yaml")

# ── Helm release (trevor app + worker) ────────────────────────────────────────
k8s_yaml(
    helm(
        "helm/trevor",
        name="trevor",
        namespace=NAMESPACE,
        set=[
            "image.repository=" + IMAGE_NAME,
            "image.tag=latest",
            "replicaCount=1",
            "worker.replicaCount=1",
            "env.DEV_AUTH_BYPASS=false",
            "env.LOG_LEVEL=DEBUG",
            "env.LOG_FORMAT=console",
            "env.DATABASE_URL=postgresql+asyncpg://trevor:trevor@postgres:5432/trevor",
            "env.REDIS_URL=redis://redis:6379/0",
            "env.KEYCLOAK_URL=http://keycloak:8080",
            "env.KEYCLOAK_REALM=karectl",
            "env.KEYCLOAK_CLIENT_ID=trevor",
            "env.S3_ENDPOINT_URL=http://seaweedfs:8333",
            "env.S3_ACCESS_KEY_ID=devaccess",
            "env.S3_SECRET_ACCESS_KEY=devsecret",
            "env.S3_QUARANTINE_BUCKET=trevor-quarantine",
            "env.S3_RELEASE_BUCKET=trevor-release",
            "env.SECRET_KEY=tilt-dev-secret-key",
        ],
    )
)

# ── Resources & port forwards ────────────────────────────────────────────────
k8s_resource("trevor-trevor", port_forwards=["8000:8000"], labels=["app"],
             resource_deps=["postgres", "redis", "seaweedfs"])
k8s_resource("postgres", port_forwards=["5432:5432"], labels=["infra"])
k8s_resource("redis", port_forwards=["6379:6379"], labels=["infra"])
k8s_resource("seaweedfs", port_forwards=["8333:8333", "9333:9333"], labels=["infra"])
k8s_resource("keycloak", port_forwards=["8080:8080"], labels=["infra"])
```

### `tilt-values.yaml`

Not needed — all overrides are passed via `set=[]` in the Tiltfile. Eliminates a stale-values problem.

---

## 5. Environment variables for Tilt dev

When Tilt is running, trevor connects to real infrastructure inside k3d:

| Variable | Value in Tilt |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://trevor:trevor@postgres:5432/trevor` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `KEYCLOAK_URL` | `http://keycloak:8080` |
| `KEYCLOAK_REALM` | `karectl` |
| `KEYCLOAK_CLIENT_ID` | `trevor` |
| `S3_ENDPOINT_URL` | `http://seaweedfs:8333` |
| `S3_ACCESS_KEY_ID` | `devaccess` |
| `S3_SECRET_ACCESS_KEY` | `devsecret` |
| `DEV_AUTH_BYPASS` | `false` (real Keycloak in Tilt) |
| `LOG_LEVEL` | `DEBUG` |
| `LOG_FORMAT` | `console` |

For running trevor outside the cluster (bare `uv run trevor`), use `.env` with `DEV_AUTH_BYPASS=true` and SQLite. The two modes are intentionally distinct.

---

## 6. Database migration in dev

The Tilt setup requires Alembic migrations against PostgreSQL. Options:

### Option A: Init container (recommended)

Add an init container to the trevor Deployment that runs `alembic upgrade head` before the main container starts. Requires updating `deployment.yaml`.

### Option B: Manual

Developer runs `kubectl exec` into the trevor pod and runs migrations. Documented in the developer guide.

### Decision

Option A (init container) for Tilt. The `deployment.yaml` template gets an optional `initContainers` section gated by `.Values.migrations.enabled`.

---

## 7. Quick-start instructions

### Devcontainer

```bash
# Open in VS Code → "Reopen in Container"
# post-create.sh runs automatically, then:
tilt up
```

### Bare-metal

```bash
# One-time setup
./scripts/dev-setup.sh

# Start dev stack
tilt up

# Run tests (separate terminal, uses SQLite + DEV_AUTH_BYPASS)
uv run pytest -v

# Teardown cluster
./scripts/dev-teardown.sh
```

### SQLite-only (no Kubernetes)

For fast iteration without infrastructure:

```bash
uv sync
# .env has DEV_AUTH_BYPASS=true, SQLite URL
uv run trevor
# In another terminal:
uv run pytest -v
```

---

## 8. Alembic migration against PostgreSQL

Developers must be able to run Alembic against the Tilt PostgreSQL:

```bash
# Port-forward is active (5432), so from host:
DATABASE_URL=postgresql+asyncpg://trevor:trevor@localhost:5432/trevor uv run alembic upgrade head
```

Or via kubectl exec:

```bash
kubectl exec -n trevor-dev deploy/trevor-trevor -- uv run alembic upgrade head
```

---

## New / modified files

```
.devcontainer/
  devcontainer.json          # NEW — devcontainer config
  post-create.sh             # NEW — setup script
scripts/
  dev-setup.sh               # NEW — bare-metal k3d setup
  dev-teardown.sh            # NEW — cluster deletion
deploy/dev/
  seaweedfs.yaml             # NEW — SeaweedFS single-node + S3 config
  seaweedfs-buckets-job.yaml # NEW — bucket creation job
  keycloak.yaml              # NEW — Keycloak dev deployment
  keycloak-realm.yaml        # NEW — karectl realm config
  redis.yaml                 # NEW — Redis deployment
  postgres.yaml              # NEW — PostgreSQL deployment
Tiltfile                     # MODIFIED — complete rewrite
sample.env                   # MODIFIED — update S3 comments for SeaweedFS
docs/guide/index.md          # MODIFIED — add dev setup instructions
```

---

## Test plan

1. `scripts/dev-setup.sh` creates k3d cluster without error.
2. `tilt up` brings all resources to Running within 3 minutes.
3. `curl http://localhost:8000/health` returns `{"status": "ok"}`.
4. Keycloak admin console accessible at `http://localhost:8080` (admin/admin).
5. SeaweedFS S3 gateway responds at `http://localhost:8333`.
6. PostgreSQL accepts connections at `localhost:5432`.
7. Full Alembic migration runs against PostgreSQL.
8. OIDC login flow works (Keycloak → trevor) with test users.
9. File upload stores object in SeaweedFS quarantine bucket.
10. Devcontainer builds and post-create completes within 5 minutes.
11. `scripts/dev-teardown.sh` deletes cluster cleanly.
