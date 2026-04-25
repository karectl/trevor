"""Metrics and admin query service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.project import Project
from trevor.models.request import (
    AirlockRequest,
    AirlockRequestStatus,
    AuditEvent,
    OutputObject,
)
from trevor.models.review import Review, ReviewerType
from trevor.models.user import User
from trevor.schemas.admin import (
    PipelineMetrics,
    RequestSummary,
    ReviewerStats,
    StuckRequest,
)


def _now_naive() -> datetime:
    """UTC now without tzinfo, compatible with SQLite naive datetimes."""
    return datetime.now(UTC).replace(tzinfo=None)


async def _scalar_count(session: AsyncSession, stmt: Any) -> int:  # noqa: ANN401
    """Execute a count query and return int."""
    result = await session.exec(stmt)
    row = result.one()
    # session.exec wraps scalars; row may be int or tuple
    return (
        int(row)
        if isinstance(row, int)
        else int(row[0])
        if hasattr(row, "__getitem__")
        else int(row)
    )


async def list_admin_requests(
    session: AsyncSession,
    *,
    status_filter: list[str] | None = None,
    project_id: uuid.UUID | None = None,
    direction: str | None = None,
    sort: str = "-updated_at",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[RequestSummary], int]:
    """List all requests with summary info for admin dashboard."""
    now = _now_naive()

    # Base query
    base = select(AirlockRequest)
    if status_filter:
        base = base.where(AirlockRequest.status.in_(status_filter))
    if project_id:
        base = base.where(AirlockRequest.project_id == project_id)
    if direction:
        base = base.where(AirlockRequest.direction == direction)

    # Count
    count_q = select(func.count()).select_from(base.subquery())
    total = await _scalar_count(session, count_q)

    # Sort
    desc = sort.startswith("-")
    sort_field = sort.lstrip("-")
    col = getattr(AirlockRequest, sort_field, AirlockRequest.updated_at)
    order = col.desc() if desc else col.asc()
    query = base.order_by(order).offset(offset).limit(min(limit, 100))

    result = await session.exec(query)
    requests = list(result.all())

    summaries = []
    for req in requests:
        # Get project name
        project = await session.get(Project, req.project_id)
        project_name = project.display_name if project else "Unknown"

        # Get submitter name
        submitted_by_name = None
        if req.submitted_by:
            user = await session.get(User, req.submitted_by)
            if user:
                submitted_by_name = f"{user.given_name} {user.family_name}"

        # Count objects
        obj_q = select(func.count()).where(OutputObject.request_id == req.id)
        obj_count = await _scalar_count(session, obj_q)

        age_hours = (now - req.updated_at).total_seconds() / 3600

        summaries.append(
            RequestSummary(
                id=req.id,
                project_id=req.project_id,
                project_name=project_name,
                title=req.title,
                status=req.status,
                direction=req.direction,
                submitted_by_name=submitted_by_name,
                object_count=obj_count,
                submitted_at=req.submitted_at,
                updated_at=req.updated_at,
                age_hours=round(age_hours, 1),
            )
        )

    return summaries, total


async def compute_metrics(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    since: datetime | None = None,
    stuck_hours: int = 72,
) -> PipelineMetrics:
    """Compute pipeline metrics."""
    now = _now_naive()
    if since is None:
        since = now - timedelta(days=30)

    # Base filter
    base = select(AirlockRequest).where(AirlockRequest.updated_at >= since)
    if project_id:
        base = base.where(AirlockRequest.project_id == project_id)

    result = await session.exec(base)
    requests = list(result.all())
    total = len(requests)

    # By status
    by_status: dict[str, int] = {}
    for req in requests:
        status = str(req.status)
        by_status[status] = by_status.get(status, 0) + 1

    # Review time: time from SUBMITTED to first review created_at
    review_hours: list[float] = []
    completed = [
        r
        for r in requests
        if r.status
        in (
            AirlockRequestStatus.APPROVED,
            AirlockRequestStatus.REJECTED,
            AirlockRequestStatus.RELEASED,
            AirlockRequestStatus.CHANGES_REQUESTED,
        )
    ]
    for req in completed:
        review_q = (
            select(Review)
            .where(Review.request_id == req.id, Review.reviewer_type == ReviewerType.HUMAN)
            .order_by(Review.created_at.asc())
            .limit(1)
        )
        rev_result = await session.exec(review_q)
        first_human = rev_result.first()
        if first_human and req.submitted_at:
            hours = (first_human.created_at - req.submitted_at).total_seconds() / 3600
            review_hours.append(hours)

    median_review = round(median(review_hours), 1) if review_hours else None
    mean_review = round(sum(review_hours) / len(review_hours), 1) if review_hours else None

    # Rates (based on terminal/decided requests)
    decided = [
        r
        for r in requests
        if r.status
        in (
            AirlockRequestStatus.APPROVED,
            AirlockRequestStatus.REJECTED,
            AirlockRequestStatus.RELEASED,
            AirlockRequestStatus.CHANGES_REQUESTED,
        )
    ]
    decided_count = len(decided) or None
    approval_count = sum(
        1
        for r in decided
        if r.status in (AirlockRequestStatus.APPROVED, AirlockRequestStatus.RELEASED)
    )
    revision_count = sum(1 for r in decided if r.status == AirlockRequestStatus.CHANGES_REQUESTED)
    rejection_count = sum(1 for r in decided if r.status == AirlockRequestStatus.REJECTED)

    approval_rate = round(approval_count / decided_count, 2) if decided_count else None
    revision_rate = round(revision_count / decided_count, 2) if decided_count else None
    rejection_rate = round(rejection_count / decided_count, 2) if decided_count else None

    # Median revisions: count resubmit audit events per request
    revision_counts: list[int] = []
    for req in requests:
        resubmit_q = select(func.count()).where(
            AuditEvent.request_id == req.id,
            AuditEvent.event_type == "request.resubmitted",
        )
        cnt = await _scalar_count(session, resubmit_q)
        revision_counts.append(cnt)
    median_revisions = float(median(revision_counts)) if revision_counts else None

    # Requests per reviewer (human only)
    reviewer_q = (
        select(Review.reviewer_id, func.count().label("cnt"))
        .where(
            Review.reviewer_type == ReviewerType.HUMAN,
            Review.created_at >= since,
        )
        .group_by(Review.reviewer_id)
    )
    if project_id:
        reviewer_q = reviewer_q.where(
            Review.request_id.in_(
                select(AirlockRequest.id).where(AirlockRequest.project_id == project_id)
            )
        )
    rev_rows = await session.exec(reviewer_q)
    reviewer_stats = []
    for reviewer_id, count in rev_rows.all():
        if reviewer_id:
            user = await session.get(User, reviewer_id)
            name = f"{user.given_name} {user.family_name}" if user else "Unknown"
            reviewer_stats.append(
                ReviewerStats(reviewer_id=reviewer_id, reviewer_name=name, count=count)
            )

    # Stuck requests
    stuck_threshold = now - timedelta(hours=stuck_hours)
    stuck_statuses = [
        AirlockRequestStatus.SUBMITTED,
        AirlockRequestStatus.AGENT_REVIEW,
        AirlockRequestStatus.HUMAN_REVIEW,
    ]
    stuck_q = select(AirlockRequest).where(
        AirlockRequest.status.in_(stuck_statuses),
        AirlockRequest.updated_at < stuck_threshold,
    )
    if project_id:
        stuck_q = stuck_q.where(AirlockRequest.project_id == project_id)
    stuck_result = await session.exec(stuck_q)
    stuck_list = []
    for req in stuck_result.all():
        waiting = (now - req.updated_at).total_seconds() / 3600
        stuck_list.append(
            StuckRequest(
                request_id=req.id,
                title=req.title,
                status=str(req.status),
                waiting_hours=round(waiting, 1),
            )
        )

    return PipelineMetrics(
        total_requests=total,
        by_status=by_status,
        median_review_hours=median_review,
        mean_review_hours=mean_review,
        approval_rate=approval_rate,
        revision_rate=revision_rate,
        rejection_rate=rejection_rate,
        median_revisions_per_request=median_revisions,
        requests_per_reviewer=reviewer_stats,
        stuck_requests=stuck_list,
    )


async def list_audit_events(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    """List audit events with filters."""
    now = _now_naive()
    if since is None:
        since = now - timedelta(days=30)
    if until is None:
        until = now

    base = select(AuditEvent).where(
        AuditEvent.timestamp >= since,
        AuditEvent.timestamp <= until,
    )

    if project_id:
        request_ids = select(AirlockRequest.id).where(AirlockRequest.project_id == project_id)
        base = base.where(AuditEvent.request_id.in_(request_ids))
    if actor_id:
        base = base.where(AuditEvent.actor_id == actor_id)
    if event_type:
        base = base.where(AuditEvent.event_type.startswith(event_type))

    count_q = select(func.count()).select_from(base.subquery())
    total = await _scalar_count(session, count_q)

    query = base.order_by(AuditEvent.timestamp.desc()).offset(offset).limit(min(limit, 500))
    result = await session.exec(query)
    events = list(result.all())

    return events, total
