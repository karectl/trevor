"""Routers for Review endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_session
from trevor.models.project import ProjectMembership, ProjectRole
from trevor.models.request import (
    AirlockRequest,
    AirlockRequestStatus,
    OutputObject,
    OutputObjectMetadata,
    OutputObjectState,
)
from trevor.models.review import Review, ReviewDecision, ReviewerType
from trevor.schemas.review import HumanReviewCreate, ReviewRead
from trevor.services import audit_service

router = APIRouter(prefix="/requests", tags=["reviews"])

Session = Annotated[AsyncSession, Depends(get_session)]

_CHECKER_ROLES = {ProjectRole.OUTPUT_CHECKER, ProjectRole.SENIOR_CHECKER}

# Map from ReviewDecision to OutputObjectState
_DECISION_TO_OBJ_STATE = {
    ReviewDecision.APPROVED: OutputObjectState.APPROVED,
    ReviewDecision.REJECTED: OutputObjectState.REJECTED,
    ReviewDecision.CHANGES_REQUESTED: OutputObjectState.CHANGES_REQUESTED,
}

# Strictness ranking for per-object decisions (higher = stricter)
_STRICTNESS = {
    ReviewDecision.APPROVED: 0,
    ReviewDecision.CHANGES_REQUESTED: 1,
    ReviewDecision.REJECTED: 2,
}


async def _get_request_or_404(request_id: uuid.UUID, session: AsyncSession) -> AirlockRequest:
    req = await session.get(AirlockRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


async def _assert_access(req: AirlockRequest, auth: CurrentAuth, session: AsyncSession) -> None:
    if auth.is_admin:
        return
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == req.project_id,
            ProjectMembership.user_id == auth.user.id,
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=403, detail="Not a member of this project")


async def _evaluate_two_reviewer_rule(req: AirlockRequest, session: AsyncSession) -> None:
    """Check if 2+ reviews exist and transition request state accordingly."""
    result = await session.exec(select(Review).where(Review.request_id == req.id))
    reviews = list(result.all())

    if len(reviews) < 2:
        return  # Not enough reviews yet

    # Determine overall outcome
    decisions = [r.decision for r in reviews]

    if ReviewDecision.REJECTED in decisions:
        new_status = AirlockRequestStatus.REJECTED
        event = "request.rejected"
    elif ReviewDecision.CHANGES_REQUESTED in decisions:
        new_status = AirlockRequestStatus.CHANGES_REQUESTED
        event = "request.changes_requested"
    elif all(d == ReviewDecision.APPROVED for d in decisions):
        new_status = AirlockRequestStatus.APPROVED
        event = "request.approved"
    else:
        return  # Shouldn't happen, but safety

    req.status = new_status
    req.updated_at = datetime.now(UTC)
    if new_status in (
        AirlockRequestStatus.APPROVED,
        AirlockRequestStatus.REJECTED,
    ):
        req.closed_at = datetime.now(UTC)
    session.add(req)
    await audit_service.emit(
        session,
        event_type=event,
        actor_id="system",
        request_id=req.id,
        payload={"review_count": len(reviews)},
    )


@router.post(
    "/{request_id}/reviews",
    status_code=status.HTTP_201_CREATED,
    response_model=ReviewRead,
)
async def create_human_review(
    request_id: uuid.UUID,
    body: HumanReviewCreate,
    auth: CurrentAuth,
    session: Session,
) -> Review:
    req = await _get_request_or_404(request_id, session)

    # Must be HUMAN_REVIEW state
    if req.status != AirlockRequestStatus.HUMAN_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"Request in {req.status}, expected HUMAN_REVIEW",
        )

    # Submitter cannot review (C-04)
    if req.submitted_by == auth.user.id:
        raise HTTPException(status_code=403, detail="Submitter cannot review own request")

    # Must be checker on the project
    if not auth.is_admin:
        result = await session.exec(
            select(ProjectMembership).where(
                ProjectMembership.project_id == req.project_id,
                ProjectMembership.user_id == auth.user.id,
                ProjectMembership.role.in_(  # type: ignore[union-attr]
                    [r.value for r in _CHECKER_ROLES]
                ),
            )
        )
        if result.first() is None:
            raise HTTPException(
                status_code=403,
                detail="Output checker or senior checker role required",
            )

    # No duplicate human review
    existing = await session.exec(
        select(Review).where(
            Review.request_id == request_id,
            Review.reviewer_id == auth.user.id,
            Review.reviewer_type == ReviewerType.HUMAN,
        )
    )
    if existing.first() is not None:
        raise HTTPException(status_code=409, detail="Already reviewed this request")

    # Validate object_decisions reference real objects
    obj_map: dict[uuid.UUID, OutputObject] = {}
    if body.object_decisions:
        for od in body.object_decisions:
            obj = await session.get(OutputObject, od.object_id)
            if obj is None or obj.request_id != request_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"Object {od.object_id} not found in request",
                )
            obj_map[od.object_id] = obj

    # Build findings JSON
    findings = [
        {
            "object_id": str(od.object_id),
            "decision": od.decision.value,
            "feedback": od.feedback,
        }
        for od in body.object_decisions
    ]

    # Create review
    review = Review(
        request_id=request_id,
        reviewer_id=auth.user.id,
        reviewer_type=ReviewerType.HUMAN,
        decision=body.decision,
        summary=body.summary,
        findings=findings,
    )
    session.add(review)

    # Update per-object states and metadata
    for od in body.object_decisions:
        obj = obj_map[od.object_id]
        new_state = _DECISION_TO_OBJ_STATE[od.decision]

        # If object already has a human decision, use stricter one
        current_strictness = _STRICTNESS.get(
            ReviewDecision(obj.state.value)
            if obj.state.value in [d.value for d in ReviewDecision]
            else ReviewDecision.APPROVED,
            -1,
        )
        new_strictness = _STRICTNESS[od.decision]
        if obj.state == OutputObjectState.PENDING or new_strictness > current_strictness:
            obj.state = new_state
            session.add(obj)
            await audit_service.emit(
                session,
                event_type="object.state_changed",
                actor_id=str(auth.user.id),
                request_id=request_id,
                payload={
                    "object_id": str(od.object_id),
                    "new_state": new_state.value,
                },
            )

        # Append feedback to metadata
        if od.feedback:
            meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
            if meta:
                feedback_entry = {
                    "reviewer_id": str(auth.user.id),
                    "version": obj.version,
                    "feedback": od.feedback,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                # Must create new list to trigger change detection
                meta.checker_feedback = [*meta.checker_feedback, feedback_entry]
                meta.updated_at = datetime.now(UTC)
                session.add(meta)

    await audit_service.emit(
        session,
        event_type="review.created",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={
            "review_id": str(review.id),
            "decision": body.decision.value,
        },
    )

    # Evaluate two-reviewer rule
    await session.flush()  # Ensure review is visible in queries
    await _evaluate_two_reviewer_rule(req, session)

    await session.commit()
    await session.refresh(review)
    return review


@router.get("/{request_id}/reviews", response_model=list[ReviewRead])
async def list_reviews(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> list[Review]:
    req = await _get_request_or_404(request_id, session)
    await _assert_access(req, auth, session)
    result = await session.exec(
        select(Review).where(Review.request_id == request_id).order_by(Review.created_at)
    )
    return list(result.all())


@router.get("/{request_id}/reviews/{review_id}", response_model=ReviewRead)
async def get_review(
    request_id: uuid.UUID,
    review_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> Review:
    req = await _get_request_or_404(request_id, session)
    await _assert_access(req, auth, session)
    review = await session.get(Review, review_id)
    if review is None or review.request_id != request_id:
        raise HTTPException(status_code=404, detail="Review not found")
    return review
