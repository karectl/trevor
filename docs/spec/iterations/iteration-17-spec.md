# Iteration 17 Spec — ARQ Cron Jobs: URL Expiry Warnings + Stuck Request Alerts

## Goal

Implement two ARQ cron jobs that proactively alert users about time-sensitive conditions: pre-signed URLs approaching expiry (researcher notification) and requests stuck in review pipeline (admin alert).

---

## Current state

| Component | Status |
|---|---|
| `url_expiry_warning_job` in `worker.py` | Stub — logs and returns |
| `ReleaseRecord` model with `url_expires_at` | Implemented (`models/release.py`) |
| `Settings.stuck_request_hours` | Implemented (default 72) |
| `Settings.presigned_url_ttl` | Implemented (default 604800 = 7 days) |
| `compute_metrics()` stuck detection | Implemented (`services/metrics_service.py`) |
| Notification service (iter 14) | Not yet implemented |
| `ReleaseRecord.expiry_warned_at` | Missing |
| `stuck_request_alert_job` | Missing |
| `Settings.url_expiry_warning_hours` | Missing |

---

## Scope

Two cron jobs:

| Job | Schedule | Target audience | Purpose |
|---|---|---|---|
| `url_expiry_warning_job` | Daily at 00:00 UTC (existing slot) | Researcher who submitted the request | Warn that download URL expires within threshold |
| `stuck_request_alert_job` | Daily at 06:00 UTC | All `tre_admin` users | Alert that requests are stuck in pipeline |

Both jobs are idempotent: re-running produces no duplicate notifications or audit events.

### Dependency on notification dispatch

Iteration 14 (notification service) is not yet implemented. This iteration uses a lightweight `dispatch_notification()` helper that enqueues an ARQ job with a typed payload. If iter 14 lands first, the dispatch call integrates directly. If iter 17 lands first, the dispatch function logs the notification payload and emits the audit event — a "log-and-audit" fallback that iter 14 will later upgrade to email/webhook delivery.

---

## Settings additions

Add to `Settings` in `settings.py`:

```python
url_expiry_warning_hours: int = 48  # warn when URL expires within this window
```

No other settings needed — `stuck_request_hours` already exists (default 72).

---

## Model changes

### `ReleaseRecord` — add `expiry_warned_at`

```python
class ReleaseRecord(SQLModel, table=True):
    ...
    expiry_warned_at: datetime | None = Field(default=None)
```

This field provides idempotency: once set, the job skips this record on subsequent runs. Only reset if a new pre-signed URL is generated (out of scope for this iteration).

### Alembic migration

```
uv run alembic revision --autogenerate -m "add expiry_warned_at to release_records"
```

Single column addition:

```python
def upgrade() -> None:
    op.add_column("release_records", sa.Column("expiry_warned_at", sa.DateTime(), nullable=True))

def downgrade() -> None:
    op.drop_column("release_records", "expiry_warned_at")
```

---

## Notification event types

| Event type | Audience | Payload |
|---|---|---|
| `presigned_url.expiring_soon` | Request submitter (researcher) | `request_id`, `release_id`, `url_expires_at`, `hours_remaining` |
| `request.stuck` | All `tre_admin` users | `request_id`, `title`, `status`, `waiting_hours` |

---

## Job implementations

### `url_expiry_warning_job`

Replace the stub in `worker.py`.

**Algorithm:**

```
1. now = utcnow()
2. threshold = now + timedelta(hours=settings.url_expiry_warning_hours)
3. SELECT * FROM release_records
   WHERE url_expires_at IS NOT NULL
     AND url_expires_at <= threshold
     AND url_expires_at > now          -- not already expired
     AND expiry_warned_at IS NULL      -- not already warned
4. For each record:
   a. Load the AirlockRequest to get submitted_by (researcher user ID)
   b. hours_remaining = (record.url_expires_at - now).total_seconds() / 3600
   c. Dispatch notification:
      - event_type: "presigned_url.expiring_soon"
      - recipient_user_id: request.submitted_by
      - payload: { request_id, release_id, url_expires_at (ISO), hours_remaining }
   d. Emit audit event:
      - event_type: "release.url_expiry_warning"
      - actor_id: "system"
      - request_id: request.id
      - payload: { release_id, url_expires_at (ISO), hours_remaining }
   e. Set record.expiry_warned_at = now
   f. Commit per-record (not batched) to avoid partial failure losing progress
5. Log summary: "url_expiry_warning_job: warned {n} release(s)"
```

