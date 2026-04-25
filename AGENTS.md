# AGENTS.md — trevor

Egress/airlock microservice for the KARECTL Trusted Research Environment (TRE). Manages controlled import/export of research outputs across the TRE security boundary.

---

## Toolchain

| Tool | Version |
|---|---|
| Python | 3.13 (pinned in `.python-version`) |
| Package manager | `uv` 0.11.2 |
| Build backend | `uv_build` |
| Linting/formatting | `ruff` (config in `pyproject.toml`) |
| Pre-commit | `prek` (config in `pyproject.toml [tool.prek]`) |
| Tests | `pytest` + `pytest-asyncio` (async mode auto) |

All commands go through `uv`:

```bash
uv sync                          # install / sync deps into .venv
uv run trevor                    # run app (uvicorn on :8000)
uv run pytest -v                 # run tests
uv run ruff check .              # lint
uv run ruff format .             # format
uv run alembic upgrade head      # run migrations
uv run alembic revision --autogenerate -m "description"  # generate migration
uv run arq trevor.worker.WorkerSettings  # run ARQ worker
```

No `make`, `just`, or `task` — `uv run` only.

---

## Fixed technology stack (C-13 — deviations require a new ADR)

| Layer | Technology |
|---|---|
| Backend | FastAPI |
| Validation | Pydantic v2 |
| ORM | SQLModel |
| Migrations | Alembic (async template, `aiosqlite` locally, `asyncpg` in prod) |
| Linting/formatting | `ruff` |
| Pre-commit | `prek` |
| Frontend | **Datastar** (hypermedia + SSE; no JS build step — not htmx, not React) |
| Templating | Jinja2 |
| Object storage client | `aioboto3` |
| Task queue | ARQ (Async Redis Queue) |
| Auth | Keycloak OIDC (`python-jose` for JWT; `DEV_AUTH_BYPASS` for tests) |
| Orchestration | Kubernetes (Tilt + k3d/kind for local dev) |
| Agent framework | Pydantic-AI (OpenAI-compatible backend) |
| RO-Crate | `rocrate` Python library |
| File preview | `mistune`, `polars`, `pygments` |
| Notifications | Jinja2 email templates (planned) |

---

## Project layout

