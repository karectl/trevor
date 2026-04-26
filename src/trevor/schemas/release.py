"""Schemas for ReleaseRecord and DeliveryRecord."""

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


class UploadUrlResponse(BaseModel):
    upload_url: str
    expires_in: int
    storage_key: str


class DeliveryObjectUrl(BaseModel):
    object_id: uuid.UUID
    filename: str
    download_url: str
    checksum_sha256: str
    size_bytes: int


class DeliveryRecordRead(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    delivery_type: str
    delivered_at: datetime
    delivered_by: uuid.UUID
    delivery_metadata: dict[str, Any]

    model_config = {"from_attributes": True}


class DeliveryResponse(DeliveryRecordRead):
    object_urls: list[DeliveryObjectUrl] = []