**Idempotency:** `expiry_warned_at IS NULL` filter ensures each record is warned exactly once.

**Edge cases:**
- `url_expires_at` in the past → skipped (`url_expires_at > now`)
- `url_expires_at` is `None` → skipped by query filter
- No matching records → log "warned 0 release(s)" and return

### `stuck_request_alert_job`

New function in `worker.py`.

**Algorithm:**

```
1. now = utcnow()
2. threshold = now - timedelta(hours=settings.stuck_request_hours)
3. stuck_statuses = [SUBMITTED, AGENT_REVIEW, HUMAN_REVIEW]
4. SELECT * FROM airlock_requests
   WHERE status IN stuck_statuses
     AND updated_at < threshold
5. If no stuck requests → log and return
6. Collect admin user IDs:
   SELECT id FROM users
   WHERE id IN (
     -- users with tre_admin role are identified at request time via JWT,
     -- not stored in DB. For cron context, query users who have
     -- performed admin actions (actor_id in audit_events with admin event types)
     -- OR: use a simpler heuristic (see Decision below)
   )
7. For each stuck request:
   a. waiting_hours = (now - req.updated_at).total_seconds() / 3600
   b. Emit audit event:
      - event_type: "request.stuck"
      - actor_id: "system"
      - request_id: req.id
      - payload: { status, waiting_hours, title }
   c. Dispatch notification:
      - event_type: "request.stuck"
      - recipient: "role:tre_admin" (broadcast to admin role)
      - payload: { request_id, title, status, waiting_hours }
8. Log summary: "stuck_request_alert_job: {n} stuck request(s) alerted"
```

**Decision — admin recipient resolution:**

Keycloak is source of truth for `tre_admin` role (C-10). The cron job cannot decode JWTs to find admins. Two options:

| Option | Pros | Cons |
|---|---|---|
| A. Broadcast to `role:tre_admin` — notification service resolves recipients at dispatch time via Keycloak Admin API | Clean separation; always current | Requires Keycloak Admin API access |
| B. Query audit_events for distinct `actor_id` values on admin-only event types | No Keycloak dependency | May miss new admins; stale data |

**Decision: Option A.** Dispatch with `recipient_role: "tre_admin"`. The notification service (iter 14) will resolve actual users via Keycloak Admin API or a cached role membership table. Until iter 14 lands, the log-and-audit fallback records the intent.

**Idempotency:** Stuck alerts are intentionally NOT deduplicated — they fire daily as long as the request remains stuck. This is by design: daily reminders for unresolved stuck requests. The audit event `request.stuck` can be queried to see alert history.

---

## Notification dispatch helper

Add `src/trevor/services/notification_service.py` with a minimal interface that iter 14 will flesh out:

```python
"""Notification dispatch — placeholder until iter 14."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def dispatch_notification(
    *,
    event_type: str,
    recipient_user_id: str | None = None,
    recipient_role: str | None = None,
    payload: dict[str, Any],
) -> None:
    """Dispatch a notification. Currently logs; iter 14 adds real delivery."""
    logger.info(
        "notification: event_type=%s recipient_user=%s recipient_role=%s payload=%s",
        event_type,
        recipient_user_id,
        recipient_role,
        payload,
    )
```

This gives both jobs a stable call site. Iter 14 replaces the body with ARQ enqueue / email / webhook logic.

---

## Changes to `worker.py`

### Updated `cron_jobs`

```python
cron_jobs = [
    cron(url_expiry_warning_job, hour={0}, minute=0, run_at_startup=False),
    cron(stuck_request_alert_job, hour={6}, minute=0, run_at_startup=False),
]
```

### Updated `functions`

No change — cron jobs are not on-demand functions. `functions` stays `[agent_review_job, release_job]`.

---

## Audit event types introduced

