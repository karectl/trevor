# ADR-0015 — SSE Live Updates via Datastar

**Status**: Accepted  
**Date**: 2026-04  
**Deciders**: trevor project lead  
**Related**: ADR-0001 (Datastar frontend)

---

## Context

The trevor UI currently requires manual page refresh to see status changes (request submitted → agent review → human review → approved). Real-time updates improve UX for checkers monitoring the review queue and researchers watching their request progress.

Datastar v1 natively supports Server-Sent Events (SSE) via `@get()` actions and `data-on-load` attributes. No WebSocket infrastructure needed — SSE works over standard HTTP through any reverse proxy.

---

## Decision

Implement **SSE endpoints** for live UI updates using Datastar's native SSE support.

### Architecture

SSE endpoints return `text/event-stream` responses. Each event contains a Datastar-compatible fragment (HTML partial) that Datastar merges into the DOM automatically.

```python
from starlette.responses import StreamingResponse

async def sse_request_status(request_id: UUID):
    async def event_stream():
        last_status = None
        while True:
            status = await get_request_status(request_id)
            if status != last_status:
                html = render_status_badge(status)
                yield f"event: datastar-merge-fragments\ndata: fragments {html}\n\n"
                last_status = status
            await asyncio.sleep(2)
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

### Polling vs Redis pub/sub

For v1, use **database polling** (2-second interval). Rationale:
- Simple, no new infrastructure
- trevor's scale (tens of concurrent users, not thousands) doesn't warrant pub/sub complexity
- Redis pub/sub can be added later as an optimization without changing the SSE contract

### Endpoints

| SSE Endpoint | Updates | Used by |
|---|---|---|
| `/ui/sse/requests/{id}/status` | Request status badge | Researcher detail view |
| `/ui/sse/review/queue` | Review queue count | Checker nav badge |
| `/ui/sse/notifications/count` | Unread notification count | All users nav badge |

### Client-side integration

```html
<!-- In request_detail.html -->
<div data-on-load="@get('/ui/sse/requests/{{ request.id }}/status')">
  {% include 'components/status_badge.html' %}
</div>
```

---

## Consequences

- **Positive**: Real-time UI without WebSocket complexity or JS build step
- **Positive**: Works through any HTTP reverse proxy / load balancer
- **Positive**: Datastar handles DOM merging — zero custom JavaScript
- **Negative**: DB polling adds load proportional to connected clients. Mitigation: 2s interval with connection timeout (5 min), index on status column
- **Negative**: SSE is unidirectional (server → client). Sufficient for status updates; form submissions remain standard POST
