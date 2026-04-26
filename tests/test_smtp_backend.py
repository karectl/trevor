"""Tests for SmtpBackend and get_router email integration."""

from __future__ import annotations

import uuid
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trevor.services.notification_service import (
    InAppBackend,
    NotificationEvent,
    SmtpBackend,
    get_router,
)
from trevor.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SMTP_PATCH = "trevor.services.notification_service.aiosmtplib.send"

EVENT_TYPES = [
    "request.submitted",
    "agent_review.ready",
    "request.changes_requested",
    "request.approved",
    "request.rejected",
    "request.released",
    "presigned_url.expiring_soon",
]

BASE_CTX = {
    "request_title": "Test Output Package",
    "request_id": str(uuid.uuid4()),
    "project_name": "Interstellar",
    "recipient_name": "Alice",
    "trevor_base_url": "http://localhost:8000",
    "recipient_emails": ["alice@example.com"],
}

EXTRA_CTX: dict[str, dict] = {
    "request.submitted": {"submitter_name": "Bob"},
    "agent_review.ready": {"object_count": 3, "risk_summary": "Low risk"},
    "request.changes_requested": {
        "reviewer_name": "Carol",
        "feedback_summary": "Remove row counts",
    },
    "request.approved": {"reviewer_name": "Carol", "approver_count": 2},
    "request.rejected": {
        "reviewer_name": "Carol",
        "rejection_reason": "Identifiable data present",
    },
    "request.released": {
        "presigned_url": "https://s3.example.com/output.zip?sig=abc",
        "expiry_hours": 168,
        "object_count": 2,
    },
    "presigned_url.expiring_soon": {
        "presigned_url": "https://s3.example.com/output.zip?sig=abc",
        "hours_remaining": 24,
    },
}


def make_event(event_type: str, emails: list[str] | None = None) -> NotificationEvent:
    ctx = {**BASE_CTX, **EXTRA_CTX[event_type]}
    if emails is not None:
        ctx["recipient_emails"] = emails
    return NotificationEvent(
        event_type=event_type,
        title="Test title",
        body="Test body",
        request_id=uuid.uuid4(),
        recipient_user_ids=[uuid.uuid4()],
        metadata=ctx,
    )


