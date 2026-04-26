"""Membership routes — admin CRUD for ProjectMembership."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth, require_admin
from trevor.database import get_session
from trevor.limiter import limiter
from trevor.schemas.membership import MembershipCreate, MembershipRead
from trevor.services.membership_service import (
    create_membership,
    delete_membership,
    list_memberships_for_project,
)

router = APIRouter(prefix="/memberships", tags=["memberships"])


@router.get("/project/{project_id}", response_model=list[MembershipRead])
async def list_project_memberships(
    project_id: uuid.UUID,
    auth: CurrentAuth,  # noqa: ARG001
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MembershipRead]:
    memberships = await list_memberships_for_project(project_id, session)
    return [MembershipRead.model_validate(m) for m in memberships]


@router.post("", response_model=MembershipRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def create_project_membership(
    request: Request,
    body: MembershipCreate,
    auth: Annotated[CurrentAuth, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MembershipRead:
    membership = await create_membership(
        user_id=body.user_id,
        project_id=body.project_id,
        role=body.role,
        assigned_by=auth.user.id,
        session=session,
    )
    return MembershipRead.model_validate(membership)


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_membership(
    membership_id: uuid.UUID,
    auth: Annotated[CurrentAuth, Depends(require_admin)],  # noqa: ARG001
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await delete_membership(membership_id, session)
