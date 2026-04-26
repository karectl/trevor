# Iteration 14 Spec — Notification Service: Core + In-App Backend

## Goal

Implement the notification abstraction layer prescribed by ADR-0009: `NotificationBackend` protocol, `NotificationRouter` dispatcher, `InAppBackend` (DB-backed), `Notification` model, API endpoints for reading/marking notifications, and a static unread-count badge in the nav. Notification dispatch runs asynchronously via ARQ — HTTP handlers never call backends directly.

---

## Current state

| Component | Status |
|---|---|
| `Notification` model | Missing |
| `NotificationBackend` protocol | Missing |
| `NotificationRouter` | Missing |
| `InAppBackend` | Missing |
| `notification_service.py` | Missing |
| Notification API endpoints | Missing |
| ARQ `send_notifications_job` | Missing |
| Nav badge (unread count) | Missing |
| Email backend (SMTP) | Out of scope — iteration 15 |
| Webhook backend | Out of scope — backlog |
| SSE live update for badge | Out of scope — iteration 16 |

---

## Scope decisions

| Item | Decision | Rationale |
|---|---|---|
| Email backend | Not this iteration | Iteration 15; requires SMTP config + Jinja2 email templates |
| Webhook backend | Backlog | Low priority; no users requesting yet |
| SSE for live badge | Iteration 16 | Static badge with page-load fetch is sufficient for v1 |
| Dispatch location | ARQ job only | HTTP handlers enqueue `send_notifications_job`; keeps request latency low |
| InAppBackend | Always enabled | ADR-0009: in-app is the reliable fallback |
| Recipient resolution | From `ProjectMembership` + `AirlockRequest.submitted_by` | No separate recipient config table |
| Notification preferences | Not this iteration | v2 feature per ADR-0009 |
| Pagination | Cursor-based (created_at desc) | Simple, efficient for "load more" UI |
| Mark-all-read | Not this iteration | Single mark-read endpoint is sufficient for v1 |

---

## 1. Notification model

### File: `src/trevor/models/notification.py`

```python
"""Notification model — in-app notification records."""

import enum
import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class NotificationEventType(enum.StrEnum):
    REQUEST_SUBMITTED = "request.submitted"
    AGENT_REVIEW_READY = "agent_review.ready"
    REQUEST_CHANGES_REQUESTED = "request.changes_requested"
    REQUEST_APPROVED = "request.approved"
    REQUEST_REJECTED = "request.rejected"
    REQUEST_RELEASED = "request.released"
    PRESIGNED_URL_EXPIRING = "presigned_url.expiring_soon"


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(index=True)          # FK to users.id
    event_type: str = Field(index=True)              # NotificationEventType value
    title: str                                        # Human-readable short title
    body: str                                         # Human-readable body text
    request_id: uuid.UUID | None = Field(default=None, index=True)  # optional link to AirlockRequest
    read: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
```

Notes:
- `user_id` is not a formal SQLModel `Relationship` — we avoid cross-model relationship declarations per existing codebase convention.
- `event_type` is stored as `str` (not enum column) for forward-compatibility with new event types.
- Composite index on `(user_id, read, created_at)` added in migration for the common "unread for user" query.

---

## 2. Notification schemas

### File: `src/trevor/schemas/notification.py`

```python
"""Pydantic schemas for notification endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    event_type: str
    title: str
    body: str
    request_id: uuid.UUID | None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountRead(BaseModel):
    count: int
```

---

## 3. NotificationEvent dataclass

### Defined in: `src/trevor/services/notification_service.py`

```python
from dataclasses import dataclass, field
import uuid


@dataclass(frozen=True)
class NotificationEvent:
    """Immutable event passed to backends for dispatch."""
    event_type: str                          # NotificationEventType value
    title: str                               # Short summary
    body: str                                # Longer description
    request_id: uuid.UUID | None = None      # Link to AirlockRequest
    recipient_user_ids: list[uuid.UUID] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)  # Extra context (e.g. project name)
```

This is what gets serialized into the ARQ job payload and passed to each backend.

---

## 4. NotificationBackend protocol

### Defined in: `src/trevor/services/notification_service.py`

