# Iteration 7 Spec — Admin Dashboard & Metrics API

## Goal

Backend API endpoints giving admins and senior checkers full visibility into request pipeline health, reviewer performance, and audit history. No UI in this iteration — Datastar frontend comes later.

---

## New Endpoints

### 1. `GET /admin/requests` — All-projects request overview

**Auth**: `tre_admin` or `senior_checker` on any project.

**Query parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `status` | `str` (comma-separated) | all | Filter by status(es) |
| `project_id` | `uuid` | all | Filter by project |
| `direction` | `str` | all | `egress` or `ingress` |
| `sort` | `str` | `-created_at` | Sort field, prefix `-` for desc |
| `limit` | `int` | 50 | Page size (max 100) |
| `offset` | `int` | 0 | Pagination offset |

**Response**: `{ items: RequestSummary[], total: int }`

```json
{
  "items": [
    {
      "id": "uuid",
      "project_id": "uuid",
      "project_name": "display_name",
      "title": "str",
      "status": "HUMAN_REVIEW",
      "direction": "egress",
      "submitted_by_name": "str",
      "object_count": 3,
      "created_at": "iso",
      "updated_at": "iso",
      "age_hours": 72.5
    }
  ],
  "total": 142
}
```

### 2. `GET /admin/metrics` — Pipeline metrics

**Auth**: `tre_admin` or `senior_checker`.

**Query parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `project_id` | `uuid` | all | Scope to project |
| `since` | `datetime` | 30 days ago | Start of window |

**Response**:

```json
{
  "total_requests": 142,
  "by_status": { "DRAFT": 5, "SUBMITTED": 3, ... },
  "median_review_hours": 18.5,
  "mean_review_hours": 24.2,
  "approval_rate": 0.78,
  "revision_rate": 0.15,
  "rejection_rate": 0.07,
  "median_revisions_per_request": 1,
  "requests_per_reviewer": [
    { "reviewer_id": "uuid", "reviewer_name": "str", "count": 12 }
  ],
  "stuck_requests": [
    { "request_id": "uuid", "title": "str", "status": "HUMAN_REVIEW", "waiting_hours": 96.2 }
  ]
}
```

**Stuck threshold**: configurable via `Settings.stuck_request_hours` (default: 72).

### 3. `GET /admin/audit` — Filterable audit log

**Auth**: `tre_admin`.

**Query parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `project_id` | `uuid` | all | Filter by project |
| `actor_id` | `str` | all | Filter by actor |
| `event_type` | `str` | all | Filter by event type (prefix match) |
| `since` | `datetime` | 30 days ago | Start of window |
| `until` | `datetime` | now | End of window |
| `limit` | `int` | 100 | Page size (max 500) |
| `offset` | `int` | 0 | Pagination offset |

**Response**: `{ items: AuditEventRead[], total: int }`

### 4. `GET /admin/audit/export` — CSV export

**Auth**: `tre_admin`.

Same query params as `GET /admin/audit` (no limit/offset — streams all matching rows).

**Response**: `text/csv` with headers: `id, timestamp, event_type, actor_id, request_id, payload`.

---

## New Schemas

### `RequestSummary` (Pydantic)

Fields: `id`, `project_id`, `project_name`, `title`, `status`, `direction`, `submitted_by_name`, `object_count`, `created_at`, `updated_at`, `age_hours`.

### `PipelineMetrics` (Pydantic)

Fields: `total_requests`, `by_status`, `median_review_hours`, `mean_review_hours`, `approval_rate`, `revision_rate`, `rejection_rate`, `median_revisions_per_request`, `requests_per_reviewer`, `stuck_requests`.

### `ReviewerStats` (Pydantic)

Fields: `reviewer_id`, `reviewer_name`, `count`.

### `StuckRequest` (Pydantic)

Fields: `request_id`, `title`, `status`, `waiting_hours`.

---

## Settings additions

| Variable | Type | Default | Description |
|---|---|---|---|
| `STUCK_REQUEST_HOURS` | `int` | 72 | Hours before a request is flagged as stuck |

---

## Implementation notes

- **No new DB tables or migrations.** All data from existing tables via queries.
- Metrics queries use SQLAlchemy Core for efficiency (per ADR-0003). `func.count`, `func.avg`, subqueries for median.
- Median in SQLite: use Python-side sort. In PostgreSQL: `percentile_cont(0.5)`.
- `age_hours` computed as `(now - created_at).total_seconds() / 3600`.
- Stuck requests: `status in (SUBMITTED, AGENT_REVIEW, HUMAN_REVIEW)` and `updated_at < now - stuck_threshold`.
- CSV export uses `StreamingResponse` with a generator yielding rows.
- Senior checker access: query `ProjectMembership` for `role = senior_checker` on any project. If found, grant read access to admin endpoints (except audit export, which is admin-only).

---

## New files

- `src/trevor/routers/admin.py` — all 4 endpoints
- `src/trevor/schemas/admin.py` — RequestSummary, PipelineMetrics, ReviewerStats, StuckRequest
- `src/trevor/services/metrics_service.py` — query functions
- `tests/test_admin.py` — endpoint tests

---

## Test plan

1. `GET /admin/requests` — empty, with data, filtered by status, filtered by project, pagination
2. `GET /admin/metrics` — basic metrics calculation, stuck detection
3. `GET /admin/audit` — filtered by event_type, by actor, by date range, pagination
4. `GET /admin/audit/export` — CSV format, content-type header
5. Auth: non-admin rejected (403), senior_checker access for requests/metrics
