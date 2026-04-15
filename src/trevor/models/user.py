"""User model — shadow record synced from Keycloak."""

import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    keycloak_sub: str = Field(unique=True, index=True)
    email: str = Field(default="")
    display_name: str = Field(default="")
    created_at: datetime = Field(default_factory=_utcnow)