```python
from typing import Protocol, runtime_checkable
from sqlmodel.ext.asyncio.session import AsyncSession


@runtime_checkable
class NotificationBackend(Protocol):
    async def send(
        self,
        event: NotificationEvent,
        session: AsyncSession,
    ) -> None:
        """Deliver a notification event.

        session is provided so DB-backed backends (InAppBackend) can write
        directly. Non-DB backends may ignore it.
        """
        ...
```

`@runtime_checkable` allows `isinstance()` checks in tests.

---

## 5. InAppBackend

### Defined in: `src/trevor/services/notification_service.py`

```python
class InAppBackend:
    """Writes one Notification row per recipient to the DB."""

    async def send(
        self,
        event: NotificationEvent,
        session: AsyncSession,
    ) -> None:
        from trevor.models.notification import Notification

        for user_id in event.recipient_user_ids:
            notification = Notification(
                user_id=user_id,
                event_type=event.event_type,
                title=event.title,
                body=event.body,
                request_id=event.request_id,
            )
            session.add(notification)
        # Caller is responsible for commit
```

---

## 6. NotificationRouter

### Defined in: `src/trevor/services/notification_service.py`

```python
import logging

logger = logging.getLogger(__name__)


class NotificationRouter:
    """Dispatches a NotificationEvent to all registered backends.

    Error isolation: failure in one backend is logged but does not prevent
    other backends from executing (ADR-0009).
    """

    def __init__(self, backends: list[NotificationBackend]) -> None:
        self._backends = backends

    async def dispatch(
        self,
        event: NotificationEvent,
        session: AsyncSession,
    ) -> None:
        if not event.recipient_user_ids:
            logger.debug("NotificationRouter: no recipients for %s, skipping", event.event_type)
            return

        for backend in self._backends:
            try:
                await backend.send(event, session)
            except Exception:
                logger.exception(
                    "NotificationRouter: backend %s failed for event %s",
                    type(backend).__name__,
                    event.event_type,
                )
```

---

## 7. notification_service.py — helper functions

### File: `src/trevor/services/notification_service.py`

The protocol, backends, router, and event dataclass from sections 3–6 all live in this file. Additional helper functions:

#### `get_recipients(event_type, request, session) -> list[UUID]`

```python
async def get_recipients(
    event_type: str,
    request: "AirlockRequest",
    session: AsyncSession,
) -> list[uuid.UUID]:
    """Resolve recipient user IDs based on event type and request context.

    Rules:
    - request.submitted       → all output_checker + senior_checker on the project
    - agent_review.ready      → all output_checker + senior_checker on the project
    - request.changes_requested → request.submitted_by (the researcher)
    - request.approved        → request.submitted_by
    - request.rejected        → request.submitted_by
    - request.released        → request.submitted_by
    - presigned_url.expiring_soon → request.submitted_by
    """
    from trevor.models.project import ProjectMembership

    checker_events = {
        NotificationEventType.REQUEST_SUBMITTED,
        NotificationEventType.AGENT_REVIEW_READY,
    }
    researcher_events = {
        NotificationEventType.REQUEST_CHANGES_REQUESTED,
        NotificationEventType.REQUEST_APPROVED,
        NotificationEventType.REQUEST_REJECTED,
        NotificationEventType.REQUEST_RELEASED,
        NotificationEventType.PRESIGNED_URL_EXPIRING,
    }

    if event_type in checker_events:
        result = await session.exec(
            select(ProjectMembership.user_id).where(
                ProjectMembership.project_id == request.project_id,
                ProjectMembership.role.in_(["output_checker", "senior_checker"]),
            )
        )
        return list(result.all())

    if event_type in researcher_events:
        return [request.submitted_by] if request.submitted_by else []

    logger.warning("get_recipients: unknown event_type %s", event_type)
    return []
```

#### `create_event(event_type, request, session) -> NotificationEvent`