```
src/trevor/
  __init__.py              # main() entrypoint → uvicorn
  app.py                   # FastAPI factory, lifespan, router registration
  settings.py              # pydantic-settings BaseSettings (all env vars)
  database.py              # async engine (lru_cache by URL), session factory, get_session dep
  auth.py                  # AuthContext dep, DEV_AUTH_BYPASS, require_admin
  storage.py               # aioboto3 S3 abstraction (upload, download, presigned URLs)
  worker.py                # ARQ WorkerSettings, agent_review_job, cron stubs
  agent/
    __init__.py
    rules.py               # statbarn rule engine (pure functions, no I/O)
    agent.py               # Pydantic-AI agent orchestration + LLM narrative
    prompts.py             # system prompt, template-based narratives
    schemas.py             # RuleResult, ObjectAssessment dataclasses
  models/
    user.py                # User (synced from CRD + Keycloak; nullable keycloak_sub)
    project.py             # Project, ProjectMembership, ProjectStatus, ProjectRole
    request.py             # AirlockRequest, OutputObject, OutputObjectMetadata, AuditEvent
    review.py              # Review, ReviewerType, ReviewDecision
  schemas/
    user.py                # UserRead, UserMeRead
    project.py             # ProjectRead
    membership.py          # MembershipCreate, MembershipRead
    request.py             # RequestCreate/Read, OutputObjectRead, MetadataRead, AuditEventRead
    review.py              # ReviewRead
    release.py             # ReleaseRecordRead
  services/
    user_service.py        # upsert_user (create/update from CRD sync or JWT claims)
    membership_service.py  # CRUD + role conflict validation
    audit_service.py       # emit() helper for AuditEvent
    release_service.py     # assemble_and_release(), RO-Crate assembly, zip building
    metrics_service.py     # admin dashboard queries, pipeline metrics
  routers/
    users.py               # GET /users/me
    projects.py            # GET /projects, GET /projects/{id}
    memberships.py         # POST /memberships (admin), GET, DELETE
    requests.py            # CRUD + submit + upload for AirlockRequest/OutputObject
    reviews.py             # GET /requests/{id}/reviews
    releases.py            # POST/GET /requests/{id}/release
    admin.py               # GET /admin/requests, /metrics, /audit, /audit/export
    ui.py                  # Datastar HTML views: researcher, checker, admin
  templates/
    base.html              # Shell: head, nav, Datastar CDN, flash area
    components/            # nav, flash, pagination, status_badge, file_preview
    researcher/            # request_list, request_create, request_detail, object_upload, object_metadata, object_replace, revision_feedback
    checker/               # review_queue, review_form
    admin/                 # request_overview, metrics_dashboard, audit_log, membership_manage
  static/
    style.css              # Minimal CSS (system fonts, custom properties, status colors)
tests/
  conftest.py              # fixtures: in-memory SQLite, client, admin_client, sample data
  test_health.py
  test_users.py
  test_projects.py
  test_memberships.py
  test_requests.py
  test_rules.py            # statbarn rule engine unit tests
  test_reviews.py          # agent review job + review endpoint tests
  test_releases.py         # release endpoint + RO-Crate tests
  test_admin.py            # admin dashboard + metrics endpoint tests
  test_ui.py               # Datastar UI route tests
alembic/                   # async Alembic config, migrations
helm/trevor/               # Helm chart skeleton
.github/workflows/ci.yml   # lint → test → docker build
Dockerfile                 # multi-stage, non-root user
Tiltfile                   # k3d/kind local dev
spec/                      # authoritative design docs (read before implementing)
  CONSTRAINTS.md           # non-negotiable constraints (C-01 – C-13)
  DOMAIN_MODEL.md          # entity definitions, state machines, field-level detail
  ITERATION_PLAN.md        # delivery plan; spec before code per iteration
  GLOSSARY.md
  0001-*.md … 0012-*.md    # ADRs
```

**Spec-first rule**: each iteration requires writing OpenAPI paths and DB migration spec *before* implementation. Check `spec/ITERATION_PLAN.md` for what to spec next.

---

## Architecture patterns

- **App factory**: `create_app(settings)` in `app.py`. Module-level `app = create_app()` for uvicorn.
- **Dependency injection**: `get_session`, `get_settings`, `get_auth_context` are FastAPI deps. Tests override via `app.dependency_overrides`.
- **Engine caching**: `get_engine(url)` is `@lru_cache` — one engine per URL. Tests use `sqlite+aiosqlite:///:memory:`.
- **Auth**: `AuthContext` dataclass holds `User` (DB model) + `realm_roles` + `is_admin`. `CurrentAuth` type alias for Depends injection. `RequireAdmin` chains admin check.
- **User upsert**: on every authed request, `upsert_user()` creates/updates User shadow record from Keycloak claims. Users may also be pre-created from CRD sync with nullable `keycloak_sub`. Keycloak is source of truth for identity (C-10).
- **Role conflict enforcement**: `validate_no_role_conflict()` prevents researcher + checker on same project (C-04). Checked before every membership create.

---

## Non-negotiable constraints (abbreviated — read `spec/CONSTRAINTS.md` for full text)

- **C-02**: Researchers never hold S3 credentials. trevor is the sole storage proxy.
- **C-03**: `OutputObject` is immutable after submission. SHA-256 checksum verified at every state transition. No PUT/DELETE/PATCH on file content.
- **C-04**: Every request needs exactly 2 distinct reviewers before approval. Submitter cannot review. Researcher cannot check their own project.
- **C-05**: `AuditEvent` table is append-only. No UPDATE or DELETE ever.
- **C-06**: trevor never writes Kubernetes CRDs — project data is read-only from CR8TOR.
- **C-07**: Kubernetes-only. No Docker Compose production path.
- **C-08**: Application tier must be stateless (state in DB or Redis only).
- **C-09**: No SACRO/ACRO Python library. trevor implements its own rule engine.
- **C-10**: Auth exclusively via Keycloak. No local credential store.
- **C-11**: RO-Crate assembled only at `RELEASED` state, never as a draft.

