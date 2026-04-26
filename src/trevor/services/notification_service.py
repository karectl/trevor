"""Notification service — event dataclass, backend protocol, router, helpers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.notification import Notification, NotificationEventType

if TYPE_CHECKING:
    from trevor.models.request import AirlockRequest
    from trevor.settings import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NotificationEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotificationEvent:
    """Immutable event passed to backends for dispatch."""

    event_type: str
    title: str
    body: str
    request_id: uuid.UUID | None = None
    recipient_user_ids: list[uuid.UUID] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NotificationBackend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationBackend(Protocol):
    async def send(self, event: NotificationEvent, session: AsyncSession) -> None:
        """Deliver a notification event."""
        ...


# ---------------------------------------------------------------------------
# InAppBackend
# ---------------------------------------------------------------------------


class InAppBackend:
    """Writes one Notification row per recipient to the DB."""

    async def send(self, event: NotificationEvent, session: AsyncSession) -> None:
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


# ---------------------------------------------------------------------------
# NotificationRouter
# ---------------------------------------------------------------------------


class NotificationRouter:
    """Dispatches a NotificationEvent to all registered backends.

    Error isolation: failure in one backend is logged but does not prevent
    other backends from executing (ADR-0009).
    """

    def __init__(self, backends: list[NotificationBackend]) -> None:
        self._backends = backends

    async def dispatch(self, event: NotificationEvent, session: AsyncSession) -> None:
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


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------


_CHECKER_EVENTS = {
    NotificationEventType.REQUEST_SUBMITTED,
    NotificationEventType.AGENT_REVIEW_READY,
}

_RESEARCHER_EVENTS = {
    NotificationEventType.REQUEST_CHANGES_REQUESTED,
    NotificationEventType.REQUEST_APPROVED,
    NotificationEventType.REQUEST_REJECTED,
    NotificationEventType.REQUEST_RELEASED,
    NotificationEventType.PRESIGNED_URL_EXPIRING,
}


async def get_recipients(
    event_type: str,
    request: AirlockRequest,
    session: AsyncSession,
) -> list[uuid.UUID]:
    """Resolve recipient user IDs based on event type and request context."""
    from trevor.models.project import ProjectMembership, ProjectRole

    if event_type in _CHECKER_EVENTS:
        result = await session.exec(
            select(ProjectMembership.user_id).where(
                ProjectMembership.project_id == request.project_id,
                ProjectMembership.role.in_(  # type: ignore[attr-defined]
                    [ProjectRole.OUTPUT_CHECKER, ProjectRole.SENIOR_CHECKER]
                ),
            )
        )
        return list(result.all())

    if event_type in _RESEARCHER_EVENTS:
        return [request.submitted_by] if request.submitted_by else []

    logger.warning("get_recipients: unknown event_type %s", event_type)
    return []


# ---------------------------------------------------------------------------
# Event builder
# ---------------------------------------------------------------------------


_TITLES: dict[str, str] = {
    NotificationEventType.REQUEST_SUBMITTED: "Request submitted: {title}",
    NotificationEventType.AGENT_REVIEW_READY: "Agent review ready: {title}",
    NotificationEventType.REQUEST_CHANGES_REQUESTED: "Changes requested: {title}",
    NotificationEventType.REQUEST_APPROVED: "Request approved: {title}",
    NotificationEventType.REQUEST_REJECTED: "Request rejected: {title}",
    NotificationEventType.REQUEST_RELEASED: "Request released: {title}",
    NotificationEventType.PRESIGNED_URL_EXPIRING: "Download link expiring: {title}",
    NotificationEventType.REQUEST_STUCK: "Request stuck: {title}",
}

_BODIES: dict[str, str] = {
    NotificationEventType.REQUEST_SUBMITTED: (
        'Airlock request "{title}" has been submitted and is awaiting review.'
    ),
    NotificationEventType.AGENT_REVIEW_READY: (
        'Automated agent review is complete for "{title}". Human review can begin.'
    ),
    NotificationEventType.REQUEST_CHANGES_REQUESTED: (
        'A reviewer has requested changes to your request "{title}".'
    ),
    NotificationEventType.REQUEST_APPROVED: ('Your request "{title}" has been approved.'),
    NotificationEventType.REQUEST_REJECTED: ('Your request "{title}" has been rejected.'),
    NotificationEventType.REQUEST_RELEASED: (
        'Your request "{title}" has been released. Download links are available.'
    ),
    NotificationEventType.PRESIGNED_URL_EXPIRING: (
        'Download links for "{title}" are expiring soon.'
    ),
    NotificationEventType.REQUEST_STUCK: (
        'Request "{title}" has been waiting for review longer than the SLA threshold.'
    ),
}


async def create_event(
    event_type: str,
    request: AirlockRequest,
    session: AsyncSession,
) -> NotificationEvent:
    """Build a NotificationEvent with resolved recipients and human-readable text."""
    recipients = await get_recipients(event_type, request, session)
    t = request.title
    title = _TITLES.get(event_type, event_type).format(title=t)
    body = _BODIES.get(event_type, "").format(title=t)
    return NotificationEvent(
        event_type=event_type,
        title=title,
        body=body,
        request_id=request.id,
        recipient_user_ids=recipients,
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def get_router(settings: Settings) -> NotificationRouter:
    """Build a NotificationRouter with enabled backends.

    InAppBackend is always registered.
    Future: SmtpBackend added in iteration 15 when smtp_notifications_enabled=True.
    """
    backends: list[NotificationBackend] = [InAppBackend()]
    return NotificationRouter(backends)
