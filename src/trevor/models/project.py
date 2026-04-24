"""Project and ProjectMembership models."""

import enum
import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProjectStatus(enum.StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ProjectRole(enum.StrEnum):
    RESEARCHER = "researcher"
    OUTPUT_CHECKER = "output_checker"
    SENIOR_CHECKER = "senior_checker"


class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    crd_name: str = Field(unique=True, index=True)
    display_name: str = Field(default="")
    status: ProjectStatus = Field(default=ProjectStatus.ACTIVE)
    synced_at: datetime = Field(default_factory=_utcnow)


class ProjectMembership(SQLModel, table=True):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id", "role", name="uq_membership"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    role: ProjectRole
    assigned_by: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    assigned_at: datetime = Field(default_factory=_utcnow)
