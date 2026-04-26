---
icon: lucide/code
---

# Developer Guide

## Prerequisites

- Python 3.13 (pinned in `.python-version`)
- [`uv`](https://docs.astral.sh/uv/) 0.11+

No other tools needed for running tests. Full local dev stack additionally needs: Docker, k3d 5.7+, kubectl, Helm 3.14+, Tilt 0.33+.

## Quick start

```bash
uv sync                          # install / sync deps
uv run pytest -v                 # run all tests
uv run trevor                    # run app (uvicorn on :8000)
uv run ruff check .              # lint
uv run ruff format .             # format
uv run zensical serve            # serve docs locally
```

All commands go through `uv run`. No `make`, `just`, or `task`.

## Local dev stack (Tilt + k3d)

Brings up PostgreSQL, Redis, SeaweedFS (S3), and Keycloak inside a local k3d cluster with live-reload.

### Devcontainer (recommended)

Open the repo in VS Code and choose **Reopen in Container**. The post-create script:

1. Installs uv, k3d, Tilt, and prek hooks
2. Runs `uv sync`
3. Copies `sample.env` → `.env` (if `.env` doesn't already exist)
4. Creates the k3d cluster `trevor-dev` with a local image registry

Then start the dev stack:

```bash
tilt up
```

Tilt brings up all services, runs DB migrations, and seeds the database with test users and project memberships automatically via the `seed-dev-db` resource.

### Bare-metal

```bash
# One-time cluster setup (requires Docker, k3d, Tilt, Helm, uv)
./scripts/dev-setup.sh

# Start dev stack
tilt up

# Teardown
./scripts/dev-teardown.sh
```

### Port forwards

Active while Tilt is running:

| Port | Service | Credentials |
|---|---|---|
| 8000 | trevor API | — |
| 8080 | Keycloak admin console | `admin` / `admin` |
| 8333 | SeaweedFS S3 gateway | `devaccess` / `devsecret` |
| 5432 | PostgreSQL | `trevor` / `trevor` |
| 6379 | Redis | — |

### Test users

All test users have password `password` and are created automatically in Keycloak from `deploy/dev/keycloak-realm.yaml` on first Keycloak startup.

| Username | Role | Notes |
|---|---|---|
| `researcher-1` | researcher | Member of `lancs-tre-proj-1` |
| `checker-1` | output_checker | Member of `lancs-tre-proj-1` |
| `checker-2` | output_checker + senior_checker | Member of `lancs-tre-proj-1` |
| `admin-user` | tre_admin | Global admin via Keycloak realm role |

These users are seeded into postgres by the `seed-dev-db` Tilt resource, which runs automatically after trevor and Keycloak are healthy. You can also run it manually:

```bash
uv run python scripts/seed-dev-db.py
```

### Environment variables

`post-create.sh` copies `sample.env` → `.env` on devcontainer creation. The `.env` file is gitignored — edit it for local overrides without affecting other developers.

Key variables and their defaults for the Tilt stack:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://trevor:trevor@localhost:5432/trevor` | Port-forwarded from k3d |
| `DEV_AUTH_BYPASS` | `false` | Real Keycloak OIDC login required |
| `KEYCLOAK_URL` | `http://localhost:8080` | Browser-facing; also JWT issuer base |
| `KEYCLOAK_INTERNAL_URL` | _(empty)_ | In-cluster URL for server-side OIDC calls — set to `http://keycloak:8080` in Tiltfile |
| `KEYCLOAK_ADMIN_USERNAME` | `admin` | Used by `seed-dev-db.py` to query Keycloak admin API |
| `KEYCLOAK_ADMIN_PASSWORD` | `admin` | Must match `KC_BOOTSTRAP_ADMIN_PASSWORD` in `deploy/dev/keycloak.yaml` |
| `S3_ENDPOINT_URL` | `http://localhost:8333` | SeaweedFS S3 gateway |
| `S3_ACCESS_KEY_ID` | `devaccess` | |
| `S3_SECRET_ACCESS_KEY` | `devsecret` | |

### CR8TOR sample project

Tilt automatically applies KARECTL CRD definitions and a sample project (`lancs-tre-proj-1`) with one user (`hardingmp`) and group memberships. These live in:

- `deploy/dev/crds/` — CustomResourceDefinition models (Project, User, Group, KeycloakClient, VDIInstance)
- `deploy/dev/sample-project/` — CR instances for the sample project

```bash
kubectl get projects,users,groups -n trevor-dev
```

### Run Alembic migrations against the Tilt PostgreSQL

```bash
# With port-forward active (handled automatically by Tilt):
DATABASE_URL=postgresql+asyncpg://trevor:trevor@localhost:5432/trevor uv run alembic upgrade head
```

### SQLite-only (no Kubernetes)

For fast iteration without any infrastructure:

```bash
uv sync
# Set DEV_AUTH_BYPASS=true and a SQLite DATABASE_URL in .env, then:
uv run trevor          # API on :8000
uv run pytest -v       # tests (in-memory SQLite, no .env needed)
```

## Testing

Tests use in-memory SQLite with `DEV_AUTH_BYPASS=true`. No external services needed.

```bash
uv run pytest -v                 # full suite (164 tests)
uv run pytest tests/test_ui.py   # just UI tests
uv run pytest -k "test_rules"    # just rule engine tests
```

### Test fixtures

Defined in `tests/conftest.py`:

- `client` — async HTTP client as regular user (`dev-bypass-user`)
- `admin_client` — async HTTP client as admin (`dev-bypass-admin`)
- `db_session` — direct async SQLModel session
- `researcher_setup` — creates user + project + researcher membership, returns `(client, project_id)`
- `sample_user`, `sample_project`, `sample_membership` — pre-created DB records

### Writing tests

```python
@pytest.mark.anyio
async def test_something(client: AsyncClient) -> None:
    r = await client.get("/some/endpoint")
    assert r.status_code == 200
```

## Database migrations

```bash
uv run alembic upgrade head                          # run migrations
uv run alembic revision --autogenerate -m "desc"     # generate migration
```

!!! warning "SQLite gotchas"

    - SQLite doesn't support `ALTER COLUMN`. Use `op.batch_alter_table()` in migrations.
    - Alembic autogenerate always misses `import sqlmodel` — add manually.
    - Alembic may detect phantom `projects.status` type changes (VARCHAR→Enum) — remove manually.

## Git workflow

- Commit after each discrete piece of work
- Conventional Commits format, subject ≤50 chars
- Run before every commit:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -v
```

## Documentation

Documentation lives in `docs/` and is built with [zensical](https://zensical.org) (Material for MkDocs alternative).

```bash
uv run zensical serve    # preview at http://localhost:8000
uv run zensical build    # build static site
```

!!! info "Document after each iteration"

    Every iteration must include documentation updates. Update the relevant docs pages and AGENTS.md as part of the iteration deliverables.

## Adding a new endpoint

1. Add the model/migration if needed (`models/`, `alembic/`)
2. Add Pydantic schemas (`schemas/`)
3. Add the router function (`routers/`)
4. Add audit events via `audit_service.emit()`
5. Add tests
6. Add UI template + route if applicable
7. Update `docs/api.md`
8. Run lint + format + tests
