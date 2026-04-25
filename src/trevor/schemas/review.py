"""Schemas for Review."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from trevor.models.review import ReviewDecision


class ObjectDecision(BaseModel):
    object_id: uuid.UUID
    decision: ReviewDecision
    feedback: str = ""


class HumanReviewCreate(BaseModel):
    decision: ReviewDecision
    summary: str
    object_decisions: list[ObjectDecision] = []


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
