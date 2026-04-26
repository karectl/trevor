# ADR-0008 — Kubernetes Deployment Architecture

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

trevor runs exclusively on Kubernetes (C-07). It must support horizontal scaling (C-08) and integrate with the karectl cluster's existing patterns.

---

## Decision

### Components

trevor is deployed as the following Kubernetes resources:

```
trevor/
├── Deployment: trevor-web          — FastAPI application (horizontally scalable)
├── Deployment: trevor-worker       — Background task worker (ARQ)
├── CronJob: trevor-crd-sync        — Syncs Project CRDs into trevor DB
├── CronJob: trevor-storage-cleanup — Runs quarantine/release retention policy
├── Service: trevor-web             — ClusterIP + optional Ingress
├── ConfigMap: trevor-config        — Non-secret configuration
├── Secret: trevor-secrets          — DB URL, S3 credentials, Keycloak client secret
└── (optional) PersistentVolumeClaim — SQLite volume for dev only
```

### Task queue: ARQ

Background tasks (agent review, RO-Crate assembly, notification dispatch, storage copy) are handled by **ARQ** (Async Redis Queue), chosen because:
- Native async Python — consistent with FastAPI's async model
- Lightweight — no Celery worker infrastructure overhead
- Redis is typically already available in karectl cluster

If Redis is not available, a fallback to FastAPI `BackgroundTasks` is acceptable for low-volume deployments (dev/staging), but is not suitable for production (tasks are lost on pod restart).

```yaml
# ARQ worker deployment
trevor-worker:
  replicas: 2
  env:
    - REDIS_URL: redis://trevor-redis:6379/0
```

### Helm chart structure

```
charts/trevor/
├── Chart.yaml
├── values.yaml          — default values, all overridable
├── values.dev.yaml      — local dev overrides (SQLite, mock SMTP)
├── templates/
│   ├── deployment-web.yaml
│   ├── deployment-worker.yaml
│   ├── cronjob-crd-sync.yaml
│   ├── cronjob-storage-cleanup.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── configmap.yaml
│   ├── secret.yaml
│   ├── hpa.yaml         — HorizontalPodAutoscaler
│   └── serviceaccount.yaml
```

### Horizontal scaling

The `trevor-web` deployment is stateless (C-08) and scales via `HorizontalPodAutoscaler` based on CPU/memory or custom metrics. The following are required for statelessness:
- No in-process session state — sessions are cookie-based (signed, server-verified)
- No in-process job state — all async work goes through ARQ/Redis
- File uploads stream directly to S3 — no local disk writes

### CRD sync

A `CronJob` runs every 60 seconds (configurable) to reconcile karectl project CRDs into trevor's `Project` table. It uses the Kubernetes Python client with the in-cluster service account config.

The service account requires `get` and `list` permissions on the CR8TOR CRD group:

```yaml
# RBAC
rules:
  - apiGroups: ["cr8tor.karectl.io"]
    resources: ["projects", "workspaces"]
    verbs: ["get", "list", "watch"]
```

### Local development

Local development uses **Tilt** with a `Tiltfile` that:
- Builds the trevor Docker image on file change
- Applies Helm chart with `values.dev.yaml`
- Provides port-forwards for the web service, ARQ dashboard, and Keycloak
- Uses a local MinIO instance for S3 and k3d/kind for Kubernetes

---

## Consequences

- **Positive**: Fully cloud-native from day one. No non-k8s deployment path to maintain.
- **Positive**: ARQ integrates cleanly with FastAPI's async model.
- **Positive**: Helm chart makes deployment configuration explicit and version-controlled.
- **Negative**: Local development requires a running Kubernetes environment (k3d/kind). Mitigation: Tiltfile automates setup; documented in CONTRIBUTING.md.
- **Negative**: ARQ requires Redis. Mitigation: Redis is a standard dependency in karectl; included in dev Helm values.