```python
async def create_event(
    event_type: str,
    request: "AirlockRequest",
    session: AsyncSession,
) -> NotificationEvent:
    """Build a NotificationEvent with resolved recipients and human-readable text."""
    recipients = await get_recipients(event_type, request, session)

    titles = {
        NotificationEventType.REQUEST_SUBMITTED: f"Request submitted: {request.title}",
        NotificationEventType.AGENT_REVIEW_READY: f"Agent review ready: {request.title}",
        NotificationEventType.REQUEST_CHANGES_REQUESTED: f"Changes requested: {request.title}",
        NotificationEventType.REQUEST_APPROVED: f"Request approved: {request.title}",
        NotificationEventType.REQUEST_REJECTED: f"Request rejected: {request.title}",
        NotificationEventType.REQUEST_RELEASED: f"Request released: {request.title}",
        NotificationEventType.PRESIGNED_URL_EXPIRING: f"Download link expiring: {request.title}",
    }

    bodies = {
        NotificationEventType.REQUEST_SUBMITTED: f'Airlock request "{request.title}" has been submitted and is awaiting review.',
        NotificationEventType.AGENT_REVIEW_READY: f'Automated agent review is complete for "{request.title}". Human review can begin.',
        NotificationEventType.REQUEST_CHANGES_REQUESTED: f'A reviewer has requested changes to your request "{request.title}".',
        NotificationEventType.REQUEST_APPROVED: f'Your request "{request.title}" has been approved.',
        NotificationEventType.REQUEST_REJECTED: f'Your request "{request.title}" has been rejected.',
        NotificationEventType.REQUEST_RELEASED: f'Your request "{request.title}" has been released. Download links are available.',
        NotificationEventType.PRESIGNED_URL_EXPIRING: f'Download links for "{request.title}" are expiring soon.',
    }

    return NotificationEvent(
        event_type=event_type,
        title=titles.get(event_type, event_type),
        body=bodies.get(event_type, ""),
        request_id=request.id,
        recipient_user_ids=recipients,
    )
```

#### `get_router(settings) -> NotificationRouter`

```python
def get_router(settings: "Settings") -> NotificationRouter:
    """Build a NotificationRouter with enabled backends.

    InAppBackend is always registered.
    Future: SmtpBackend, WebhookBackend added here based on settings.
    """
    backends: list[NotificationBackend] = [InAppBackend()]
    # Future iteration 15: if settings.smtp_notifications_enabled: backends.append(SmtpBackend(...))
    return NotificationRouter(backends)
```

---

## 8. Changes to worker.py — `send_notifications_job`

### Modified file: `src/trevor/worker.py`

Add a new ARQ job function:

```python
async def send_notifications_job(
    ctx: dict[str, Any],
    event_type: str,
    request_id: str,
) -> None:
    """Dispatch a notification event for a given request and event type.

    Called asynchronously from HTTP handlers via arq.enqueue_job().
    """
    from trevor.models.request import AirlockRequest
    from trevor.services.notification_service import create_event, get_router

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings: Settings = ctx["settings"]

    if not settings.notifications_enabled:
        logger.debug("send_notifications_job: notifications disabled, skipping")
        return

    import uuid as _uuid
    req_uuid = _uuid.UUID(request_id)

    async with session_factory() as session:
        req = await session.get(AirlockRequest, req_uuid)
        if req is None:
            logger.error("send_notifications_job: request %s not found", request_id)
            return

        event = await create_event(event_type, req, session)
        if not event.recipient_user_ids:
            logger.debug("send_notifications_job: no recipients for %s", event_type)
            return

        router = get_router(settings)
        await router.dispatch(event, session)
        await session.commit()
        logger.info(
            "send_notifications_job: dispatched %s for request %s to %d recipients",
            event_type,
            request_id,
            len(event.recipient_user_ids),
        )
```

Register in `WorkerSettings.functions`:

```python
functions = [agent_review_job, release_job, send_notifications_job]
```

### Enqueue points

Add `send_notifications_job` enqueue calls at the end of existing jobs in `worker.py`:

1. **`agent_review_job`** — after successful transition to `HUMAN_REVIEW`, enqueue:
   ```python
   await ctx["redis"].enqueue_job("send_notifications_job", "agent_review.ready", request_id)
   ```

2. **`release_job`** — after successful release, enqueue:
   ```python
   await ctx["redis"].enqueue_job("send_notifications_job", "request.released", request_id)
   ```

### Enqueue from HTTP handlers

