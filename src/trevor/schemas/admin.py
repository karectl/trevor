"""Schemas for admin dashboard endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class RequestSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    title: str
    status: str
    direction: str
    submitted_by_name: str | None
    object_count: int
    submitted_at: datetime | None
    updated_at: datetime
    age_hours: float

    model_config = {"from_attributes": True}


class RequestListResponse(BaseModel):
    items: list[RequestSummary]
    total: int


class ReviewerStats(BaseModel):
    reviewer_id: uuid.UUID
    reviewer_name: str
    count: int


class StuckRequest(BaseModel):
    request_id: uuid.UUID
    title: str
    status: str
    waiting_hours: float


class PipelineMetrics(BaseModel):
    total_requests: int
    by_status: dict[str, int]
    median_review_hours: float | None
    mean_review_hours: float | None
    approval_rate: float | None
    revision_rate: float | None
    rejection_rate: float | None
    median_revisions_per_request: float | None
    requests_per_reviewer: list[ReviewerStats]
    stuck_requests: list[StuckRequest]


class AuditListResponse(BaseModel):
    items: list[dict]
    total: int
