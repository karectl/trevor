"""User model — shadow record synced from CR8TOR."""

import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # DB columns are TIMESTAMP WITHOUT TIME ZONE, so persist naive UTC.
    return datetime.now(UTC).replace(tzinfo=None)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    keycloak_sub: str | None = Field(default=None, unique=True, index=True)
    username: str
    email: str
    given_name: str
    family_name: str
    affiliation: str
    crd_name: str
    active: bool
    crd_synced_at: datetime = Field(default_factory=_utcnow)
    created_at: datetime = Field(default_factory=_utcnow)
