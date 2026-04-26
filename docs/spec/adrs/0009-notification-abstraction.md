# ADR-0009 — Notification Abstraction Layer

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

trevor needs to notify users of workflow events (request submitted, feedback received, approved, released, etc.). The requirements are:
- Email (SMTP) must be supported as the primary channel
- Alternative channels should be pluggable without code changes
- The existing karectl SMTP service should be used
- In-app notifications (visible in the trevor UI) are useful as a fallback and supplement

---

## Decision

Implement a **Notification Abstraction Layer** with a backend registry pattern.

### Architecture

```python
class NotificationBackend(Protocol):
    async def send(self, event: NotificationEvent, recipients: list[str]) -> None: ...

class NotificationRouter:
    def __init__(self, backends: list[NotificationBackend]): ...
    async def dispatch(self, event: NotificationEvent) -> None: ...
```

All notification dispatch is routed through `NotificationRouter.dispatch()`. Multiple backends can be active simultaneously. Failure in one backend is logged but does not block others.

### Supported backends (v1)

| Backend | Config key | Notes |
|---------|-----------|-------|
| `SmtpBackend` | `notifications.smtp` | Uses karectl SMTP service. Jinja2 email templates. |
| `InAppBackend` | `notifications.inapp` | Writes to `Notification` DB table. Always enabled. |
| `WebhookBackend` | `notifications.webhook` | Posts JSON payload to configured URL(s). Optional. |

### In-app notification table

```python
class Notification(SQLModel, table=True):
    id: UUID
    user_id: UUID          # FK to User
    event_type: str        # e.g. "request.changes_requested"
    title: str
    body: str
    request_id: UUID | None
    read: bool = False
    created_at: datetime
```

The UI polls (or uses SSE) for unread notifications, shown as a badge in the nav bar.

### Event types

| Event type | Default recipients | Template |
|-----------|-------------------|---------|
| `request.submitted` | Assigned checkers | `submitted.html` |
| `agent_review.ready` | Assigned checkers | `agent_report_ready.html` |
| `request.changes_requested` | Submitting researcher | `changes_requested.html` |
| `request.approved` | Researcher, project lead | `approved.html` |
| `request.rejected` | Researcher | `rejected.html` |
| `request.released` | Researcher, download recipients | `released.html` (contains pre-signed URL) |
| `presigned_url.expiring` | Researcher, download recipients | `url_expiring.html` |

### Configuration (Helm values)

```yaml
notifications:
  smtp:
    enabled: true
    host: smtp.karectl.internal
    port: 587
    from_address: trevor@karectl.example
    use_tls: true
  inapp:
    enabled: true   # always recommended
  webhook:
    enabled: false
    urls: []
    secret_header: X-Trevor-Signature
```

### Email templates

Templates live in `trevor/notifications/templates/`. They are Jinja2 HTML templates with a plain-text equivalent (`*.txt`). The `released.html` template includes the pre-signed URL prominently, with an expiry notice.

---

## Consequences

- **Positive**: New notification backends (Slack, Teams, PagerDuty) can be added by implementing the `NotificationBackend` protocol and registering in config — no core changes.
- **Positive**: In-app notifications provide a reliable fallback if SMTP fails.
- **Positive**: Backend failures are isolated — a broken webhook doesn't block email delivery.
- **Negative**: Multiple active backends mean the same event may reach a user multiple times across channels. Mitigation: users can configure their preferences per channel (future v2 feature; for v1, admin configures which backends are active globally).
