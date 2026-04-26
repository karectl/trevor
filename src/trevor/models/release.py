"""ReleaseRecord model — tracks released RO-Crate packages.
DeliveryRecord model — tracks ingress deliveries to workspace.
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DeliveryType(enum.StrEnum):
    WORKSPACE_PULL = "workspace_pull"


class ReleaseRecord(SQLModel, table=True):
    __tablename__ = "release_records"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(foreign_key="airlock_requests.id", index=True, unique=True)
    crate_storage_key: str
    crate_checksum_sha256: str
    presigned_url: str = Field(default="")
    url_expires_at: datetime | None = Field(default=None)
    expiry_warned_at: datetime | None = Field(default=None)
    delivered_to: list[Any] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, default=list),
    )
    created_at: datetime = Field(default_factory=_utcnow)


class DeliveryRecord(SQLModel, table=True):
    __tablename__ = "delivery_records"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(foreign_key="airlock_requests.id", index=True, unique=True)
    delivery_type: DeliveryType = Field(default=DeliveryType.WORKSPACE_PULL)
    delivered_at: datetime = Field(default_factory=_utcnow)
    delivered_by: uuid.UUID = Field(foreign_key="users.id")
    delivery_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )
