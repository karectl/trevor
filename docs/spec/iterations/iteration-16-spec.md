# Iteration 16 Spec — SSE Live Updates

## Goal

Real-time UI updates via Server-Sent Events using Datastar's native SSE support. Three SSE endpoints push HTML fragments to the browser when state changes, eliminating manual page refresh for status tracking.

**Implements**: ADR-0015 (SSE Live Updates via Datastar)

---

## Current state

| Component | Status |
|---|---|
| `src/trevor/routers/ui.py` | 1067-line UI router with all views |
| `src/trevor/templates/base.html` | Shell with nav, Datastar CDN loaded |
| `src/trevor/templates/components/status_badge.html` | Status badge partial |
| `src/trevor/models/request.py` | `AirlockRequest` with `status` field |
| `src/trevor/models/review.py` | `Review` model |
| Notification model | Added in iteration 14, includes `read` boolean field |
| `src/trevor/auth.py` | `CurrentAuth` dependency (JWT/cookie-based) |
| `src/trevor/database.py` | `get_session` async session dependency |
| Datastar v1 | CDN loaded in `base.html`, `@get()` SSE support available |

---

## Scope decisions

| Item | Decision |
|---|---|
| Transport | SSE via `starlette.responses.StreamingResponse` |
| Polling backend | Database polling — not Redis pub/sub |
| Poll interval | 2 seconds |
| Connection timeout | 5 minutes; Datastar reconnects automatically |
| Auth | Session cookie validated once on connection open, not per-poll |
| Router file | New `src/trevor/routers/sse.py` (keep `ui.py` manageable) |
| Router prefix | `/ui/sse` (mounted in `app.py`) |
| First event | Always sent immediately with current state (no wait for change) |
| Fragment format | Datastar `datastar-merge-fragments` event type |
| Dependencies | None new — uses Starlette and asyncio stdlib |

---

## Datastar fragment format

Datastar v1 expects SSE events in this exact format:

```
event: datastar-merge-fragments
data: fragments <div id="target-element-id">...html content...</div>

```

- Event type must be `datastar-merge-fragments`
- Data line must start with `fragments ` followed by the HTML
- HTML root element must have an `id` attribute — Datastar uses it to find and replace the existing DOM element (morph merge by default)
- Double newline terminates the event
- Multi-line HTML: each line prefixed with `data: ` (first line has `data: fragments `, continuation lines have `data: `)

Single-line example:
```
event: datastar-merge-fragments
data: fragments <span id="request-status" class="badge badge-approved">APPROVED</span>

```

Multi-line example:
```
event: datastar-merge-fragments
data: fragments <div id="review-queue-count">
data: <span class="badge">3</span>
data: </div>

```

---

## SSE response helper

Add `src/trevor/sse.py` — a small utility module.

```python
"""SSE utilities for Datastar fragment streaming."""

import asyncio
from collections.abc import AsyncGenerator, Callable, Awaitable
from typing import Any

from starlette.requests import Request
from starlette.responses import StreamingResponse

POLL_INTERVAL = 2  # seconds
CONNECTION_TIMEOUT = 300  # 5 minutes


def format_fragment_event(html: str) -> str:
    """Format an HTML fragment as a Datastar merge-fragments SSE event.

    Args:
        html: HTML string. Root element must have an id attribute.

    Returns:
        SSE-formatted event string with trailing double newline.
    """
    lines = html.strip().splitlines()
    parts = [f"event: datastar-merge-fragments\n"]
    parts.append(f"data: fragments {lines[0]}\n")
    for line in lines[1:]:
        parts.append(f"data: {line}\n")
    parts.append("\n")
    return "".join(parts)


async def sse_stream(
    request: Request,
    poll_fn: Callable[[], Awaitable[str]],
    *,
    poll_interval: float = POLL_INTERVAL,
    timeout: float = CONNECTION_TIMEOUT,
    send_unchanged: bool = False,
) -> AsyncGenerator[str, None]:
    """Generic SSE stream that polls a function and yields fragment events.

    Args:
        request: Starlette request (used for disconnect detection).
        poll_fn: Async callable returning an HTML fragment string.
        poll_interval: Seconds between polls.
        timeout: Total connection lifetime in seconds.
        send_unchanged: If True, send every poll result even if unchanged.
            If False (default), only send when output differs from last sent.

    Yields:
        SSE-formatted event strings.
    """
    elapsed = 0.0
    last_html: str | None = None

    while elapsed < timeout:
        if await request.is_disconnected():
            break

        html = await poll_fn()

        if last_html is None or html != last_html or send_unchanged:
            yield format_fragment_event(html)
            last_html = html

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval


def sse_response(stream: AsyncGenerator[str, None]) -> StreamingResponse:
    """Wrap an SSE async generator in a StreamingResponse.

    Sets correct headers for SSE: content-type, no-cache, keep-alive.
    """
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

Key design notes:
- `poll_fn` is a zero-arg async callable. Each endpoint closes over its own session/query logic.
- `format_fragment_event` handles multi-line HTML correctly.
- `X-Accel-Buffering: no` ensures nginx (common in k8s ingress) doesn't buffer SSE.
- First poll executes immediately (`last_html` starts as `None`), guaranteeing the first event is always sent.
- Client disconnect detected via `request.is_disconnected()` each poll cycle.

---

## SSE endpoints

All endpoints go in `src/trevor/routers/sse.py`.

### Router setup

```python
from fastapi import APIRouter