The following routers must enqueue `send_notifications_job` after state transitions (via `arq_pool.enqueue_job()`). The ARQ pool is made available as a FastAPI dependency (see section 12).

| Router | Trigger | Event type |
|---|---|---|
| `routers/requests.py` — submit endpoint | After `SUBMITTED` transition | `request.submitted` |
| `routers/reviews.py` — create review | After `CHANGES_REQUESTED` decision | `request.changes_requested` |
| `routers/reviews.py` — create review | After 2nd approval → `APPROVED` | `request.approved` |
| `routers/reviews.py` — create review | After rejection → `REJECTED` | `request.rejected` |
| `routers/requests.py` — resubmit endpoint | After `SUBMITTED` transition | `request.submitted` |

Enqueue pattern in HTTP handlers:

```python
from trevor.worker import send_notifications_job

# After state transition + commit:
pool = request.app.state.arq_pool  # ArqRedis, set in lifespan
await pool.enqueue_job("send_notifications_job", event_type, str(req.id))
```

The `arq_pool` is created in `app.py` lifespan and stored on `app.state.arq_pool`. If `notifications_enabled=False` or pool is None (tests), skip enqueue.

---

## 9. API endpoints

### File: `src/trevor/routers/notifications.py`

Router prefix: `/notifications`

#### `GET /notifications`

List notifications for the current user, ordered by `created_at` desc.

Query params:
- `limit: int = 20` (max 100)
- `before: datetime | None` — cursor for pagination (return notifications with `created_at < before`)
- `unread_only: bool = False`

Returns: `list[NotificationRead]`

```python
@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    auth: CurrentAuth,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
    before: datetime | None = Query(default=None),
    unread_only: bool = Query(default=False),
) -> list[NotificationRead]:
    stmt = select(Notification).where(Notification.user_id == auth.user.id)
    if unread_only:
        stmt = stmt.where(Notification.read == False)  # noqa: E712
    if before:
        stmt = stmt.where(Notification.created_at < before)
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    result = await session.exec(stmt)
    return [NotificationRead.model_validate(n) for n in result.all()]
```

#### `PATCH /notifications/{id}/read`

Mark a single notification as read. Returns the updated notification.

```python
@router.patch("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(
    notification_id: uuid.UUID,
    auth: CurrentAuth,
    session: AsyncSession = Depends(get_session),
) -> NotificationRead:
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != auth.user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.read = True
    session.add(notification)
    await session.commit()
    await session.refresh(notification)
    return NotificationRead.model_validate(notification)
```

#### `GET /notifications/unread-count`

Returns the unread notification count for the current user.

```python
@router.get("/unread-count", response_model=UnreadCountRead)
async def unread_count(
    auth: CurrentAuth,
    session: AsyncSession = Depends(get_session),
) -> UnreadCountRead:
    from sqlalchemy import func

    result = await session.exec(
        select(func.count()).where(
            Notification.user_id == auth.user.id,
            Notification.read == False,  # noqa: E712
        )
    )
    return UnreadCountRead(count=result.one())
```

---

## 10. Settings additions

### Changes to `src/trevor/settings.py`

```python
# Notifications
notifications_enabled: bool = True
```

This is a global kill switch. When `False`, `send_notifications_job` returns early and no backends are invoked. Individual backend toggles (SMTP, webhook) will be added in their respective iterations.

---

## 11. App changes

### Changes to `src/trevor/app.py`

1. Register notification router:
   ```python
   from trevor.routers import notifications
   app.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
   ```

2. Create ARQ pool in lifespan (if not already present):
   ```python
   from arq import create_pool
   from arq.connections import RedisSettings

   async def lifespan(app: FastAPI):
       settings = get_settings()
       if settings.notifications_enabled:
           try:
               app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
           except Exception:
               logger.warning("Failed to connect to Redis; notification enqueue disabled")
               app.state.arq_pool = None
       else:
           app.state.arq_pool = None
       yield
       if app.state.arq_pool:
           await app.state.arq_pool.close()
   ```

   In tests (no Redis), `app.state.arq_pool` is `None` and enqueue calls are skipped.

---

## 12. Alembic migration

