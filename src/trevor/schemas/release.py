"""Schemas for ReleaseRecord."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ReleaseRecordRead(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    crate_storage_key: str
    crate_checksum_sha256: str
    presigned_url: str
    url_expires_at: datetime | None
    delivered_to: list[Any]
    created_at: datetime

    model_config = {"from_attributes": True}
