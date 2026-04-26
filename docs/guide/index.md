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

Open the repo in VS Code and choose **Reopen in Container**. The post-create script installs all tools, creates the k3d cluster, and runs `uv sync` automatically.

```bash
tilt up   # start dev stack (run inside container)
```

### Bare-metal

```bash
# One-time cluster setup (requires Docker, k3d, Tilt, Helm, uv)
./scripts/dev-setup.sh

# Start dev stack
tilt up

# Teardown
./scripts/dev-teardown.sh
```

Port forwards active while Tilt is running:

| Port | Service |
|---|---|
| 8000 | trevor API |
| 8080 | Keycloak admin console (`admin` / `admin`) |
| 8333 | SeaweedFS S3 gateway (`devaccess` / `devsecret`) |
| 5432 | PostgreSQL (`trevor` / `trevor`) |
| 6379 | Redis |

### Run Alembic migrations against the Tilt PostgreSQL

```bash
# With port-forward active:
DATABASE_URL=postgresql+asyncpg://trevor:trevor@localhost:5432/trevor uv run alembic upgrade head
```

### SQLite-only (no Kubernetes)

For fast iteration without any infrastructure:

```bash
uv sync
# .env has DEV_AUTH_BYPASS=true and SQLite DATABASE_URL
uv run trevor          # API on :8000
uv run pytest -v       # tests (in-memory SQLite)
```

## Testing

Tests use in-memory SQLite with `DEV_AUTH_BYPASS=true`. No external services needed.

```bash
uv run pytest -v                 # full suite (111 tests)
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
