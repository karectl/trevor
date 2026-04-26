# trevor — Iteration Plan

Spec-driven, iterative delivery. Each iteration produces working, tested code and a refined spec layer. Spec artefacts (API schema, data model updates) are written *before* implementation within each iteration.

---

## Iteration 0 — Project skeleton & local dev

**Goal**: A running trevor with no features, but with the full scaffolding in place.

Deliverables:
- `uv`-managed Python project, `ruff` config, `prek` hooks
- FastAPI app with health endpoint
- SQLModel + Alembic setup (SQLite locally, PostgreSQL path documented)
- Keycloak dev container + OIDC login flow (or `DEV_AUTH_BYPASS`)
- MinIO dev container + S3 client abstraction (`aioboto3`)
- ARQ worker + Redis setup
- Tilt / k3d local dev environment
- Helm chart skeleton
- Docker image + GitHub Actions CI (lint, test, build)
- `spec/` directory with all initial ADRs (this document set)

---

## Iteration 1 — Project sync & user model

**Goal**: trevor knows about projects and users.

Spec to write first:
- OpenAPI paths: `GET /projects`, `GET /projects/{id}`, `GET /users/me`
- DB migration: `Project`, `User`, `ProjectMembership` tables

Deliverables:
- CRD sync CronJob reading CR8TOR project CRDs
- User shadow record created/updated on Keycloak login
- `ProjectMembership` CRUD (admin only)
- Role conflict validation (researcher ≠ checker on same project)
- Admin UI: project list, checker assignment

---

## Iteration 2 — Airlock request lifecycle (researcher side)

**Goal**: Researcher can create a request and upload output objects.

Spec to write first:
- OpenAPI paths: `POST /requests`, `GET /requests`, `GET /requests/{id}`, `POST /requests/{id}/objects`, `GET /requests/{id}/objects/{object_id}`
- DB migration: `AirlockRequest`, `OutputObject`, `OutputObjectMetadata`, `AuditEvent`
- State machine implementation

Deliverables:
- Request creation (egress + ingress direction)
- File upload with streaming to quarantine S3, checksum computation
- Object metadata form (title, description, statbarn, justification, suppression notes)
- File preview rendering (markdown, CSV, image, PDF, parquet, code)
- Request submission
- Researcher dashboard: list requests, view status
- Audit event emission on all state transitions

---

## Iteration 3 — Agent review

**Goal**: Automatic statbarn-based review runs on submission.

Spec to write first:
- Agent rule engine design (statbarn → rules mapping)
- `Review` table migration
- ARQ job: `agent_review_job`

Deliverables:
- Statbarn rule engine (initial rule set from SACRO guide)
- Agent review ARQ job triggered on `SUBMITTED`
- Structured report output per object
- Optional LLM narrative via configurable provider
- Agent report stored as `Review` record
- State transition: `SUBMITTED` → `AGENT_REVIEW` → `HUMAN_REVIEW`
- Notification to checkers when agent report is ready

---

## Iteration 4 — Human review (checker side)

**Goal**: Output checkers can review requests and provide structured feedback.

Spec to write first:
- OpenAPI paths: `POST /requests/{id}/reviews`, `GET /requests/{id}/reviews`
- Checker dashboard spec

Deliverables:
- Checker dashboard: queue of requests awaiting review
- Per-object review form with agent report displayed alongside
- Accept agent findings / override per object
- Decision: approve / request changes / reject
- Checker feedback written to `OutputObjectMetadata.checker_feedback`
- Two-reviewer rule enforcement
- State transitions to `CHANGES_REQUESTED`, `APPROVED`, `REJECTED`
- Notifications: researcher notified of outcome

---

## Iteration 5 — Revision cycle (researcher response)

**Goal**: Researcher can respond to feedback and resubmit.

Deliverables:
- Researcher sees per-object feedback inline with their objects
- Replacement object upload (new version of a logical object)
- Metadata carry-forward on replacement
- Resubmission (returns request to `SUBMITTED`, triggers agent review again)
- Lineage display in UI: version history per logical object

---

## Iteration 6 — Release (RO-Crate + pre-signed URL)

**Goal**: Approved requests are packaged and delivered.

Deliverables:
- RO-Crate assembly from approved objects + metadata
- Checksum verification before assembly
- Upload crate zip to release S3 bucket
- Pre-signed URL generation (configurable TTL)
- `ReleaseRecord` created
- Email notification with download link
- State transition to `RELEASED`
- URL expiry warning notification (CronJob)

---

## Iteration 7 — Admin dashboard & metrics

**Goal**: Admins and senior checkers have full visibility and operational metrics.

Deliverables:
- All-projects request overview (status, age, direction)
- Metrics: median time to review, requests per checker, approval rates, revision counts
- Audit log viewer (filterable by project, user, event type, date range)
- Checker assignment management
- Stuck request detection (requests waiting > configurable SLA)
- Export audit log as CSV

---

## Iteration 7.5 — Datastar UI

**Goal**: Server-rendered Datastar UI covering all backend functionality from iterations 1–7.

Spec to write first:
- Template structure, Datastar patterns, SSE endpoints
- See `spec/iteration-7.5-spec.md` for full spec

Deliverables:
- Base template shell (nav, project switcher, auth state, flash messages)
- Researcher views: request list, create, detail, upload, metadata, replace, resubmit
- Checker views: review queue, review form with agent report alongside
- Admin views: request overview, metrics dashboard, audit log, membership management
- File preview component (CSV, markdown, code, image, PDF)
- SSE live updates (request status, review queue)
- Minimal custom CSS (no framework, no build step)

---

## Iteration 8 — Ingress flow

**Goal**: Complete the ingress direction (import into TRE).

Deliverables:
- Ingress request creation by admin/external submitter
- Pre-signed PUT URL for external file submission
- Review flow (same as egress)
- Delivery to workspace on approval (pre-signed GET URL for workspace to consume)
- **UI**: Ingress-specific views (upload via pre-signed PUT, delivery status)

---

## Iteration 9 — Hardening & observability

Deliverables:
- Prometheus metrics endpoint (`/metrics`)
- Structured JSON logging
- OpenTelemetry tracing
- Horizontal scaling smoke tests (multi-pod ARQ worker concurrency)
- Security review: CSRF, rate limiting, input validation audit
- Pen test checklist
- Production Helm values review
- Runbook documentation
- **UI**: CSRF token integration, error pages (403, 404, 500), loading states

---

## Later / backlog

- Per-user notification preference settings
- Slack / Teams webhook backend
- Multi-language UI (i18n)
- Bulk operations (approve multiple objects in one action)
- Researcher self-service statbarn lookup / guidance
- API key support for programmatic submission
- TRE-specific RO-Crate profile proposal (community contribution)
