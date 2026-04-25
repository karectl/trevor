"""Review model — agent and human reviews of airlock requests."""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReviewerType(enum.StrEnum):
    AGENT = "agent"
    HUMAN = "human"


class ReviewDecision(enum.StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class Review(SQLModel, table=True):
    __tablename__ = "reviews"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(foreign_key="airlock_requests.id", index=True)
    reviewer_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    reviewer_type: ReviewerType
    decision: ReviewDecision
    summary: str = Field(default="")
    findings: list[Any] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, default=list)
    )
    created_at: datetime = Field(default_factory=_utcnow)
