# AGENTS.md — trevor

Egress/airlock microservice for the KARECTL TRE. Controls import/export of research outputs across the security boundary.

---

## Commands

```bash
uv sync                                              # install deps
uv run trevor                                        # run app (:8000)
uv run pytest -v                                     # run tests (220, no external deps)
uv run ruff check . && uv run ruff format --check .  # lint + format check
uv run ruff format .                                 # auto-format
uv run alembic upgrade head                          # run migrations
uv run alembic revision --autogenerate -m "desc"     # generate migration
uv run arq trevor.worker.WorkerSettings              # run ARQ worker
uv run zensical serve                                # serve docs
```

No `make`, `just`, or `task` — `uv run` only.

---

## Fixed stack (C-13 — deviations require a new ADR)

| Layer | Technology |
|---|---|
| Backend | FastAPI + Pydantic v2 + SQLModel |
| Migrations | Alembic (async; `aiosqlite` locally, `asyncpg` prod) |
| Frontend | **Datastar** (hypermedia + SSE; no JS build step — not htmx, not React) |
| Templating | Jinja2 |
| Object storage | `aioboto3` (S3-compatible) |
| Task queue | ARQ (async Redis queue) |
| Auth | Keycloak OIDC (`python-jose`; `DEV_AUTH_BYPASS` for tests) |
| Orchestration | Kubernetes (Tilt + k3d for local dev) |
| Agent | Pydantic-AI (OpenAI-compatible backend) |
| RO-Crate | `rocrate` |
| File preview | `mistune`, `polars`, `pygments` |
| Notifications | In-app (`Notification` table, `InAppBackend`); SMTP (`SmtpBackend`, `aiosmtplib`) |
| Linting | `ruff` |
| Pre-commit | `prek` |

---

## Project layout

```
src/trevor/
  app.py            # FastAPI factory + lifespan (ARQ pool, SQLite table creation)
  settings.py       # pydantic-settings BaseSettings — all env vars
  database.py       # get_engine (lru_cache), get_session dep
  auth.py           # AuthContext, CurrentAuth, RequireAdmin, DEV_AUTH_BYPASS
  storage.py        # aioboto3 S3 — upload, download, presigned URLs
  worker.py         # ARQ: agent_review_job, release_job, send_notifications_job, url_expiry_warning_job, stuck_request_alert_job, crd_sync_job
  crd.py            # kubernetes client wrappers (list CRDs)
  agent/            # rules.py (statbarn, pure), agent.py (Pydantic-AI), prompts.py, schemas.py
  models/
    user.py         # User
    project.py      # Project, ProjectMembership, ProjectRole, ProjectStatus
    request.py      # AirlockRequest, OutputObject, OutputObjectMetadata, AuditEvent
    review.py       # Review, ReviewerType, ReviewDecision
    notification.py # Notification, NotificationEventType (8 types)
    release.py      # ReleaseRecord, DeliveryRecord
  schemas/          # Pydantic read/write schemas mirroring models
  services/
    user_service.py         # upsert_user
    membership_service.py   # CRUD + validate_no_role_conflict
    audit_service.py        # emit() — append-only AuditEvent
    release_service.py      # assemble_and_release(), RO-Crate + zip
    metrics_service.py      # admin dashboard queries
    notification_service.py # NotificationEvent, InAppBackend, SmtpBackend, NotificationRouter, create_event, get_router
    email_templates/        # 7 event dirs (subject.txt, body.html, body.txt) for SmtpBackend
    crd_sync_service.py     # reconcile_projects/users/memberships (pure, no k8s dep)
  routers/
    requests.py      # /requests — CRUD, submit, upload, replace, resubmit
    reviews.py       # /requests/{id}/reviews
    releases.py      # /requests/{id}/release
    deliveries.py    # /requests/{id}/deliver + /delivery (ingress)
    notifications.py # /notifications — list, unread-count, mark-read, mark-all-read
    admin.py         # /admin — requests, metrics, audit, audit/export
    sse.py           # /ui/sse — SSE live update streams (status badge, queue count, notification count)
    ui.py            # /ui — all Datastar HTML views
    users.py projects.py memberships.py auth_routes.py
  sse.py              # SSE helpers: format_fragment_event, sse_stream, sse_response
  templates/
    base.html         # shell + nav (bell badge) + Datastar CDN
    components/       # nav, flash, pagination, status_badge, file_preview
    researcher/       # request_list, _create, _detail, object_upload, _metadata, _replace, revision_feedback
    checker/          # review_queue, review_form
    admin/            # request_overview, metrics_dashboard, audit_log, membership_manage
    notifications/    # list.html
  static/style.css    # custom properties, status colours, notification styles
tests/
  conftest.py         # in-memory SQLite, client/admin_client fixtures, DEV_AUTH_BYPASS
  test_*.py           # 240 tests across 17 files
alembic/versions/     # async migrations
deploy/dev/
  crds/               # CRD schemas (Project, User, Group, KeycloakClient, VDI)
  sample-project/     # Interstellar Project CR + dev User/Group CRs
scripts/seed-dev-db.py  # seeds Interstellar project + dev memberships into postgres
helm/trevor/          # production Helm chart
Dockerfile            # multi-stage, non-root
Tiltfile              # local dev orchestration
docs/                 # architecture.md, api.md, ui.md, guide/, spec/
```

