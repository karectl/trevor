"""AirlockRequest, OutputObject, OutputObjectMetadata, AuditEvent models."""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AirlockDirection(enum.StrEnum):
    EGRESS = "egress"
    INGRESS = "ingress"


class AirlockRequestStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    AGENT_REVIEW = "AGENT_REVIEW"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RELEASING = "RELEASING"
    RELEASED = "RELEASED"


class OutputObjectState(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    SUPERSEDED = "SUPERSEDED"


class OutputType(enum.StrEnum):
    TABULAR = "tabular"
    FIGURE = "figure"
    MODEL = "model"
    CODE = "code"
    REPORT = "report"
    OTHER = "other"


class AirlockRequest(SQLModel, table=True):
    __tablename__ = "airlock_requests"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    direction: AirlockDirection
    status: AirlockRequestStatus = Field(default=AirlockRequestStatus.DRAFT)
    title: str
    description: str = Field(default="")
    submitted_by: uuid.UUID = Field(foreign_key="users.id")
    submitted_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow)
    closed_at: datetime | None = Field(default=None)


class OutputObject(SQLModel, table=True):
    __tablename__ = "output_objects"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(foreign_key="airlock_requests.id", index=True)
    version: int = Field(default=1)
    replaces_id: uuid.UUID | None = Field(default=None, foreign_key="output_objects.id")
    logical_object_id: uuid.UUID = Field(default_factory=uuid.uuid4, index=True)
    filename: str
    output_type: OutputType
    statbarn: str = Field(default="")
    storage_key: str
    checksum_sha256: str
    size_bytes: int
    state: OutputObjectState = Field(default=OutputObjectState.PENDING)
    uploaded_at: datetime = Field(default_factory=_utcnow)
    uploaded_by: uuid.UUID = Field(foreign_key="users.id")
    upload_url_generated_at: datetime | None = Field(default=None)


class OutputObjectMetadata(SQLModel, table=True):
    __tablename__ = "output_object_metadata"

    logical_object_id: uuid.UUID = Field(primary_key=True)
    title: str = Field(default="")
    description: str = Field(default="")
    researcher_justification: str = Field(default="")
    suppression_notes: str = Field(default="")
    checker_feedback: list[Any] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, default=list)
    )
    tags: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False, default=dict)
    )
    updated_at: datetime = Field(default_factory=_utcnow)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID | None = Field(
        default=None, foreign_key="airlock_requests.id", index=True
    )
    actor_id: str  # user UUID str, "agent:trevor-agent", or "system"
    event_type: str  # namespaced e.g. "request.submitted"
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False, default=dict)
    )
    timestamp: datetime = Field(default_factory=_utcnow, index=True)