---

## Domain model essentials

**Implemented entities**: `User`, `Project`, `ProjectMembership`, `AirlockRequest`, `OutputObject`, `OutputObjectMetadata`, `AuditEvent`, `Review`, `ReleaseRecord` (all UUID PKs).

**Planned entities**: `Notification`.

**`AirlockRequest` states**:
`DRAFT → SUBMITTED → AGENT_REVIEW → HUMAN_REVIEW → CHANGES_REQUESTED / APPROVED → RELEASING → RELEASED` (or `REJECTED`)

**`OutputObject` states**: `PENDING → APPROVED / REJECTED / CHANGES_REQUESTED / SUPERSEDED`

**`ProjectMembership` roles**: `researcher`, `output_checker`, `senior_checker`

**Agent identity**: `agent:trevor-agent` (used as actor in `AuditEvent`)

**S3 key format**: `{project_id}/{request_id}/{logical_object_id}/{version}/{uuid4}-{filename}`

**Keycloak global admin role** (`tre_admin`) is read from JWT `realm_access.roles` on every request — not cached locally.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./local/trevor.db` locally; `postgresql+asyncpg://...` in prod |
| `DEV_AUTH_BYPASS` | Skip Keycloak JWT validation — for tests and local dev |
| `REDIS_URL` | ARQ queue; e.g. `redis://trevor-redis:6379/0` |
| `KEYCLOAK_URL` | Keycloak base URL |
| `KEYCLOAK_REALM` | Realm name (default: `karectl`) |
| `KEYCLOAK_CLIENT_ID` | OIDC client ID (default: `trevor`) |
| `S3_ENDPOINT_URL` | MinIO URL for local dev; empty for AWS |
| `S3_ACCESS_KEY_ID` | S3 credentials |
| `S3_SECRET_ACCESS_KEY` | S3 credentials |
| `S3_QUARANTINE_BUCKET` | Upload quarantine bucket (default: `trevor-quarantine`) |
| `S3_RELEASE_BUCKET` | Release bucket (default: `trevor-release`) |

S3 credentials and Keycloak client secrets are injected via Kubernetes Secrets in prod.

Agent settings (planned):

| Variable | Purpose |
|---|---|
| `AGENT_OPENAI_BASE_URL` | OpenAI-compatible LLM endpoint |
| `AGENT_MODEL_NAME` | Model to use for agent (default: configurable) |
| `AGENT_API_KEY` | API key for LLM backend |
| `AGENT_LLM_ENABLED` | Enable/disable agent LLM calls (default: `false`) |