### File: `alembic/versions/XXXX_add_notification_table.py`

```python
def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_event_type", "notifications", ["event_type"])
    op.create_index("ix_notifications_request_id", "notifications", ["request_id"])
    op.create_index("ix_notifications_read", "notifications", ["read"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    # Composite index for the primary query pattern: unread notifications for a user, newest first
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", "read", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_read", table_name="notifications")
    op.drop_index("ix_notifications_request_id", table_name="notifications")
    op.drop_index("ix_notifications_event_type", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
```

Generate with: `uv run alembic revision --autogenerate -m "add notification table"`

---

## 13. UI changes — notification badge

### File: `src/trevor/templates/components/notification_badge.html`

```html
{# Notification bell badge — included in nav.html #}
{# Receives `unread_count` from template context #}
<a href="/notifications" class="notification-badge" title="Notifications">
  🔔
  {% if unread_count and unread_count > 0 %}
  <span class="badge badge-count">{{ unread_count }}</span>
  {% endif %}
</a>
```

### Changes to `src/trevor/templates/components/nav.html`

Insert badge between the nav-links spacer and user-info:

```html
<span class="spacer"></span>
{% include "components/notification_badge.html" %}
<span class="user-info">
```

### Changes to `src/trevor/static/style.css`

Add notification badge styles:

```css
.notification-badge {
    position: relative;
    text-decoration: none;
    margin-right: 1rem;
}
.notification-badge .badge-count {
    position: absolute;
    top: -6px;
    right: -8px;
    background: var(--color-rejected, #dc3545);
    color: white;
    border-radius: 50%;
    font-size: 0.7rem;
    min-width: 1.2em;
    padding: 0 0.3em;
    text-align: center;
    line-height: 1.2em;
}
```

### Template context injection

The `unread_count` must be available in every page that renders `nav.html`. Add a helper to the UI router or a Jinja2 context processor:

In `src/trevor/routers/ui.py`, add a helper function used by all UI route handlers:

```python
async def _get_unread_count(user_id: uuid.UUID, session: AsyncSession) -> int:
    from sqlalchemy import func
    from trevor.models.notification import Notification

    result = await session.exec(
        select(func.count()).where(
            Notification.user_id == user_id,
            Notification.read == False,  # noqa: E712
        )
    )
    return result.one()
```

Every UI route that renders a template must include `unread_count=await _get_unread_count(auth.user.id, session)` in the template context dict.

---

## 14. Test plan

### File: `tests/test_notifications.py`

All tests use in-memory SQLite with `DEV_AUTH_BYPASS=true`. No Redis needed — notification jobs are tested by calling the job function directly (same pattern as `test_reviews.py`).

#### Model / service unit tests

| Test | Validates |
|---|---|
| `test_notification_model_defaults` | `Notification()` has `read=False`, `created_at` auto-set |
| `test_notification_event_frozen` | `NotificationEvent` is immutable (frozen dataclass) |
| `test_inapp_backend_creates_rows` | `InAppBackend.send()` creates one `Notification` per recipient |
| `test_inapp_backend_no_recipients` | No rows created when `recipient_user_ids` is empty |
| `test_router_dispatches_to_all_backends` | `NotificationRouter` calls all backends |
| `test_router_isolates_failures` | One backend raising does not prevent others from running |
| `test_router_skips_empty_recipients` | Router returns early when no recipients |
| `test_get_recipients_submitted` | `request.submitted` → checkers on the project |
| `test_get_recipients_changes_requested` | `request.changes_requested` → submitting researcher |
| `test_get_recipients_approved` | `request.approved` → submitting researcher |
| `test_get_recipients_rejected` | `request.rejected` → submitting researcher |
| `test_get_recipients_released` | `request.released` → submitting researcher |
| `test_get_recipients_agent_review_ready` | `agent_review.ready` → checkers on the project |
| `test_get_recipients_unknown_type` | Unknown event type → empty list + warning logged |
| `test_create_event_builds_correct_title` | Title includes request title |
| `test_create_event_builds_correct_body` | Body includes request title |
| `test_get_router_returns_inapp` | `get_router()` always includes `InAppBackend` |

#### API endpoint tests

