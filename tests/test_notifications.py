"""Tests for notification endpoints and service (iteration 14)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.notification import Notification, NotificationEventType
from trevor.models.project import ProjectMembership, ProjectRole
from trevor.models.request import AirlockDirection, AirlockRequest, AirlockRequestStatus
from trevor.models.user import User
from trevor.services import notification_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(session: AsyncSession, suffix: str) -> User:
    u = User(
        keycloak_sub=f"sub-{suffix}",
        username=f"user-{suffix}",
        email=f"{suffix}@example.com",
        given_name="Test",
        family_name="User",
        affiliation="Org",
        crd_name=f"user-{suffix}",
        active=True,
    )
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


async def _make_notification(
    session: AsyncSession,
    user_id: uuid.UUID,
    read: bool = False,
) -> Notification:
    n = Notification(
        user_id=user_id,
        event_type=NotificationEventType.REQUEST_SUBMITTED,
        title="Test notification",
        body="A request was submitted.",
        read=read,
    )
    session.add(n)
    await session.commit()
    await session.refresh(n)
    return n


# ---------------------------------------------------------------------------
# Notification service unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_app_backend_creates_rows(db_session: AsyncSession, sample_user: User):
    """InAppBackend.send() writes one Notification row per recipient."""
    backend = notification_service.InAppBackend()
    event = notification_service.NotificationEvent(
        event_type=NotificationEventType.REQUEST_SUBMITTED,
        title="T",
        body="B",
        request_id=None,
        recipient_user_ids=[sample_user.id],
    )
    await backend.send(event, db_session)
    await db_session.commit()

    from sqlmodel import select

    result = await db_session.exec(
        select(Notification).where(Notification.user_id == sample_user.id)
    )
    rows = result.all()
    assert len(rows) == 1
    assert rows[0].event_type == NotificationEventType.REQUEST_SUBMITTED
    assert rows[0].read is False


@pytest.mark.asyncio
async def test_notification_router_dispatch(db_session: AsyncSession, sample_user: User):
    """NotificationRouter dispatches to all backends."""
    router = notification_service.NotificationRouter([notification_service.InAppBackend()])
    event = notification_service.NotificationEvent(
        event_type=NotificationEventType.REQUEST_APPROVED,
        title="Approved",
        body="Your request was approved.",
        recipient_user_ids=[sample_user.id],
    )
    await router.dispatch(event, db_session)
    await db_session.commit()

    from sqlmodel import select

    result = await db_session.exec(
        select(Notification).where(Notification.user_id == sample_user.id)
    )
    assert len(result.all()) == 1


@pytest.mark.asyncio
async def test_notification_router_no_recipients_skips(
    db_session: AsyncSession,
):
    """Router does not call backend when recipient list is empty."""
    called = []

    class _CountingBackend:
        async def send(self, event, session):
            called.append(event)

    router = notification_service.NotificationRouter([_CountingBackend()])
    event = notification_service.NotificationEvent(
        event_type=NotificationEventType.REQUEST_SUBMITTED,
        title="T",
        body="B",
        recipient_user_ids=[],
    )
    await router.dispatch(event, db_session)
    assert called == []


@pytest.mark.asyncio
async def test_get_recipients_checker_events(
    db_session: AsyncSession,
    sample_user: User,
    sample_project,
):
    """get_recipients returns checkers for AGENT_REVIEW_READY event."""
    checker = await _make_user(db_session, "chk-recip")
    membership = ProjectMembership(
        user_id=checker.id,
        project_id=sample_project.id,
        role=ProjectRole.OUTPUT_CHECKER,
        assigned_by=sample_user.id,
    )
    db_session.add(membership)
    await db_session.commit()

    req = AirlockRequest(
        project_id=sample_project.id,
        submitted_by=sample_user.id,
        title="Test req",
        purpose="Research",
        status=AirlockRequestStatus.HUMAN_REVIEW,
        direction=AirlockDirection.EGRESS,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    recipients = await notification_service.get_recipients(
        NotificationEventType.AGENT_REVIEW_READY, req, db_session
    )
    assert checker.id in recipients


@pytest.mark.asyncio
async def test_get_recipients_researcher_events(
    db_session: AsyncSession,
    sample_user: User,
    sample_project,
):
    """get_recipients returns submitter for REQUEST_APPROVED event."""
    req = AirlockRequest(
        project_id=sample_project.id,
        submitted_by=sample_user.id,
        title="Test req",
        purpose="Research",
        status=AirlockRequestStatus.APPROVED,
        direction=AirlockDirection.EGRESS,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    recipients = await notification_service.get_recipients(
        NotificationEventType.REQUEST_APPROVED, req, db_session
    )
    assert recipients == [sample_user.id]


@pytest.mark.asyncio
async def test_create_event_builds_correct_text(
    db_session: AsyncSession,
    sample_user: User,
    sample_project,
):
    """create_event interpolates request title into title/body strings."""
    req = AirlockRequest(
        project_id=sample_project.id,
        submitted_by=sample_user.id,
        title="My Output",
        purpose="Research",
        status=AirlockRequestStatus.SUBMITTED,
        direction=AirlockDirection.EGRESS,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    event = await notification_service.create_event(
        NotificationEventType.REQUEST_SUBMITTED, req, db_session
    )
    assert "My Output" in event.title
    assert "My Output" in event.body
    assert event.request_id == req.id


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unread_count_empty(client: AsyncClient, db_session: AsyncSession):
    """GET /notifications/unread-count returns 0 when no notifications."""
    resp = await client.get("/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


@pytest.mark.asyncio
async def test_unread_count_with_notifications(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """GET /notifications/unread-count returns correct count."""
    # The default test client user is the "testuser" injected by DEV_AUTH_BYPASS.
    # We need the actual user ID — get it via /users/me.
    me_resp = await client.get("/users/me")
    assert me_resp.status_code == 200
    user_id = uuid.UUID(me_resp.json()["id"])

    await _make_notification(db_session, user_id, read=False)
    await _make_notification(db_session, user_id, read=True)

    resp = await client.get("/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


@pytest.mark.asyncio
async def test_list_notifications_empty(client: AsyncClient):
    resp = await client.get("/notifications")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_notifications_returns_own_only(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """GET /notifications only returns notifications for the authenticated user."""
    me_resp = await client.get("/users/me")
    user_id = uuid.UUID(me_resp.json()["id"])

    other = await _make_user(db_session, "other-list")
    await _make_notification(db_session, user_id)
    await _make_notification(db_session, other.id)

    resp = await client.get("/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == str(user_id)


@pytest.mark.asyncio
async def test_list_notifications_unread_only(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """GET /notifications?unread_only=true filters to unread."""
    me_resp = await client.get("/users/me")
    user_id = uuid.UUID(me_resp.json()["id"])

    await _make_notification(db_session, user_id, read=False)
    await _make_notification(db_session, user_id, read=True)

    resp = await client.get("/notifications?unread_only=true")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["read"] is False


@pytest.mark.asyncio
async def test_mark_read(client: AsyncClient, db_session: AsyncSession):
    """PATCH /notifications/{id}/read marks the notification as read."""
    me_resp = await client.get("/users/me")
    user_id = uuid.UUID(me_resp.json()["id"])
    n = await _make_notification(db_session, user_id, read=False)

    resp = await client.patch(f"/notifications/{n.id}/read")
    assert resp.status_code == 200
    assert resp.json()["read"] is True


@pytest.mark.asyncio
async def test_mark_read_other_user_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """PATCH /notifications/{id}/read returns 404 for another user's notification."""
    other = await _make_user(db_session, "other-mark")
    n = await _make_notification(db_session, other.id)

    resp = await client.patch(f"/notifications/{n.id}/read")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mark_all_read(client: AsyncClient, db_session: AsyncSession):
    """POST /notifications/mark-all-read marks all unread as read."""
    me_resp = await client.get("/users/me")
    user_id = uuid.UUID(me_resp.json()["id"])

    await _make_notification(db_session, user_id, read=False)
    await _make_notification(db_session, user_id, read=False)

    resp = await client.post("/notifications/mark-all-read")
    assert resp.status_code == 204

    count_resp = await client.get("/notifications/unread-count")
    assert count_resp.json()["count"] == 0
