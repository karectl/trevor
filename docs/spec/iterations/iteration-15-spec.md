# Iteration 15 Spec — Email Notification Backend

## Goal

Implement `SmtpBackend` for the notification abstraction layer (ADR-0009). trevor sends email notifications for all 7 workflow events via the karectl SMTP service using Jinja2 templates with HTML and plain-text variants.

---

## Current state

| Component | Status |
|---|---|
| `NotificationBackend` protocol | Implemented (iter 14) |
| `NotificationRouter` | Implemented (iter 14) |
| `NotificationEvent` dataclass | Implemented (iter 14) |
| `InAppBackend` | Implemented (iter 14) |
| `Notification` model (in-app) | Implemented (iter 14) |
| ARQ `notify_job` dispatch | Implemented (iter 14) |
| `SmtpBackend` | **Missing** |
| Email templates | **Missing** |
| SMTP settings | **Missing** |
| `aiosmtplib` dependency | **Not installed** |

---

## Scope decisions

| Item | Decision |
|---|---|
| Transport | SMTP only. No SES, SendGrid, or other providers. |
| SMTP library | `aiosmtplib` — async, fits the existing async architecture |
| Templates | Jinja2 with a **separate** `Environment` / `FileSystemLoader` (not the UI Jinja2 env) |
| Template format | HTML + plain-text fallback for every event type |
| Error handling | SMTP errors are logged but **never block** dispatch to other backends (ADR-0009 error isolation) |
| Retry logic | None in v1 — failed emails are logged and dropped |
| WebhookBackend | Out of scope (future iteration) |

---

## 1. Dependency

### Add `aiosmtplib` to `pyproject.toml`

```toml
[project]
dependencies = [
    ...,
    "aiosmtplib>=3.0",
]
```

---

## 2. SmtpBackend class

### File: `src/trevor/notifications/smtp_backend.py`

Implements the `NotificationBackend` protocol from iteration 14.

```python
"""SMTP notification backend — sends email via karectl SMTP service."""

from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from trevor.notifications.base import NotificationBackend, NotificationEvent
from trevor.settings import Settings

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


class SmtpBackend(NotificationBackend):
    """Send email notifications via SMTP."""

    def __init__(self, settings: Settings) -> None:
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.from_address = settings.smtp_from_address
        self.use_tls = settings.smtp_use_tls
        self.username = settings.smtp_username
        self.password = settings.smtp_password

        self._jinja_env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    async def send(self, event: NotificationEvent, recipients: list[str]) -> None:
        """Send email to all recipients. Errors are logged, never raised."""
        if not recipients:
            return

        try:
            subject = self._render_template(event, "subject.txt").strip()
            body_html = self._render_template(event, "body.html")
            body_text = self._render_template(event, "body.txt")
        except Exception:
            logger.exception("smtp: failed to render template for %s", event.event_type)
            return

        for recipient in recipients:
            try:
                msg = self._build_message(subject, body_html, body_text, recipient)
                await aiosmtplib.send(
                    msg,
                    hostname=self.host,
                    port=self.port,
                    start_tls=self.use_tls,
                    username=self.username or None,
                    password=self.password or None,
                )
                logger.info("smtp: sent %s to %s", event.event_type, recipient)
            except Exception:
                logger.exception("smtp: failed to send %s to %s", event.event_type, recipient)

    def _render_template(self, event: NotificationEvent, filename: str) -> str:
        """Render a template file from the event's template directory."""
        template = self._jinja_env.get_template(f"{event.event_type}/{filename}")
        return template.render(**event.context)

    def _build_message(
        self, subject: str, body_html: str, body_text: str, recipient: str
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_address
        msg["To"] = recipient
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")
        return msg
```

Key design points:

- One `aiosmtplib.send()` call per recipient (no BCC — each user gets their own email).
- Template rendering happens once; only the send loop is per-recipient.
- All exceptions are caught and logged — `send()` never raises.

---

## 3. Email template structure

```
src/trevor/notifications/templates/
  request.submitted/
    subject.txt
    body.html
    body.txt
  agent_review.ready/
    subject.txt
    body.html
    body.txt
  request.changes_requested/
    subject.txt
    body.html
    body.txt
  request.approved/
    subject.txt
    body.html
    body.txt
  request.rejected/
    subject.txt
    body.html
    body.txt
  request.released/
    subject.txt
    body.html
    body.txt
  presigned_url.expiring/
    subject.txt
    body.html
    body.txt
```

