"""Pydantic schemas for notification endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    event_type: str
    title: str
    body: str
    request_id: uuid.UUID | None
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountRead(BaseModel):
    count: int
