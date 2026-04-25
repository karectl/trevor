"""Admin dashboard and metrics endpoints."""

from __future__ import annotations

import collections.abc
import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth, RequireAdmin
from trevor.database import get_session
from trevor.models.project import ProjectMembership, ProjectRole
from trevor.schemas.admin import AuditListResponse, PipelineMetrics, RequestListResponse
from trevor.services.metrics_service import (
    compute_metrics,
    list_admin_requests,
    list_audit_events,
)
from trevor.settings import Settings, get_settings

router = APIRouter(prefix="/admin", tags=["admin"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _require_admin_or_senior(auth: CurrentAuth, session: AsyncSession) -> None:
    """Raise 403 if user is not admin or senior_checker on any project."""
    if auth.is_admin:
        return
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.user_id == auth.user.id,
            ProjectMembership.role == ProjectRole.SENIOR_CHECKER,
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=403, detail="Admin or senior checker required")


@router.get("/requests", response_model=RequestListResponse)
async def admin_list_requests(
    auth: CurrentAuth,
    session: Session,
    status: Annotated[str | None, Query()] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    direction: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "-created_at",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RequestListResponse:
    """List all requests across projects (admin/senior checker)."""
    await _require_admin_or_senior(auth, session)

    status_filter = [s.strip() for s in status.split(",")] if status else None
    items, total = await list_admin_requests(
        session,
        status_filter=status_filter,
        project_id=project_id,
        direction=direction,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return RequestListResponse(items=items, total=total)


@router.get("/metrics", response_model=PipelineMetrics)
async def admin_metrics(
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
) -> PipelineMetrics:
    """Pipeline metrics (admin/senior checker)."""
    await _require_admin_or_senior(auth, session)

    return await compute_metrics(
        session,
        project_id=project_id,
        since=since,
        stuck_hours=settings.stuck_request_hours,
    )


@router.get("/audit", response_model=AuditListResponse)
async def admin_audit(
    _admin: RequireAdmin,
    session: Session,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_id: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditListResponse:
    """Filterable audit log (admin only)."""
    events, total = await list_audit_events(
        session,
        project_id=project_id,
        actor_id=actor_id,
        event_type=event_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    items = [
        {
            "id": str(e.id),
            "timestamp": e.timestamp.isoformat(),
            "event_type": e.event_type,
            "actor_id": e.actor_id,
            "request_id": str(e.request_id) if e.request_id else None,
            "payload": e.payload,
        }
        for e in events
    ]
    return AuditListResponse(items=items, total=total)


@router.get("/audit/export")
async def admin_audit_export(
    _admin: RequireAdmin,
    session: Session,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_id: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> StreamingResponse:
    """Export audit log as CSV (admin only)."""
    events, _ = await list_audit_events(
        session,
        project_id=project_id,
        actor_id=actor_id,
        event_type=event_type,
        since=since,
        until=until,
        limit=10000,
        offset=0,
    )

    def generate() -> collections.abc.Generator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "timestamp", "event_type", "actor_id", "request_id", "payload"])
        buf.seek(0)
        yield buf.read()

        for event in events:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                str(event.id),
                event.timestamp.isoformat(),
                event.event_type,
                event.actor_id,
                str(event.request_id) if event.request_id else "",
                json.dumps(event.payload),
            ])
            buf.seek(0)
            yield buf.read()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
