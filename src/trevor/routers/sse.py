"""SSE endpoints for live UI updates (Datastar merge-fragments)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_engine
from trevor.database import get_session_factory as _get_session_factory_fn
from trevor.models.notification import Notification
from trevor.models.project import ProjectMembership, ProjectRole
from trevor.models.request import AirlockRequest, AirlockRequestStatus
from trevor.models.review import Review
from trevor.settings import Settings, get_settings
from trevor.sse import sse_response, sse_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui/sse", tags=["sse"])


# ---------------------------------------------------------------------------
# Session factory helper (injected via dep so tests can override get_settings)
# ---------------------------------------------------------------------------


def _make_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = get_engine(settings.database_url)
    return _get_session_factory_fn(engine)


def get_sse_session_factory(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> async_sessionmaker[AsyncSession]:
    """FastAPI dep — returns a session factory for SSE poll loops.

    Overridable in tests via app.dependency_overrides.
    """
    return _make_factory(settings)


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------


async def _assert_request_access(
    auth: CurrentAuth, airlock_req: AirlockRequest, session: AsyncSession
) -> None:
    """Raise 403 if user is not a member of the request's project and not admin."""
    if auth.is_admin:
        return
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == airlock_req.project_id,
            ProjectMembership.user_id == auth.user.id,
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=403, detail="Not a member of this project")


async def _assert_checker_access(auth: CurrentAuth, session: AsyncSession) -> None:
    """Raise 403 if user has no checker membership on any project."""
    if auth.is_admin:
        return
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.user_id == auth.user.id,
            ProjectMembership.role.in_(  # type: ignore[attr-defined]
                [ProjectRole.OUTPUT_CHECKER, ProjectRole.SENIOR_CHECKER]
            ),
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=403, detail="Checker role required")


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def _count_reviewable(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Count HUMAN_REVIEW requests the user can still review."""
    checker_projects = await session.exec(
        select(ProjectMembership.project_id).where(
            ProjectMembership.user_id == user_id,
            ProjectMembership.role.in_(  # type: ignore[attr-defined]
                [ProjectRole.OUTPUT_CHECKER, ProjectRole.SENIOR_CHECKER]
            ),
        )
    )
    project_ids = list(checker_projects.all())
    if not project_ids:
        return 0

    reviewed = await session.exec(select(Review.request_id).where(Review.reviewer_id == user_id))
    reviewed_ids = list(reviewed.all())

    stmt = (
        select(func.count())
        .select_from(AirlockRequest)
        .where(
            AirlockRequest.status == AirlockRequestStatus.HUMAN_REVIEW,
            AirlockRequest.project_id.in_(project_ids),  # type: ignore[attr-defined]
        )
    )
    if reviewed_ids:
        stmt = stmt.where(
            AirlockRequest.id.not_in(reviewed_ids)  # type: ignore[attr-defined]
        )
    result = await session.exec(stmt)
    return result.one()


async def _count_unread(session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await session.exec(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.read == False,  # noqa: E712
        )
    )
    return result.one()


# ---------------------------------------------------------------------------
# Endpoint 1: Request status badge
# ---------------------------------------------------------------------------


@router.get("/requests/{request_id}/status")
async def sse_request_status(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    factory: async_sessionmaker[AsyncSession] = Depends(get_sse_session_factory),  # noqa: B008
) -> StreamingResponse:
    """Stream live status badge for a single AirlockRequest."""
    async with factory() as check_session:
        airlock_req = await check_session.get(AirlockRequest, request_id)
        if airlock_req is None:
            raise HTTPException(status_code=404, detail="Request not found")
        await _assert_request_access(auth, airlock_req, check_session)

    async def poll() -> str:
        async with factory() as poll_session:
            req = await poll_session.get(AirlockRequest, request_id)
            if req is None:
                return '<span id="request-status-badge" class="badge">unknown</span>'
            status_val = req.status.value if hasattr(req.status, "value") else str(req.status)
            label = status_val.replace("_", " ")
            return (
                f'<span id="request-status-badge" class="badge badge-{status_val}">{label}</span>'
            )

    return sse_response(sse_stream(request, poll))


# ---------------------------------------------------------------------------
# Endpoint 2: Review queue count
# ---------------------------------------------------------------------------


@router.get("/review/queue-count")
async def sse_review_queue_count(
    request: Request,
    auth: CurrentAuth,
    factory: async_sessionmaker[AsyncSession] = Depends(get_sse_session_factory),  # noqa: B008
) -> StreamingResponse:
    """Stream live count of reviewable requests for the current checker."""
    async with factory() as check_session:
        await _assert_checker_access(auth, check_session)

    user_id = auth.user.id

    async def poll() -> str:
        async with factory() as poll_session:
            count = await _count_reviewable(poll_session, user_id)
        if count:
            return f'<span id="review-queue-count" class="badge badge-count">{count}</span>'
        return '<span id="review-queue-count" class="badge badge-count"></span>'

    return sse_response(sse_stream(request, poll))


# ---------------------------------------------------------------------------
# Endpoint 3: Notification count
# ---------------------------------------------------------------------------


@router.get("/notifications/count")
async def sse_notification_count(
    request: Request,
    auth: CurrentAuth,
    factory: async_sessionmaker[AsyncSession] = Depends(get_sse_session_factory),  # noqa: B008
) -> StreamingResponse:
    """Stream live unread notification count for the nav badge."""
    user_id = auth.user.id

    async def poll() -> str:
        async with factory() as poll_session:
            count = await _count_unread(poll_session, user_id)
        if count:
            return f'<span id="notification-count" class="badge badge-notify">{count}</span>'
        return '<span id="notification-count"></span>'

    return sse_response(sse_stream(request, poll))
