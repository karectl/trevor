"""Membership service — CRUD + role conflict validation."""

import uuid

from fastapi import HTTPException, status
from sqlmodel import and_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.project import ProjectMembership, ProjectRole

# Roles that conflict with researcher on same project.
_CHECKER_ROLES = {ProjectRole.OUTPUT_CHECKER, ProjectRole.SENIOR_CHECKER}


async def validate_no_role_conflict(
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    new_role: ProjectRole,
    session: AsyncSession,
) -> None:
    """Enforce: researcher != checker on same project (C-04, DOMAIN_MODEL).

    Raises HTTPException 409 if conflict detected.
    """
    stmt = select(ProjectMembership).where(
        and_(
            ProjectMembership.user_id == user_id,
            ProjectMembership.project_id == project_id,
        )
    )
    result = await session.exec(stmt)
    existing = result.all()

    existing_roles = {m.role for m in existing}

    if new_role == ProjectRole.RESEARCHER and existing_roles & _CHECKER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot assign researcher role: user is already a checker on this project",
        )
    if new_role in _CHECKER_ROLES and ProjectRole.RESEARCHER in existing_roles:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot assign checker role: user is already a researcher on this project",
        )


async def create_membership(
    *,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    role: ProjectRole,
    assigned_by: uuid.UUID,
    session: AsyncSession,
) -> ProjectMembership:
    """Create a new membership after validation. Returns the new record."""
    await validate_no_role_conflict(
        user_id=user_id, project_id=project_id, new_role=role, session=session
    )

    # Check for duplicate.
    stmt = select(ProjectMembership).where(
        and_(
            ProjectMembership.user_id == user_id,
            ProjectMembership.project_id == project_id,
            ProjectMembership.role == role,
        )
    )
    result = await session.exec(stmt)
    if result.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Membership with this role already exists",
        )

    membership = ProjectMembership(
        user_id=user_id,
        project_id=project_id,
        role=role,
        assigned_by=assigned_by,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(membership)
    return membership


async def list_memberships_for_project(
    project_id: uuid.UUID, session: AsyncSession
) -> list[ProjectMembership]:
    stmt = select(ProjectMembership).where(ProjectMembership.project_id == project_id)
    result = await session.exec(stmt)
    return list(result.all())


async def list_memberships_for_user(
    user_id: uuid.UUID, session: AsyncSession
) -> list[ProjectMembership]:
    stmt = select(ProjectMembership).where(ProjectMembership.user_id == user_id)
    result = await session.exec(stmt)
    return list(result.all())


async def delete_membership(membership_id: uuid.UUID, session: AsyncSession) -> None:
    stmt = select(ProjectMembership).where(ProjectMembership.id == membership_id)
    result = await session.exec(stmt)
    membership = result.first()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    await session.delete(membership)
    await session.commit()
