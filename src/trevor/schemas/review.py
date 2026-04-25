"""Schemas for Review."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ReviewRead(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    reviewer_id: uuid.UUID | None
    reviewer_type: str
    decision: str
    summary: str
    findings: list[Any]
    created_at: datetime

    model_config = {"from_attributes": True}