| Event type | Actor | When |
|---|---|---|
| `release.url_expiry_warning` | `system` | URL expiry warning sent |
| `request.stuck` | `system` | Stuck request alert sent |

---

## Test plan

All tests in `tests/test_cron_jobs.py`.

### `test_url_expiry_warning_job_warns_expiring_records`

1. Create a `ReleaseRecord` with `url_expires_at = now + 24h` (within 48h window)
2. Run `url_expiry_warning_job(ctx)`
3. Assert `expiry_warned_at` is set on the record
4. Assert audit event `release.url_expiry_warning` emitted
5. Assert notification dispatch called with `presigned_url.expiring_soon`

### `test_url_expiry_warning_job_skips_already_warned`

1. Create a `ReleaseRecord` with `url_expires_at = now + 24h` AND `expiry_warned_at` already set
2. Run job
3. Assert no new audit events, no notification dispatched

### `test_url_expiry_warning_job_skips_expired_urls`

1. Create a `ReleaseRecord` with `url_expires_at = now - 1h` (already expired)
2. Run job
3. Assert no warnings

### `test_url_expiry_warning_job_skips_urls_outside_window`

1. Create a `ReleaseRecord` with `url_expires_at = now + 96h` (outside 48h window)
2. Run job
3. Assert no warnings

### `test_stuck_request_alert_job_detects_stuck`

1. Create an `AirlockRequest` with `status=HUMAN_REVIEW`, `updated_at = now - 96h`
2. Run `stuck_request_alert_job(ctx)`
3. Assert audit event `request.stuck` emitted
4. Assert notification dispatch called with `request.stuck`

### `test_stuck_request_alert_job_ignores_recent`

1. Create an `AirlockRequest` with `status=HUMAN_REVIEW`, `updated_at = now - 1h`
2. Run job
3. Assert no alerts

### `test_stuck_request_alert_job_ignores_terminal_states`

1. Create requests in `APPROVED`, `RELEASED`, `REJECTED` states with old `updated_at`
2. Run job
3. Assert no alerts

### `test_stuck_alert_fires_daily_not_deduplicated`

1. Create stuck request
2. Run job twice
3. Assert two audit events emitted (daily reminder behavior)

### Mocking strategy

- Mock `datetime.now(UTC)` via `freezegun` or manual injection to control time
- Patch `trevor.services.notification_service.dispatch_notification` to capture calls
- Use in-memory SQLite with `DEV_AUTH_BYPASS=true` (standard test setup)

---

## New and modified files

| File | Action |
|---|---|
| `src/trevor/settings.py` | Add `url_expiry_warning_hours` |
| `src/trevor/models/release.py` | Add `expiry_warned_at` to `ReleaseRecord` |
| `src/trevor/schemas/release.py` | Add `expiry_warned_at` to `ReleaseRecordRead` |
| `src/trevor/services/notification_service.py` | **New** — dispatch placeholder |
| `src/trevor/worker.py` | Implement `url_expiry_warning_job`, add `stuck_request_alert_job`, update `cron_jobs` |
| `alembic/versions/xxxx_add_expiry_warned_at.py` | **New** — migration |
| `tests/test_cron_jobs.py` | **New** — all cron job tests |

---

## Implementation order

1. `settings.py` — add `url_expiry_warning_hours: int = 48`
2. `models/release.py` — add `expiry_warned_at` field
3. `schemas/release.py` — add `expiry_warned_at` to read schema
4. Alembic migration — `add expiry_warned_at to release_records`
5. `services/notification_service.py` — create dispatch placeholder
6. `worker.py` — implement `url_expiry_warning_job`
7. `worker.py` — implement `stuck_request_alert_job`, wire into `cron_jobs`
8. `tests/test_cron_jobs.py` — all tests
9. Lint + format + full test suite

---

## Out of scope

- Actual notification delivery (email, webhook) — deferred to iter 14
- Re-generating expired pre-signed URLs — separate feature
- Resetting `expiry_warned_at` when a new URL is generated — handle in the URL regeneration feature
- Watch-based real-time stuck detection — daily cron is sufficient for alerts
- UI for notification preferences — future iteration