router = APIRouter(prefix="/ui/sse", tags=["sse"])
```

Registered in `app.py` alongside the existing UI router.

---

### Endpoint 1: Request status badge

**Path**: `GET /ui/sse/requests/{request_id}/status`

**Purpose**: Pushes updated status badge HTML when an `AirlockRequest` status changes.

**Auth**: Requires authenticated user who is a member of the request's project or is admin.

**Fragment target**: `<span id="request-status-badge">...</span>`

**Implementation sketch**:

```python
@router.get("/requests/{request_id}/status")
async def sse_request_status(
    request: Request,
    request_id: UUID,
    auth: CurrentAuth,
    session: AsyncSession = Depends(get_session),
):
    airlock_req = await session.get(AirlockRequest, request_id)
    if not airlock_req:
        raise HTTPException(404)
    # Verify membership or admin (same check as request detail view)
    _check_request_access(auth, airlock_req)

    async def poll() -> str:
        # Refresh from DB each poll
        await session.refresh(airlock_req, ["status"])
        return render_template(
            "components/status_badge_sse.html",
            {"status": airlock_req.status},
        )

    return sse_response(sse_stream(request, poll))
```

**Fragment HTML** (`components/status_badge_sse.html`):

```html
<span id="request-status-badge" class="badge badge-{{ status.value | lower }}">
  {{ status.value | replace('_', ' ') }}
</span>
```

**Session management note**: The SSE endpoint holds a DB session for the lifetime of the connection. Use a dedicated session scope — do not reuse the request-scoped `get_session`. Instead, create a session inside the poll function or use `async_sessionmaker` directly:

```python
async def poll() -> str:
    async with async_session_factory() as poll_session:
        result = await poll_session.get(AirlockRequest, request_id)
        return render_status_badge(result.status)
```

This avoids holding a single session open for 5 minutes and ensures each poll sees fresh data (no stale cache from SQLAlchemy identity map).

---

### Endpoint 2: Review queue count

**Path**: `GET /ui/sse/review/queue-count`

**Purpose**: Pushes updated count of requests in `HUMAN_REVIEW` state that the current checker can review.

**Auth**: Requires authenticated user with `output_checker` or `senior_checker` role on at least one project, or admin.

**Fragment target**: `<span id="review-queue-count">...</span>`

**Implementation sketch**:

```python
@router.get("/review/queue-count")
async def sse_review_queue_count(
    request: Request,
    auth: CurrentAuth,
):
    # Validate checker role at connection time
    if not auth.is_admin and not _has_checker_role(auth):
        raise HTTPException(403)

    async def poll() -> str:
        async with async_session_factory() as session:
            count = await _count_reviewable_requests(session, auth)
            return f'<span id="review-queue-count" class="badge badge-count">{count}</span>'

    return sse_response(sse_stream(request, poll))