Each directory name matches the `event_type` string from `NotificationEvent`.

---

## 4. Template context

All templates receive a base set of variables. Event-specific variables are added on top.

### Base context (all templates)

| Variable | Type | Source |
|---|---|---|
| `request_title` | `str` | `AirlockRequest.title` |
| `request_id` | `str` | `AirlockRequest.id` (UUID as string) |
| `project_name` | `str` | `Project.display_name` |
| `recipient_name` | `str` | `User.given_name` or `User.username` |
| `trevor_base_url` | `str` | From settings (`trevor_base_url`) |

### Event-specific context

| Event type | Extra variables |
|---|---|
| `request.submitted` | `submitter_name` |
| `agent_review.ready` | `object_count`, `risk_summary` |
| `request.changes_requested` | `reviewer_name`, `feedback_summary` |
| `request.approved` | `reviewer_name`, `approver_count` |
| `request.rejected` | `reviewer_name`, `rejection_reason` |
| `request.released` | `presigned_url`, `expiry_hours`, `object_count` |
| `presigned_url.expiring` | `presigned_url`, `hours_remaining` |

---

## 5. All 7 email templates

### 5.1 `request.submitted`

**Subject**: `[trevor] Request "{{ request_title }}" submitted for review`

**Key content**: Informs checkers that a new request has been submitted and is pending agent review. Links to the review queue.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `submitter_name`, `trevor_base_url`

### 5.2 `agent_review.ready`

**Subject**: `[trevor] Agent review complete for "{{ request_title }}"`

**Key content**: Notifies checkers that the automated agent review is finished and human review can begin. Includes object count and risk summary.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `object_count`, `risk_summary`, `trevor_base_url`

### 5.3 `request.changes_requested`

**Subject**: `[trevor] Changes requested on "{{ request_title }}"`

**Key content**: Tells the researcher that a checker has requested changes. Includes the reviewer's name and a summary of feedback. Links to the request detail page.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `reviewer_name`, `feedback_summary`, `trevor_base_url`

### 5.4 `request.approved`

**Subject**: `[trevor] Request "{{ request_title }}" approved`

**Key content**: Notifies the researcher (and project lead) that the request has been approved by the required number of reviewers.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `reviewer_name`, `approver_count`, `trevor_base_url`

### 5.5 `request.rejected`

**Subject**: `[trevor] Request "{{ request_title }}" rejected`

**Key content**: Informs the researcher that the request has been rejected. Includes the reviewer's name and rejection reason.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `reviewer_name`, `rejection_reason`, `trevor_base_url`

### 5.6 `request.released`

**Subject**: `[trevor] Outputs released for "{{ request_title }}"`

**Key content**: Tells the researcher that outputs are available for download. Contains the pre-signed URL prominently with an expiry notice.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `presigned_url`, `expiry_hours`, `object_count`, `trevor_base_url`

### 5.7 `presigned_url.expiring`

**Subject**: `[trevor] Download link expiring for "{{ request_title }}"`

**Key content**: Warns that the pre-signed download URL is about to expire. Includes hours remaining and the URL.

**Variables**: `request_title`, `request_id`, `project_name`, `recipient_name`, `presigned_url`, `hours_remaining`, `trevor_base_url`

---

## 6. Settings additions

### Changes to `src/trevor/settings.py`

```python
# Email / SMTP
smtp_host: str = "localhost"
smtp_port: int = 587
smtp_from_address: str = "trevor@karectl.example"
smtp_use_tls: bool = True
smtp_username: str = ""
smtp_password: str = ""
email_notifications_enabled: bool = False
trevor_base_url: str = "http://localhost:8000"
```

| Setting | Env var | Default | Notes |
|---|---|---|---|
| `smtp_host` | `SMTP_HOST` | `localhost` | karectl SMTP service address |
| `smtp_port` | `SMTP_PORT` | `587` | Standard submission port |
| `smtp_from_address` | `SMTP_FROM_ADDRESS` | `trevor@karectl.example` | Envelope From |
| `smtp_use_tls` | `SMTP_USE_TLS` | `True` | STARTTLS on connect |
| `smtp_username` | `SMTP_USERNAME` | `""` | Optional SMTP auth |
| `smtp_password` | `SMTP_PASSWORD` | `""` | Optional SMTP auth |
| `email_notifications_enabled` | `EMAIL_NOTIFICATIONS_ENABLED` | `False` | Gate for SmtpBackend registration |
| `trevor_base_url` | `TREVOR_BASE_URL` | `http://localhost:8000` | Used in email links |

