# AGENTS.md — trevor

Egress/airlock microservice for the KARECTL Trusted Research Environment (TRE). Manages controlled import/export of research outputs across the TRE security boundary.

**Status**: Iteration 0 skeleton — only FastAPI dep and a stub `main()` exist. All tooling (ruff, pre-commit, Alembic, tests, CI, Helm, Tiltfile) is planned but not yet configured.

---

## Toolchain

| Tool | Version |
|---|---|
| Python | 3.13 (pinned in `.python-version`) |
| Package manager | `uv` 0.11.2 |
| Build backend | `uv_build` |

All commands go through `uv`:

```bash
uv sync                          # install / sync deps into .venv
uv run trevor                    # run entry point (trevor:main in src/trevor/__init__.py)
uv add <package>                 # add a dependency
uv run ruff check .              # lint (not yet configured)
uv run ruff format .             # format (not yet configured)
uv run pytest                    # tests (not yet configured)
uv run alembic upgrade head      # migrations (not yet configured)
```

No `make`, `just`, `task`, or `.pre-commit-config.yaml` exist yet.

---

## Lockfile discrepancy

`pyproject.toml` depends on `fastapi>=0.125.0` and `spec/CONSTRAINTS.md` mandates **Pydantic v2**, but `uv.lock` currently resolves to **Pydantic 1.10.26**. The lock needs to be updated before implementing any models.

---

## Fixed technology stack (C-13 — deviations require a new ADR)

| Layer | Technology |
|---|---|
| Backend | FastAPI |
| Validation | Pydantic v2 |
| ORM | SQLModel |
| Migrations | Alembic |
| Async DB sessions | `AsyncSession` / `async_sessionmaker` (`aiosqlite` locally, `asyncpg` in prod) |
| Linting/formatting | `ruff` |
| Pre-commit | `prek` |
| Frontend | **Datastar** (hypermedia + SSE; no JS build step; ~14 KB script — not htmx, not React) |
| Templating | Jinja2 |
| Object storage client | `aioboto3` |
| Task queue | ARQ (Async Redis Queue) |
| Auth | Keycloak OIDC (`python-jose` or `authlib` for JWT) |
| Orchestration | Kubernetes (Tilt + k3d/kind for local dev) |
| RO-Crate | `rocrate` Python library |
| File preview | `mistune`, `polars`, `pygments` |
| Notifications | Jinja2 email templates (HTML + `.txt` pairs in `trevor/notifications/templates/`) |

---

## Project layout

```
src/trevor/__init__.py      # sole source file — stub main()
spec/                       # authoritative design docs (read before implementing)
  CONSTRAINTS.md            # non-negotiable constraints (C-01 – C-13)
  DOMAIN_MODEL.md           # entity definitions, state machines, field-level detail
  ITERATION_PLAN.md         # delivery plan; spec must be written before each iteration's code
  GLOSSARY.md
  adr/0001-*.md … 0011-*.md # ADRs live directly in spec/ (not in a subdir)
```

**Spec-first rule**: Each iteration requires writing OpenAPI paths and DB migration spec *before* implementation. Check `spec/ITERATION_PLAN.md` for what needs to be specced first.

---

## Non-negotiable constraints (abbreviated — read `spec/CONSTRAINTS.md` for full text)

- **C-02**: Researchers never hold S3 credentials. trevor is the sole storage proxy.
- **C-03**: `OutputObject` is immutable after submission. SHA-256 checksum verified at every state transition. No PUT/DELETE/PATCH on file content.
- **C-04**: Every request needs exactly 2 distinct reviewers before approval. Submitter cannot review. Researcher cannot check their own project.
- **C-05**: `AuditEvent` table is append-only. No UPDATE or DELETE ever — enforce via a DB role with INSERT-only permission.
- **C-06**: trevor never writes Kubernetes CRDs — project data is read-only from CR8TOR.
- **C-07**: Kubernetes-only. No Docker Compose production path.
- **C-08**: Application tier must be stateless (state in DB or Redis only).
- **C-09**: No SACRO/ACRO Python library. trevor implements its own rule engine.
- **C-10**: Auth exclusively via Keycloak. No local credential store.
- **C-11**: RO-Crate assembled only at `RELEASED` state, never as a draft.

---

## Domain model essentials

**Core entities** (all UUID PKs): `User`, `Project`, `ProjectMembership`, `AirlockRequest`, `OutputObject`, `OutputObjectMetadata`, `Review`, `AuditEvent`, `ReleaseRecord`, `Notification`.

**`AirlockRequest` states**:
`DRAFT → SUBMITTED → AGENT_REVIEW → HUMAN_REVIEW → CHANGES_REQUESTED / APPROVED → RELEASING → RELEASED` (or `REJECTED`)

**`OutputObject` states**: `PENDING → APPROVED / REJECTED / CHANGES_REQUESTED / SUPERSEDED`

**`ProjectMembership` roles**: `researcher`, `output_checker`, `senior_checker`

**Agent identity**: `agent:trevor-agent` (used as actor in `AuditEvent`)

**S3 key format**: `{project_id}/{request_id}/{logical_object_id}/{version}/{uuid4}-{filename}` — uniqueness by construction, no overwrite possible.

**`OutputObjectMetadata`** uses optimistic locking (`version` counter) for concurrent updates.

**Keycloak global admin role** (`tre_admin`) is read from JWT `realm_access.roles` on every request — not cached locally.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./trevor.db` locally; `postgresql+asyncpg://...` in prod |
| `DEV_AUTH_BYPASS` | Skip Keycloak — required for tests without a live Keycloak instance |
| `REDIS_URL` | ARQ queue; e.g. `redis://trevor-redis:6379/0` |

S3 credentials and Keycloak client secrets are injected via Kubernetes Secrets as env vars.

---

## Local dev prerequisites (not yet set up — Iteration 0 deliverable)

Full local dev stack requires: **Tilt + k3d/kind**, **MinIO** (local S3), **Keycloak** dev container, **Redis**. Unit tests can avoid all of these with `DEV_AUTH_BYPASS` and a SQLite URL.