def smtp_settings(**overrides) -> Settings:
    defaults = dict(
        dev_auth_bypass=True,
        database_url="sqlite+aiosqlite:///:memory:",
        email_notifications_enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_from_address="trevor@karectl.example",
        smtp_use_tls=True,
        smtp_username="user",
        smtp_password="pass",
        trevor_base_url="http://localhost:8000",
    )
    return Settings(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smtp_send_basic():
    """aiosmtplib.send called once per recipient with correct host/port."""
    backend = SmtpBackend(smtp_settings())
    event = make_event("request.submitted", emails=["alice@example.com"])

    with patch(_SMTP_PATCH, new_callable=AsyncMock) as mock_send:
        await backend.send(event, MagicMock())

    mock_send.assert_awaited_once()
    _, kwargs = mock_send.call_args
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587


@pytest.mark.asyncio
async def test_smtp_message_structure():
    """EmailMessage has Subject, From, To, plain-text and HTML parts."""
    backend = SmtpBackend(smtp_settings())
    event = make_event("request.submitted", emails=["alice@example.com"])
    captured: list[EmailMessage] = []

    async def fake_send(msg, **kwargs):
        captured.append(msg)

    with patch(_SMTP_PATCH, side_effect=fake_send):
        await backend.send(event, MagicMock())

    assert len(captured) == 1
    msg = captured[0]
    assert msg["Subject"]
    assert msg["From"] == "trevor@karectl.example"
    assert msg["To"] == "alice@example.com"
    # Check multipart structure
    content_types = {part.get_content_type() for part in msg.iter_parts()}
    assert "text/plain" in content_types
    assert "text/html" in content_types


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", EVENT_TYPES)
async def test_smtp_template_rendering(event_type: str):
    """All 7 templates render without error given valid context."""
    backend = SmtpBackend(smtp_settings())
    event = make_event(event_type)

    with patch(_SMTP_PATCH, new_callable=AsyncMock):
        await backend.send(event, MagicMock())  # no exception = pass


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", EVENT_TYPES)
async def test_smtp_subject_lines(event_type: str):
    """Subject lines are non-empty and contain the request title."""
    backend = SmtpBackend(smtp_settings())
    event = make_event(event_type)
    captured: list[EmailMessage] = []

    async def fake_send(msg, **kwargs):
        captured.append(msg)

    with patch(_SMTP_PATCH, side_effect=fake_send):
        await backend.send(event, MagicMock())

    assert captured, f"No email sent for {event_type}"
    subject = captured[0]["Subject"]
    assert subject, f"Empty subject for {event_type}"
    assert "Test Output Package" in subject, f"Request title missing from subject for {event_type}"


@pytest.mark.asyncio
async def test_smtp_plain_text_fallback():
    """Plain-text body is non-empty."""
    backend = SmtpBackend(smtp_settings())
    event = make_event("request.approved")
    captured: list[EmailMessage] = []

    async def fake_send(msg, **kwargs):
        captured.append(msg)

    with patch(_SMTP_PATCH, side_effect=fake_send):
        await backend.send(event, MagicMock())

    plain = next(
        (
            p.get_payload(decode=True).decode()
            for p in captured[0].iter_parts()
            if p.get_content_type() == "text/plain"
        ),
        None,
    )
    assert plain and len(plain) > 10


@pytest.mark.asyncio
async def test_smtp_html_content():
    """HTML body for request.released contains the presigned URL."""
    backend = SmtpBackend(smtp_settings())
    event = make_event("request.released")
    captured: list[EmailMessage] = []

    async def fake_send(msg, **kwargs):
        captured.append(msg)

    with patch(_SMTP_PATCH, side_effect=fake_send):
        await backend.send(event, MagicMock())

    html = next(
        (
            p.get_payload(decode=True).decode()
            for p in captured[0].iter_parts()
            if p.get_content_type() == "text/html"
        ),
        None,
    )
    assert html
    assert "https://s3.example.com/output.zip" in html


@pytest.mark.asyncio
async def test_smtp_multiple_recipients():
    """aiosmtplib.send called N times for N recipients."""
    backend = SmtpBackend(smtp_settings())
    event = make_event(
        "request.submitted",
        emails=["a@example.com", "b@example.com", "c@example.com"],
    )

    with patch(_SMTP_PATCH, new_callable=AsyncMock) as mock_send:
        await backend.send(event, MagicMock())

    assert mock_send.await_count == 3


@pytest.mark.asyncio
async def test_smtp_empty_recipients():
    """aiosmtplib.send not called when recipient list is empty."""
    backend = SmtpBackend(smtp_settings())
    event = make_event("request.submitted", emails=[])

    with patch(_SMTP_PATCH, new_callable=AsyncMock) as mock_send:
        await backend.send(event, MagicMock())

    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_smtp_send_failure_logged(caplog):
    """SMTP exception caught, logged, does not raise."""
    import logging

    backend = SmtpBackend(smtp_settings())
    event = make_event("request.submitted", emails=["alice@example.com"])

    with patch(
        _SMTP_PATCH,
        side_effect=ConnectionRefusedError("connection refused"),
    ):
        with caplog.at_level(logging.ERROR):
            await backend.send(event, MagicMock())  # must not raise

    assert any("failed sending" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_smtp_template_error_logged(caplog):
    """Missing template renders error logged, does not raise."""
    import logging

    backend = SmtpBackend(smtp_settings())
    # Use unknown event_type so template lookup fails
    event = NotificationEvent(
        event_type="nonexistent.event",
        title="x",
        body="x",
        request_id=uuid.uuid4(),
        recipient_user_ids=[uuid.uuid4()],
        metadata={"recipient_emails": ["alice@example.com"]},
    )

    with patch(_SMTP_PATCH, new_callable=AsyncMock) as mock_send:
        with caplog.at_level(logging.ERROR):
            await backend.send(event, MagicMock())

    mock_send.assert_not_awaited()
    assert any("template render failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_smtp_no_auth_when_empty():
    """username/password passed as None when settings are empty strings."""
    backend = SmtpBackend(smtp_settings(smtp_username="", smtp_password=""))
    assert backend.username is None
    assert backend.password is None


def test_build_router_email_enabled():
    """SmtpBackend present in router when email_notifications_enabled=True."""
    settings = smtp_settings(email_notifications_enabled=True)
    router = get_router(settings)
    backend_types = [type(b) for b in router._backends]
    assert InAppBackend in backend_types
    assert SmtpBackend in backend_types


def test_build_router_email_disabled():
    """SmtpBackend absent when email_notifications_enabled=False."""
    settings = smtp_settings(email_notifications_enabled=False)
    router = get_router(settings)
    backend_types = [type(b) for b in router._backends]
    assert InAppBackend in backend_types
    assert SmtpBackend not in backend_types