---

## Architecture patterns

- **App factory**: `create_app(settings)` in `app.py`. Lifespan opens ARQ pool; closes on shutdown.
- **Deps**: `get_session`, `get_settings`, `get_auth_context`. Tests override via `app.dependency_overrides`.
- **Auth**: `AuthContext` = `User` + `realm_roles` + `is_admin`. `tre_admin` read from JWT on every request — not cached.
- **User upsert**: every authed request calls `upsert_user()` from JWT claims. Users can also be pre-created by CRD sync (`keycloak_sub` nullable until first login).
- **Role conflict**: `validate_no_role_conflict()` — researcher and checker cannot share a project (C-04).
- **Audit**: `audit_service.emit()` — append-only, never UPDATE/DELETE (C-05).
- **Notifications**: `send_notifications_job` (ARQ) resolves recipients, builds `NotificationEvent`, dispatches via `NotificationRouter`. Fired by submit, agent_review_job, release_job.
- **CRD sync**: `crd_sync_job` ARQ cron every 5 min. Reads Project/User/Group CRDs → upserts DB rows. `display_name` = `spec.display_name` → `spec.description` → `metadata.name`. Checker roles are trevor-internal (not from CRDs).
- **Migrations**: Alembic autogenerate on SQLite often misses `import sqlmodel` and detects phantom `projects.status` enum changes — fix manually. Use `op.batch_alter_table()` for SQLite ALTER.

---

## Constraints (abbreviated — full text: `docs/spec/constraints.md`)

- **C-02**: Researchers never hold S3 credentials — trevor proxies all storage.
- **C-03**: `OutputObject` immutable after submission. SHA-256 verified at every transition.
- **C-04**: 2 distinct reviewers required. Submitter cannot review their own request.
- **C-05**: `AuditEvent` append-only — no UPDATE or DELETE ever.
- **C-06**: trevor never writes CRDs — read-only from CR8TOR.
- **C-07**: Kubernetes-only. No Docker Compose production path.
- **C-08**: Stateless app tier — all state in DB or Redis.
- **C-09**: No SACRO/ACRO library — own rule engine.
- **C-10**: Auth via Keycloak only. No local credential store.
- **C-11**: RO-Crate assembled only at `RELEASED` state.

---

## Domain model

**Entities** (all UUID PKs): `User`, `Project`, `ProjectMembership`, `AirlockRequest`, `OutputObject`, `OutputObjectMetadata`, `AuditEvent`, `Review`, `ReleaseRecord`, `DeliveryRecord`, `Notification`

