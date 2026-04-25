"""ReleaseRecord model — tracks released RO-Crate packages."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReleaseRecord(SQLModel, table=True):
    __tablename__ = "release_records"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(foreign_key="airlock_requests.id", index=True, unique=True)
    crate_storage_key: str
    crate_checksum_sha256: str
    presigned_url: str = Field(default="")
    url_expires_at: datetime | None = Field(default=None)
    delivered_to: list[Any] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, default=list),
    )
    created_at: datetime = Field(default_factory=_utcnow)
