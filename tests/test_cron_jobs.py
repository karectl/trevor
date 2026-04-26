"""Tests for iteration-17 ARQ cron jobs: url_expiry_warning_job, stuck_request_alert_job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.notification import Notification, NotificationEventType
from trevor.models.project import Project, ProjectMembership, ProjectRole
from trevor.models.release import ReleaseRecord
from trevor.models.request import AirlockDirection, AirlockRequest, AirlockRequestStatus
from trevor.models.user import User
from trevor.settings import Settings
from trevor.worker import stuck_request_alert_job, url_expiry_warning_job

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**kwargs) -> Settings:
    base = dict(
        dev_auth_bypass=True,
        database_url="sqlite+aiosqlite:///:memory:",
        notifications_enabled=True,
        url_expiry_warning_hours=48,
        stuck_request_hours=72,
    )
    base.update(kwargs)
    return Settings(**base)


async def _make_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


async def _make_ctx(engine, settings: Settings | None = None):
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return {
        "session_factory": factory,
        "settings": settings or _settings(),
    }


async def _seed_user(session: AsyncSession, username: str = "u1") -> User:
    user = User(
        keycloak_sub=f"sub-{username}",
        username=username,
        email=f"{username}@example.com",
        given_name="A",
        family_name="B",
        affiliation="Org",
        crd_name=username,
        active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _seed_project(session: AsyncSession) -> Project:
    project = Project(crd_name="proj-1", display_name="Project One")
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def _seed_membership(
    session: AsyncSession,
    user: User,
    project: Project,
    role: ProjectRole = ProjectRole.OUTPUT_CHECKER,
) -> ProjectMembership:
    m = ProjectMembership(user_id=user.id, project_id=project.id, role=role, assigned_by=user.id)
    session.add(m)
    await session.commit()
    return m


async def _seed_request(
    session: AsyncSession,
    project: Project,
    user: User,
    status: AirlockRequestStatus = AirlockRequestStatus.HUMAN_REVIEW,
    updated_at: datetime | None = None,
) -> AirlockRequest:
    req = AirlockRequest(
        title="Test Request",
        project_id=project.id,
        submitted_by=user.id,
        direction=AirlockDirection.EGRESS,
        status=status,
    )
    session.add(req)
    await session.commit()
    await session.refresh(req)
    if updated_at is not None:
        req.updated_at = updated_at
        session.add(req)
        await session.commit()
        await session.refresh(req)
    return req


async def _seed_release(
    session: AsyncSession,
    request: AirlockRequest,
    url_expires_at: datetime | None = None,
    expiry_warned_at: datetime | None = None,
) -> ReleaseRecord:
    rec = ReleaseRecord(
        request_id=request.id,
        crate_storage_key="key",
        crate_checksum_sha256="abc",
        presigned_url="http://example.com/file.zip",
        url_expires_at=url_expires_at,
        expiry_warned_at=expiry_warned_at,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


# ---------------------------------------------------------------------------
# url_expiry_warning_job tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_expiry_warning_sends_notification():
    """Records expiring within warning window get a notification."""
    engine = await _make_engine()
    settings = _settings(url_expiry_warning_hours=48)
    ctx = await _make_ctx(engine, settings)
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-1")
        project = await _seed_project(session)
        req = await _seed_request(session, project, researcher, AirlockRequestStatus.RELEASED)
        now = datetime.now(UTC).replace(tzinfo=None)
        # Expires in 24h — within the 48h warning window
        await _seed_release(session, req, url_expires_at=now + timedelta(hours=24))

    await url_expiry_warning_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 1
    assert notifications[0].event_type == NotificationEventType.PRESIGNED_URL_EXPIRING


@pytest.mark.asyncio
async def test_url_expiry_warning_sets_expiry_warned_at():
    """expiry_warned_at is set after dispatching warning."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine)
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-2")
        project = await _seed_project(session)
        req = await _seed_request(session, project, researcher, AirlockRequestStatus.RELEASED)
        now = datetime.now(UTC).replace(tzinfo=None)
        rec = await _seed_release(session, req, url_expires_at=now + timedelta(hours=12))

    await url_expiry_warning_job(ctx)

    async with factory() as session:
        updated = await session.get(ReleaseRecord, rec.id)

    assert updated is not None
    assert updated.expiry_warned_at is not None


