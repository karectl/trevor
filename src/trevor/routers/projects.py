"""Project routes — GET /projects, GET /projects/{id}."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_session
from trevor.models.project import Project
from trevor.schemas.project import ProjectRead

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    auth: CurrentAuth,  # noqa: ARG001
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Project]:
    result = await session.exec(select(Project))
    return list(result.all())


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    auth: CurrentAuth,  # noqa: ARG001
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project