---

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Liveness/readiness probe |
| `GET` | `/users/me` | Any | Current user + memberships + realm roles |
| `GET` | `/projects` | Any | List all projects |
| `GET` | `/projects/{id}` | Any | Get project by ID |
| `GET` | `/memberships/project/{id}` | Any | List memberships for project |
| `POST` | `/memberships` | `tre_admin` | Create membership (role conflict validated) |
| `DELETE` | `/memberships/{id}` | `tre_admin` | Remove membership |
| `POST` | `/requests` | Researcher | Create airlock request |
| `GET` | `/requests` | Any | List requests (filtered by membership) |
| `GET` | `/requests/{id}` | Member/Admin | Get request with objects |
| `POST` | `/requests/{id}/submit` | Owner/Admin | Submit request → enqueue agent review |
| `POST` | `/requests/{id}/objects` | Researcher | Upload output object |
| `GET` | `/requests/{id}/objects` | Member/Admin | List objects |
| `GET` | `/requests/{id}/objects/{oid}` | Member/Admin | Get object |
| `PATCH` | `/requests/{id}/objects/{oid}/metadata` | Researcher | Update metadata |
| `GET` | `/requests/{id}/objects/{oid}/metadata` | Member/Admin | Get metadata |
| `GET` | `/requests/{id}/audit` | Member/Admin | List audit events |
| `GET` | `/requests/{id}/reviews` | Member/Admin | List reviews |
| `GET` | `/requests/{id}/reviews/{rid}` | Member/Admin | Get single review |
| `POST` | `/requests/{id}/reviews` | Checker/Admin | Submit human review |
| `POST` | `/requests/{id}/objects/{oid}/replace` | Researcher | Upload replacement object |
| `POST` | `/requests/{id}/resubmit` | Owner/Admin | Resubmit after changes |
| `GET` | `/requests/{id}/objects/{oid}/versions` | Member/Admin | List object version history |
| `POST` | `/requests/{id}/release` | `tre_admin` | Trigger release (RO-Crate assembly) |
| `GET` | `/requests/{id}/release` | Member/Admin | Get release record |
| `GET` | `/admin/requests` | Admin/Senior | All-projects request overview |
| `GET` | `/admin/metrics` | Admin/Senior | Pipeline metrics + stuck detection |
| `GET` | `/admin/audit` | `tre_admin` | Filterable audit log |
| `GET` | `/admin/audit/export` | `tre_admin` | Export audit log as CSV |
| `GET` | `/ui/requests` | Any | Researcher request list (HTML) |
| `GET` | `/ui/requests/new` | Any | Create request form (HTML) |
| `POST` | `/ui/requests` | Researcher | Create request via form |
| `GET` | `/ui/requests/{id}` | Member/Admin | Request detail (HTML) |
| `GET/POST` | `/ui/requests/{id}/upload` | Researcher | Upload object form + handler |
| `GET/POST` | `/ui/requests/{id}/objects/{oid}/metadata` | Researcher | Metadata form + handler |
| `GET/POST` | `/ui/requests/{id}/objects/{oid}/replace` | Researcher | Replace form + handler |
| `POST` | `/ui/requests/{id}/submit` | Owner/Admin | Submit via UI |
| `POST` | `/ui/requests/{id}/resubmit` | Owner/Admin | Resubmit via UI |
| `POST` | `/ui/requests/{id}/release` | `tre_admin` | Release via UI |
| `GET` | `/ui/review` | Checker/Admin | Review queue (HTML) |
| `GET/POST` | `/ui/review/{id}` | Checker/Admin | Review form + submit |
| `GET` | `/ui/admin` | `tre_admin` | Admin request overview (HTML) |
| `GET` | `/ui/admin/metrics` | `tre_admin` | Metrics dashboard (HTML) |
| `GET` | `/ui/admin/audit` | `tre_admin` | Audit log (HTML) |
| `GET` | `/ui/admin/memberships/{pid}` | `tre_admin` | Membership management (HTML) |
| `POST` | `/ui/admin/memberships` | `tre_admin` | Create membership via UI |
| `POST` | `/ui/admin/memberships/{mid}/delete` | `tre_admin` | Delete membership via UI |

---

## Local dev

Full local dev stack requires: **Tilt + k3d/kind**, **MinIO** (local S3), **Keycloak** dev container, **Redis**. Unit tests avoid all of these with `DEV_AUTH_BYPASS=true` and in-memory SQLite.

```bash
uv sync && uv run pytest -v    # quick check — no external deps needed
```

---

## Git workflow

- Commit after each discrete piece of work (new model, new router, bug fix, config change). Do not batch unrelated changes.
- Terse commit messages. Conventional Commits format. Subject ≤50 chars, body only when "why" isn't obvious from the diff.
- Run `uv run ruff check . && uv run ruff format --check . && uv run pytest -v` before every commit.
- Do not push unless explicitly asked.
