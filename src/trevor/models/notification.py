"""Notification model — in-app notification records."""

import enum
import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class NotificationEventType(enum.StrEnum):
    REQUEST_SUBMITTED = "request.submitted"
    AGENT_REVIEW_READY = "agent_review.ready"
    REQUEST_CHANGES_REQUESTED = "request.changes_requested"
    REQUEST_APPROVED = "request.approved"
    REQUEST_REJECTED = "request.rejected"
    REQUEST_RELEASED = "request.released"
    PRESIGNED_URL_EXPIRING = "presigned_url.expiring_soon"
    REQUEST_STUCK = "request.stuck"


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(index=True)
    event_type: str = Field(index=True)
    title: str
    body: str
    request_id: uuid.UUID | None = Field(default=None, index=True)
    read: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)