```

**Query logic** (`_count_reviewable_requests`):
1. Select `AirlockRequest` where `status = HUMAN_REVIEW`
2. Filter to projects where current user has checker membership
3. Exclude requests the user has already reviewed
4. Return `count()`

---

### Endpoint 3: Notification count

**Path**: `GET /ui/sse/notifications/count`

**Purpose**: Pushes unread notification count for the nav bar badge.

**Auth**: Any authenticated user.

**Fragment target**: `<span id="notification-count">...</span>`

**Implementation sketch**:

```python
@router.get("/notifications/count")
async def sse_notification_count(
    request: Request,
    auth: CurrentAuth,
):
    user_id = auth.user.id

    async def poll() -> str:
        async with async_session_factory() as session:
            count = await _count_unread_notifications(session, user_id)
            if count > 0:
                return f'<span id="notification-count" class="badge badge-notify">{count}</span>'
            else:
                return '<span id="notification-count"></span>'

    return sse_response(sse_stream(request, poll))
```

When count is 0, the badge is an empty span (visually hidden via CSS). When count > 0, badge displays the number.

---

## Template changes

### `templates/researcher/request_detail.html`

Replace static status badge with SSE-connected container:

```html
<!-- Before -->
{% include 'components/status_badge.html' %}

<!-- After -->
<div data-on-load="@get('/ui/sse/requests/{{ request.id }}/status')">
  {% include 'components/status_badge.html' %}
</div>
```

The initial render shows the current badge (no flash of empty content). Datastar opens an SSE connection on load and merges incoming fragments by matching `id="request-status-badge"`.

### `templates/components/nav.html`

Add SSE connections for review queue count (checker/admin only) and notification count (all users):

```html
<!-- Review queue badge (checker/admin only) -->
{% if auth.is_admin or has_checker_role %}
<li>
  <a href="/ui/review">
    Reviews
    <span data-on-load="@get('/ui/sse/review/queue-count')">
      <span id="review-queue-count" class="badge badge-count">{{ review_queue_count }}</span>
    </span>
  </a>
</li>
{% endif %}

<!-- Notification badge (all users) -->
<li>
  <a href="/ui/notifications">
    Notifications
    <span data-on-load="@get('/ui/sse/notifications/count')">
      <span id="notification-count" class="badge badge-notify">
        {% if notification_count > 0 %}{{ notification_count }}{% endif %}
      </span>
    </span>
  </a>
</li>
```

### `templates/components/status_badge_sse.html` (new)

Identical to existing `status_badge.html` but with a stable `id` attribute for SSE targeting:

```html
<span id="request-status-badge" class="badge badge-{{ status.value | lower }}">
  {{ status.value | replace('_', ' ') }}
</span>
```

If the existing `status_badge.html` already has an `id`, reuse it — no new template needed.

---

## Auth for SSE

SSE connections are long-lived HTTP requests. Auth is validated **once** at connection open:

1. `CurrentAuth` dependency resolves from session cookie / JWT header as usual
2. If auth fails → 401 response, no SSE stream opened
3. Endpoint-specific access checks (project membership, checker role) run once at connection open
4. No per-poll re-auth — if a user's role is revoked mid-connection, the stream continues until timeout (5 min max). Acceptable tradeoff for simplicity.

If the auth cookie expires during a connection, the stream continues until timeout. On the next reconnect, Datastar's `@get()` will receive a 401 and stop retrying (standard Datastar behavior for non-2xx SSE responses).

---

## Connection lifecycle

```
Client loads page
  → Datastar sees data-on-load="@get('/ui/sse/...')"
  → Browser opens GET request with Accept: text/event-stream
  → Server validates auth, opens StreamingResponse
  → First poll: immediate, sends current state
  → Loop: sleep 2s → poll DB → if changed, send event
  → After 5 min: generator returns, connection closes
  → Datastar auto-reconnects (opens new GET)
  → On page navigation: browser closes connection
  → Server detects disconnect via request.is_disconnected()
  → Generator exits cleanly