@pytest.mark.asyncio
async def test_url_expiry_warning_idempotent():
    """Already-warned records are not re-notified."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine)
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-3")
        project = await _seed_project(session)
        req = await _seed_request(session, project, researcher, AirlockRequestStatus.RELEASED)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_release(
            session,
            req,
            url_expires_at=now + timedelta(hours=12),
            expiry_warned_at=now - timedelta(hours=1),  # already warned
        )

    await url_expiry_warning_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 0


@pytest.mark.asyncio
async def test_url_expiry_warning_skips_already_expired():
    """Records whose URL has already expired are not warned."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine)
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-4")
        project = await _seed_project(session)
        req = await _seed_request(session, project, researcher, AirlockRequestStatus.RELEASED)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_release(session, req, url_expires_at=now - timedelta(hours=1))

    await url_expiry_warning_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 0


@pytest.mark.asyncio
async def test_url_expiry_warning_skips_outside_window():
    """Records expiring after the warning window are not warned."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine, _settings(url_expiry_warning_hours=48))
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-5")
        project = await _seed_project(session)
        req = await _seed_request(session, project, researcher, AirlockRequestStatus.RELEASED)
        now = datetime.now(UTC).replace(tzinfo=None)
        # Expires in 72h — outside the 48h window
        await _seed_release(session, req, url_expires_at=now + timedelta(hours=72))

    await url_expiry_warning_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 0


@pytest.mark.asyncio
async def test_url_expiry_warning_notifications_disabled():
    """Job is a no-op when notifications_enabled=False."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine, _settings(notifications_enabled=False))
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-6")
        project = await _seed_project(session)
        req = await _seed_request(session, project, researcher, AirlockRequestStatus.RELEASED)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_release(session, req, url_expires_at=now + timedelta(hours=12))

    await url_expiry_warning_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 0


# ---------------------------------------------------------------------------
# stuck_request_alert_job tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stuck_request_alert_sends_notification():
    """Requests stuck beyond SLA get a notification to checkers."""
    engine = await _make_engine()
    settings = _settings(stuck_request_hours=72)
    ctx = await _make_ctx(engine, settings)
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-7")
        checker = await _seed_user(session, "checker-7")
        project = await _seed_project(session)
        await _seed_membership(session, checker, project, ProjectRole.OUTPUT_CHECKER)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_request(
            session,
            project,
            researcher,
            AirlockRequestStatus.HUMAN_REVIEW,
            updated_at=now - timedelta(hours=100),  # stuck for 100h > 72h threshold
        )

    await stuck_request_alert_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 1
    assert notifications[0].event_type == NotificationEventType.REQUEST_STUCK


@pytest.mark.asyncio
async def test_stuck_request_alert_not_sent_for_recent():
    """Recent requests (within SLA) do not trigger stuck alert."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine, _settings(stuck_request_hours=72))
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-8")
        checker = await _seed_user(session, "checker-8")
        project = await _seed_project(session)
        await _seed_membership(session, checker, project, ProjectRole.OUTPUT_CHECKER)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_request(
            session,
            project,
            researcher,
            AirlockRequestStatus.HUMAN_REVIEW,
            updated_at=now - timedelta(hours=10),  # recent
        )

    await stuck_request_alert_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 0


@pytest.mark.asyncio
async def test_stuck_request_alert_submitted_status():
    """SUBMITTED requests also trigger stuck alert after SLA."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine, _settings(stuck_request_hours=72))
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-9")
        checker = await _seed_user(session, "checker-9")
        project = await _seed_project(session)
        await _seed_membership(session, checker, project, ProjectRole.OUTPUT_CHECKER)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_request(
            session,
            project,
            researcher,
            AirlockRequestStatus.SUBMITTED,
            updated_at=now - timedelta(hours=80),
        )

    await stuck_request_alert_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_stuck_request_alert_notifications_disabled():
    """Job is a no-op when notifications_enabled=False."""
    engine = await _make_engine()
    ctx = await _make_ctx(engine, _settings(notifications_enabled=False, stuck_request_hours=72))
    factory: async_sessionmaker = ctx["session_factory"]

    async with factory() as session:
        researcher = await _seed_user(session, "researcher-10")
        checker = await _seed_user(session, "checker-10")
        project = await _seed_project(session)
        await _seed_membership(session, checker, project, ProjectRole.OUTPUT_CHECKER)
        now = datetime.now(UTC).replace(tzinfo=None)
        await _seed_request(
            session,
            project,
            researcher,
            AirlockRequestStatus.HUMAN_REVIEW,
            updated_at=now - timedelta(hours=100),
        )

    await stuck_request_alert_job(ctx)

    async with factory() as session:
        result = await session.exec(select(Notification))
        notifications = list(result.all())

    assert len(notifications) == 0
