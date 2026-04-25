"""Routers for Review endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_session
from trevor.models.project import ProjectMembership
from trevor.models.request import AirlockRequest
from trevor.models.review import Review
from trevor.schemas.review import ReviewRead

router = APIRouter(prefix="/requests", tags=["reviews"])

Session = Annotated[AsyncSession, Depends(get_session)]


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