**`AirlockRequest` states**:
`DRAFT → SUBMITTED → AGENT_REVIEW → HUMAN_REVIEW → CHANGES_REQUESTED / APPROVED → RELEASING → RELEASED` (or `REJECTED`)

**`OutputObject` states**: `PENDING → APPROVED / REJECTED / CHANGES_REQUESTED / SUPERSEDED`

**`ProjectMembership` roles**: `researcher`, `output_checker`, `senior_checker`

**S3 key**: `{project_id}/{request_id}/{object_id}/{version}/{uuid4}-{filename}`

**Agent actor**: `agent:trevor-agent` in `AuditEvent`

**Notification event types**: `request.submitted`, `agent_review.ready`, `request.changes_requested`, `request.approved`, `request.rejected`, `request.released`, `presigned_url.expiring_soon`, `request.stuck`

---

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./local/trevor.db` | `postgresql+asyncpg://...` in prod |
| `DEV_AUTH_BYPASS` | `false` | Skip JWT — set `true` for tests/local |
| `REDIS_URL` | `redis://localhost:6379/0` | ARQ queue |
| `SECRET_KEY` | — | CSRF + session signing; required in prod |
| `KEYCLOAK_URL` | — | Browser-facing; JWT issuer base |
| `KEYCLOAK_INTERNAL_URL` | — | In-cluster URL for server-side OIDC calls |
| `KEYCLOAK_REALM` | `karectl` | |
| `KEYCLOAK_CLIENT_ID` | `trevor` | |
| `S3_ENDPOINT_URL` | — | Empty = AWS; set for SeaweedFS/MinIO |
| `S3_ACCESS_KEY_ID` | — | |
| `S3_SECRET_ACCESS_KEY` | — | |
| `S3_QUARANTINE_BUCKET` | `trevor-quarantine` | |
| `S3_RELEASE_BUCKET` | `trevor-release` | |
| `AGENT_OPENAI_BASE_URL` | — | OpenAI-compatible LLM endpoint |
| `AGENT_MODEL_NAME` | — | |
| `AGENT_API_KEY` | — | |
| `AGENT_LLM_ENABLED` | `false` | |
| `NOTIFICATIONS_ENABLED` | `true` | Disable to skip ARQ dispatch |
| `EMAIL_NOTIFICATIONS_ENABLED` | `false` | Enable SmtpBackend |
| `SMTP_HOST` | `localhost` | karectl SMTP service |
| `SMTP_PORT` | `587` | |
| `SMTP_FROM_ADDRESS` | `trevor@karectl.example` | Envelope From |
| `SMTP_USE_TLS` | `true` | STARTTLS |
| `SMTP_USERNAME` | `""` | Optional SMTP auth |
| `SMTP_PASSWORD` | `""` | Optional SMTP auth |
| `TREVOR_BASE_URL` | `http://localhost:8000` | Used in email links |
| `CRD_NAMESPACE` | `trevor-dev` | |
| `CRD_SYNC_ENABLED` | `false` | `true` in Tiltfile |
| `STUCK_REQUEST_HOURS` | `72` | SLA threshold for stuck detection |
| `PRESIGNED_URL_TTL` | `604800` | Release URL TTL in seconds (7 days) |

---

## API endpoints

