"""Tests for SSE endpoints and sse.py helpers."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from trevor.sse import format_fragment_event

# ---------------------------------------------------------------------------
# Unit tests: format_fragment_event
# ---------------------------------------------------------------------------


def test_format_fragment_event_single_line():
    html = '<span id="foo">hello</span>'
    event = format_fragment_event(html)
    assert event.startswith("event: datastar-merge-fragments\n")
    assert f"data: fragments {html}\n" in event
    assert event.endswith("\n\n")


def test_format_fragment_event_multi_line():
    html = '<div id="foo">\n  <span>hello</span>\n</div>'
    event = format_fragment_event(html)
    lines = event.splitlines()
    assert lines[0] == "event: datastar-merge-fragments"
    assert lines[1] == 'data: fragments <div id="foo">'
    assert lines[2] == "data:   <span>hello</span>"
    assert lines[3] == "data: </div>"


def test_format_fragment_event_preserves_id():
    html = '<span id="my-target">content</span>'
    event = format_fragment_event(html)
    assert 'id="my-target"' in event


# ---------------------------------------------------------------------------
# One-shot SSE patch helper
# ---------------------------------------------------------------------------


def _one_shot_patch():
    """Patch sse_stream to yield exactly one event then exit."""
    from trevor import sse as sse_module

    async def one_shot(request, poll_fn, **kw):
        html = await poll_fn()
        yield sse_module.format_fragment_event(html)

    return patch("trevor.routers.sse.sse_stream", side_effect=one_shot)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_request_status_content_type(researcher_setup):
    """SSE request status endpoint returns text/event-stream."""
    _client, project_id = researcher_setup

    r = await _client.post(
        "/requests",
        json={"title": "SSE Test", "project_id": str(project_id), "direction": "egress"},
    )
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]

    with _one_shot_patch():
        resp = await _client.get(f"/ui/sse/requests/{req_id}/status")

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_sse_request_status_first_event(researcher_setup):
    """First SSE event contains request-status-badge with current status."""
    _client, project_id = researcher_setup

    r = await _client.post(
        "/requests",
        json={"title": "SSE Test2", "project_id": str(project_id), "direction": "egress"},
    )
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]

    with _one_shot_patch():
        resp = await _client.get(f"/ui/sse/requests/{req_id}/status")

    body = resp.content.decode()
    assert "event: datastar-merge-fragments" in body
    assert "request-status-badge" in body
    assert "DRAFT" in body


@pytest.mark.asyncio
async def test_sse_request_status_not_found(client):
    """404 for non-existent request ID."""
    resp = await client.get(f"/ui/sse/requests/{uuid.uuid4()}/status")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sse_request_status_requires_auth():
    """401 when no auth token provided."""
    from httpx import ASGITransport, AsyncClient

    from trevor.app import create_app
    from trevor.settings import Settings

    settings = Settings(dev_auth_bypass=False, database_url="sqlite+aiosqlite:///:memory:")
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/ui/sse/requests/{uuid.uuid4()}/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sse_notification_count_returns_ok(researcher_setup):
    """Notification count SSE returns 200 and contains notification-count element."""
    _client, _ = researcher_setup

    with _one_shot_patch():
        resp = await _client.get("/ui/sse/notifications/count")

    assert resp.status_code == 200
    assert "notification-count" in resp.content.decode()


@pytest.mark.asyncio
async def test_sse_review_queue_requires_checker(researcher_setup):
    """Researcher (no checker role) gets 403 on review queue SSE."""
    _client, _ = researcher_setup
    resp = await _client.get("/ui/sse/review/queue-count")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sse_review_queue_admin_allowed(admin_client):
    """Admin bypasses checker role check and gets a valid SSE stream."""
    with _one_shot_patch():
        resp = await admin_client.get("/ui/sse/review/queue-count")

    assert resp.status_code == 200
    assert "review-queue-count" in resp.content.decode()