| Test | Validates |
|---|---|
| `test_list_notifications_empty` | `GET /notifications` returns `[]` for user with none |
| `test_list_notifications_returns_own` | Only returns notifications for the authed user |
| `test_list_notifications_pagination` | `before` cursor returns older notifications |
| `test_list_notifications_unread_only` | `unread_only=True` filters out read notifications |
| `test_list_notifications_limit` | `limit` param respected |
| `test_mark_read` | `PATCH /notifications/{id}/read` sets `read=True` |
| `test_mark_read_not_found` | 404 for non-existent notification |
| `test_mark_read_wrong_user` | 404 when notification belongs to a different user |
| `test_unread_count_zero` | `GET /notifications/unread-count` returns `{"count": 0}` |
| `test_unread_count_accurate` | Count reflects actual unread notifications |
| `test_unread_count_excludes_read` | Already-read notifications not counted |

#### Job integration tests

| Test | Validates |
|---|---|
| `test_send_notifications_job_creates_notifications` | Calling `send_notifications_job` directly creates `Notification` rows |
| `test_send_notifications_job_disabled` | When `notifications_enabled=False`, no rows created |
| `test_send_notifications_job_missing_request` | Logs error, does not raise |

#### Fixture setup

Tests need:
- A project with memberships (researcher + 2 checkers) — extend existing `conftest.py` sample data or create local fixtures
- A submitted `AirlockRequest` with `submitted_by` set
- Direct DB insertion of `Notification` rows for API endpoint tests

---

## New / modified files

```
src/trevor/
  models/notification.py                # NEW — Notification model, NotificationEventType enum
  schemas/notification.py               # NEW — NotificationRead, UnreadCountRead
  services/notification_service.py      # NEW — NotificationEvent, NotificationBackend, InAppBackend,
                                        #        NotificationRouter, get_recipients, create_event, get_router
  routers/notifications.py              # NEW — GET /notifications, PATCH /{id}/read, GET /unread-count
  worker.py                             # MODIFIED — add send_notifications_job, enqueue in agent_review_job + release_job
  settings.py                           # MODIFIED — add notifications_enabled
  app.py                                # MODIFIED — register notifications router, arq_pool in lifespan
  routers/requests.py                   # MODIFIED — enqueue notifications on submit/resubmit
  routers/reviews.py                    # MODIFIED — enqueue notifications on review create
  routers/ui.py                         # MODIFIED — _get_unread_count helper, pass to template context
  templates/components/notification_badge.html  # NEW — bell icon + badge
  templates/components/nav.html         # MODIFIED — include notification_badge.html
  static/style.css                      # MODIFIED — notification badge styles
alembic/versions/
  XXXX_add_notification_table.py        # NEW — migration
tests/
  test_notifications.py                 # NEW — 31 tests
```

---

## Implementation order

1. `src/trevor/models/notification.py` — `Notification` model + `NotificationEventType` enum
2. `src/trevor/schemas/notification.py` — `NotificationRead`, `UnreadCountRead`
3. `alembic/versions/XXXX_add_notification_table.py` — generate + verify migration
4. `src/trevor/services/notification_service.py` — `NotificationEvent`, `NotificationBackend`, `InAppBackend`, `NotificationRouter`, `get_recipients`, `create_event`, `get_router`
5. `src/trevor/settings.py` — add `notifications_enabled`
6. `src/trevor/routers/notifications.py` — API endpoints
7. `src/trevor/app.py` — register router, arq_pool in lifespan
8. `src/trevor/worker.py` — add `send_notifications_job`, enqueue in `agent_review_job` + `release_job`
9. `src/trevor/routers/requests.py` — enqueue on submit/resubmit
10. `src/trevor/routers/reviews.py` — enqueue on review create (changes_requested, approved, rejected)
11. `src/trevor/templates/components/notification_badge.html` — badge template
12. `src/trevor/templates/components/nav.html` — include badge
13. `src/trevor/static/style.css` — badge styles
14. `src/trevor/routers/ui.py` — `_get_unread_count` helper, inject into all template contexts
15. `tests/test_notifications.py` — all 31 tests
16. Run `uv run ruff check . && uv run ruff format --check . && uv run pytest -v`