**JSON API**

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/health` | None | |
| `GET` | `/users/me` | Any | Returns memberships + realm roles |
| `GET` | `/projects` | Any | |
| `GET/POST/DELETE` | `/memberships/...` | Admin | Role conflict enforced on POST |
| `POST` | `/requests` | Researcher | |
| `GET` | `/requests` | Any | Filtered by project membership |
| `GET` | `/requests/{id}` | Member | |
| `POST` | `/requests/{id}/submit` | Owner | Enqueues agent_review_job + notification |
| `POST` | `/requests/{id}/resubmit` | Owner | |
| `POST/GET` | `/requests/{id}/objects` | Researcher/Member | Upload / list |
| `GET/PATCH` | `/requests/{id}/objects/{oid}/metadata` | Researcher/Member | |
| `POST` | `/requests/{id}/objects/{oid}/replace` | Researcher | |
| `GET` | `/requests/{id}/objects/{oid}/versions` | Member | |
| `GET` | `/requests/{id}/audit` | Member | |
| `POST/GET` | `/requests/{id}/reviews` | Checker/Member | |
| `GET` | `/requests/{id}/reviews/{rid}` | Member | |
| `POST/GET` | `/requests/{id}/release` | Admin/Member | POST triggers RO-Crate + release_job |
| `POST` | `/requests/{id}/objects/{oid}/upload-url` | Admin/Senior | Ingress pre-signed PUT |
| `POST` | `/requests/{id}/objects/{oid}/confirm-upload` | Admin/Senior | Ingress checksum confirm |
| `POST/GET` | `/requests/{id}/deliver` | Admin/Member | Ingress delivery |
| `GET` | `/admin/requests` | Admin/Senior | |
| `GET` | `/admin/metrics` | Admin/Senior | |
| `GET` | `/admin/audit` | Admin | |
| `GET` | `/admin/audit/export` | Admin | CSV |
| `GET` | `/notifications/unread-count` | Any | JSON or SSE signals (Datastar) |
| `GET` | `/notifications` | Any | `?limit`, `?before`, `?unread_only` |
| `PATCH` | `/notifications/{id}/read` | Any | |
| `POST` | `/notifications/mark-all-read` | Any | |

**UI (HTML — all under `/ui/`)**

Researcher: `/requests`, `/requests/new`, `/requests/{id}`, `/requests/{id}/upload`, `…/objects/{oid}/metadata`, `…/replace`, `/requests/{id}/submit`, `/requests/{id}/resubmit`

Checker: `/review`, `/review/{id}`

Admin: `/admin`, `/admin/metrics`, `/admin/audit`, `/admin/memberships/{pid}`, `/ingress/new`, `/requests/ingress`, `/requests/{id}/ingress-upload`, `…/objects/{oid}/generate-url`, `…/confirm`, `/requests/{id}/deliver`, `/requests/{id}/release`

All roles: `/notifications`, `/notifications/{id}/read`, `/notifications/mark-all-read`

**SSE streams (all under `/ui/sse/`)**

| Endpoint | Auth | Fragment ID |
|---|---|---|
| `GET /ui/sse/requests/{id}/status` | Member | `request-status-badge` |
| `GET /ui/sse/review/queue-count` | Checker/Admin | `review-queue-count` |
| `GET /ui/sse/notifications/count` | Any | `notification-count` |

---

## Local dev

Requires Tilt + k3d, SeaweedFS, Keycloak, Redis. Tests need nothing external.

```bash
uv sync && uv run pytest -v    # no external deps
tilt up                        # full stack + migrations + seed
```

`tilt up` applies CRDs, creates the **Interstellar** project, and seeds dev users via `seed-dev-db`. All user passwords: `password`.

| Username | Role | Project |
|---|---|---|
| `researcher-1` | researcher | Interstellar |
| `checker-1` | output_checker | Interstellar |
| `checker-2` | output_checker + senior_checker | Interstellar |
| `admin-user` | tre_admin (global) | — |

Keycloak admin: `admin` / `admin` at `http://localhost:8080`.

---

## Git workflow

- Review and if necessary update docs, AGENTS.md and README.md before committing to ensure they stay up to date with code changes.
- Commit after each discrete piece of work. Do not batch unrelated changes.
- Conventional Commits. Subject ≤50 chars; body only when "why" isn't obvious.
- Run before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest -v`
- Do not push unless explicitly asked.