```

**Error handling**:
- DB query failure in poll: log error, skip event, continue polling. Do not kill the stream for transient DB errors.
- If DB is down for multiple consecutive polls, let the connection timeout naturally.

---

## Router registration

In `src/trevor/app.py`:

```python
from trevor.routers import sse

def create_app(settings):
    app = FastAPI(...)
    # ... existing router includes ...
    app.include_router(sse.router)
    return app
```

---

## New and modified files

| File | Action | Description |
|---|---|---|
| `src/trevor/sse.py` | **New** | SSE helper: `format_fragment_event`, `sse_stream`, `sse_response` |
| `src/trevor/routers/sse.py` | **New** | Three SSE endpoints |
| `src/trevor/app.py` | Modify | Register `sse.router` |
| `src/trevor/templates/components/status_badge_sse.html` | **New** | Status badge with stable `id` (if existing badge lacks one) |
| `src/trevor/templates/researcher/request_detail.html` | Modify | Add `data-on-load` wrapper around status badge |
| `src/trevor/templates/components/nav.html` | Modify | Add review queue count + notification count SSE badges |
| `tests/test_sse.py` | **New** | SSE endpoint tests |

---

## Test plan

### `tests/test_sse.py`

**Test 1: SSE request status returns correct content type**
```python
async def test_sse_request_status_content_type(client, sample_request):
    resp = await client.get(f"/ui/sse/requests/{sample_request.id}/status")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"
```

**Test 2: First event contains current state**
```python
async def test_sse_request_status_first_event(client, sample_request):
    resp = await client.get(
        f"/ui/sse/requests/{sample_request.id}/status",
        stream=True,  # httpx streaming
    )
    first_event = await _read_first_sse_event(resp)
    assert "event: datastar-merge-fragments" in first_event
    assert "request-status-badge" in first_event
    assert sample_request.status.value.replace("_", " ") in first_event
```

**Test 3: Auth required**
```python
async def test_sse_request_status_requires_auth(unauthed_client, sample_request):
    resp = await unauthed_client.get(
        f"/ui/sse/requests/{sample_request.id}/status"
    )
    assert resp.status_code == 401
```

**Test 4: Non-member cannot connect**
```python
async def test_sse_request_status_non_member_forbidden(
    client_other_user, sample_request
):
    resp = await client_other_user.get(
        f"/ui/sse/requests/{sample_request.id}/status"
    )
    assert resp.status_code == 403
```

**Test 5: Review queue count requires checker role**
```python
async def test_sse_review_queue_requires_checker(researcher_client):
    resp = await researcher_client.get("/ui/sse/review/queue-count")
    assert resp.status_code == 403
```

**Test 6: Notification count returns for any authed user**
```python
async def test_sse_notification_count(client):
    resp = await client.get("/ui/sse/notifications/count")
    assert resp.status_code == 200
    first_event = await _read_first_sse_event(resp)
    assert "notification-count" in first_event
```

**Test 7: 404 for non-existent request**
```python
async def test_sse_request_status_not_found(client):
    resp = await client.get(f"/ui/sse/requests/{uuid4()}/status")
    assert resp.status_code == 404
```

**Test helper** — `_read_first_sse_event`: reads from the streaming response until the first `\n\n` delimiter.

**Unit tests for `sse.py`**:
- `test_format_fragment_event_single_line` — verify correct SSE format
- `test_format_fragment_event_multi_line` — verify `data:` prefix on continuation lines
- `test_format_fragment_event_preserves_id` — verify HTML id attribute present in output

---

## Implementation order

1. **`src/trevor/sse.py`** — SSE helper utilities (pure functions, testable in isolation)
2. **Unit tests for `sse.py`** — verify fragment formatting
3. **`src/trevor/routers/sse.py`** — three SSE endpoints
4. **`src/trevor/app.py`** — register SSE router
5. **`tests/test_sse.py`** — endpoint integration tests
6. **Template: `status_badge_sse.html`** — badge with stable `id`
7. **Template: `request_detail.html`** — add `data-on-load` wrapper
8. **Template: `nav.html`** — add review queue + notification SSE badges
9. **`static/style.css`** — badge-count / badge-notify styles (if not already present)
10. **Manual smoke test** — verify SSE streams in browser dev tools