---

## 7. Registration in NotificationRouter

### Changes to `src/trevor/notifications/__init__.py` (or wherever router is assembled)

```python
def build_notification_router(settings: Settings) -> NotificationRouter:
    backends: list[NotificationBackend] = []

    # In-app is always enabled
    backends.append(InAppBackend())

    # SMTP backend — only when enabled
    if settings.email_notifications_enabled:
        backends.append(SmtpBackend(settings))

    return NotificationRouter(backends=backends)
```

---

## 8. Changes to app.py / worker.py

### `src/trevor/app.py`

No changes required if the `NotificationRouter` is already built in lifespan (iter 14). The `build_notification_router` factory handles conditional registration.

### `src/trevor/worker.py`

The ARQ worker's `on_startup` must also build the router with SmtpBackend when enabled, so that `notify_job` dispatches emails from the worker process:

```python
async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ctx["settings"] = settings
    ctx["session_factory"] = async_sessionmaker(get_engine(settings.database_url))
    ctx["notification_router"] = build_notification_router(settings)  # picks up SmtpBackend
```

No other changes to `notify_job` — it already calls `router.dispatch()` which fans out to all registered backends.

---

## 9. Test plan

### File: `tests/test_smtp_backend.py`

All tests mock `aiosmtplib.send` — no real SMTP server needed.

| Test | Validates |
|---|---|
| `test_smtp_send_basic` | `aiosmtplib.send` called once per recipient with correct hostname/port |
| `test_smtp_message_structure` | `EmailMessage` has Subject, From, To, plain-text body, HTML alternative |
| `test_smtp_template_rendering` | Each of the 7 templates renders without error given valid context |
| `test_smtp_subject_lines` | Subject lines match expected format for each event type |
| `test_smtp_plain_text_fallback` | Plain-text body is present and non-empty |
| `test_smtp_html_content` | HTML body contains expected key content (e.g. presigned URL in released) |
| `test_smtp_multiple_recipients` | `aiosmtplib.send` called N times for N recipients |
| `test_smtp_empty_recipients` | `aiosmtplib.send` not called when recipients list is empty |
| `test_smtp_send_failure_logged` | SMTP exception is caught, logged, does not raise |
| `test_smtp_template_error_logged` | Missing template variable is caught, logged, does not raise |
| `test_smtp_no_auth_when_empty` | `username`/`password` passed as `None` when settings are empty strings |
| `test_build_router_email_enabled` | `SmtpBackend` in router backends when `email_notifications_enabled=True` |
| `test_build_router_email_disabled` | `SmtpBackend` absent when `email_notifications_enabled=False` |

---

## New / modified files

```
src/trevor/
  notifications/
    smtp_backend.py                    # NEW — SmtpBackend class
    __init__.py                        # MODIFIED — build_notification_router adds SmtpBackend
    templates/
      request.submitted/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
      agent_review.ready/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
      request.changes_requested/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
      request.approved/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
      request.rejected/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
      request.released/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
      presigned_url.expiring/
        subject.txt                    # NEW
        body.html                      # NEW
        body.txt                       # NEW
  settings.py                          # MODIFIED — SMTP + email settings
  worker.py                            # MODIFIED — build router with SmtpBackend
pyproject.toml                         # MODIFIED — add aiosmtplib dependency
tests/
  test_smtp_backend.py                 # NEW — 13 tests
```

---

## Implementation order

1. `pyproject.toml` — add `aiosmtplib>=3.0`, `uv sync`
2. `src/trevor/settings.py` — add SMTP and email settings
3. `src/trevor/notifications/smtp_backend.py` — `SmtpBackend` class
4. `src/trevor/notifications/templates/` — create all 7 template directories with `subject.txt`, `body.html`, `body.txt`
5. `src/trevor/notifications/__init__.py` — update `build_notification_router` to conditionally add `SmtpBackend`
6. `src/trevor/worker.py` — ensure worker startup builds router with SMTP support
7. `tests/test_smtp_backend.py` — all unit tests
8. Run `uv run ruff check . && uv run ruff format --check . && uv run pytest -v`
