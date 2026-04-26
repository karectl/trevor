# trevor

Egress/airlock microservice for the [KARECTL](https://github.com/karectl) Trusted Research Environment (TRE). Manages controlled import and export of research outputs across the TRE security boundary.

## What it does

Researchers submit output objects (files, tables, figures) for disclosure review before they can leave the TRE. trevor enforces the full review pipeline:

1. Researcher creates an airlock request and uploads output files
2. An automated rule engine (statbarn) and optional LLM agent perform initial screening
3. Two independent output checkers review the request
4. An admin approves and triggers release — trevor assembles an RO-Crate and copies the outputs to the release bucket

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Pydantic v2 + SQLModel |
| Database | PostgreSQL (async via asyncpg) / SQLite (dev) |
| Migrations | Alembic (async) |
| Task queue | ARQ (Redis) |
| Object storage | SeaweedFS (dev) / AWS S3 (prod) |
| Auth | Keycloak OIDC |
| Frontend | Datastar (hypermedia + SSE, no JS build step) |
| Agent | Pydantic-AI (OpenAI-compatible backend) |
| Packaging | RO-Crate (`rocrate`) |
| Observability | structlog, Prometheus `/metrics`, OpenTelemetry |
| Orchestration | Kubernetes (Tilt + k3d for local dev) |

## Quick start

```bash
uv sync
uv run pytest -v        # 252 tests, no external services needed
uv run trevor           # API on http://localhost:8000
```

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

## Local dev stack (Tilt + k3d)

Starts PostgreSQL, Redis, SeaweedFS, and Keycloak in a local k3d cluster with live-reload.

### Devcontainer (VS Code)

Open in VS Code → **Reopen in Container**. The post-create script installs all tools and creates the cluster automatically.

```bash
tilt up
```

`tilt up` will:
- Deploy all services (PostgreSQL, Redis, SeaweedFS, Keycloak, trevor)
- Apply CR8TOR CRDs and sample project resources (`deploy/dev/sample-project/`)
- Run DB migrations automatically via an init container
- Seed the database with test users and the **Interstellar** project via the `seed-dev-db` local resource

### Bare-metal

```bash
# One-time setup (requires Docker, k3d, Tilt, Helm, kubectl, uv)
./scripts/dev-setup.sh

tilt up

# Teardown
./scripts/dev-teardown.sh
```

Port forwards while Tilt is running:

| Port | Service | Credentials |
|---|---|---|
| 8000 | trevor API | — |
| 8080 | Keycloak (`admin` / `admin`) | — |
| 8333 | SeaweedFS S3 | `devaccess` / `devsecret` |
| 5432 | PostgreSQL | `trevor` / `trevor` |
| 6379 | Redis | — |

### Dev users

All passwords: `password`. Seeded automatically on `tilt up`.

| Username | Role | Project |
|---|---|---|
| `researcher-1` | researcher | Interstellar |
| `checker-1` | output_checker | Interstellar |
| `checker-2` | output_checker + senior_checker | Interstellar |
| `admin-user` | tre_admin (global) | — |

The **Interstellar** project is created from `deploy/dev/sample-project/project-interstellar.yaml` and seeded into postgres by the `seed-dev-db` Tilt resource. To re-run the seed manually:

```bash
uv run python scripts/seed-dev-db.py
```

## SQLite-only mode

For fast iteration without Kubernetes — uses `DEV_AUTH_BYPASS=true` and an in-memory/file SQLite database:

```bash
cp sample.env .env   # already configured for SQLite + DEV_AUTH_BYPASS
uv run trevor        # API on :8000, no auth required
uv run pytest -v     # full test suite
```

## Environment variables

Key variables (full list in `sample.env`):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite (`sqlite+aiosqlite:///...`) or PostgreSQL (`postgresql+asyncpg://...`) |
| `DEV_AUTH_BYPASS` | Skip Keycloak JWT validation (`true` for local dev/tests) |
| `REDIS_URL` | ARQ queue URL |
| `KEYCLOAK_URL` | Keycloak base URL |
| `S3_ENDPOINT_URL` | SeaweedFS or MinIO URL (empty = AWS S3) |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | S3 credentials |
| `SECRET_KEY` | CSRF / session secret — must be strong in production |
| `NOTIFICATIONS_ENABLED` | Enable in-app notification system (`true` by default) |
| `EMAIL_NOTIFICATIONS_ENABLED` | Enable SMTP email notifications (`false` by default) |
| `SMTP_HOST` / `SMTP_PORT` | SMTP server address and port (`localhost:587`) |
| `TREVOR_BASE_URL` | Base URL used in email links (`http://localhost:8000`) |
| `AGENT_LLM_ENABLED` | Enable LLM-based agent review (`false` by default) |

## Helm chart

```bash
helm install trevor helm/trevor \
  --namespace trevor \
  --create-namespace \
  --set image.repository=your-registry/trevor \
  --set image.tag=latest
```

Secrets (`DATABASE_URL`, `REDIS_URL`, S3 creds, `SECRET_KEY`) should be injected via Kubernetes Secrets referenced in `values.yaml` under `envFromSecrets`. See `helm/trevor/values.yaml` for full configuration reference.

## Running migrations

```bash
# Against SQLite (local)
uv run alembic upgrade head

# Against Tilt PostgreSQL (port-forward active)
DATABASE_URL=postgresql+asyncpg://trevor:trevor@localhost:5432/trevor uv run alembic upgrade head
```

## Docs

```bash
uv run zensical serve   # preview at http://localhost:8000
```

Documentation covers architecture, API reference, UI guide, and developer guide under `docs/`.

## Constraints

Non-negotiable constraints are documented in `docs/spec/constraints.md`. Key ones:

- **C-02**: Researchers never hold S3 credentials — trevor is the sole storage proxy
- **C-04**: Every request requires exactly 2 distinct reviewers; submitter cannot review their own
- **C-05**: Audit log is append-only — no UPDATE or DELETE on `AuditEvent`
- **C-07**: Kubernetes-only — no Docker Compose production path
- **C-09**: No SACRO/ACRO library — trevor implements its own rule engine
