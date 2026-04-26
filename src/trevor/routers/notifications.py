"""Notification API endpoints."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_session
from trevor.models.notification import Notification
from trevor.schemas.notification import NotificationRead, UnreadCountRead

router = APIRouter(prefix="/notifications", tags=["notifications"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/unread-count", response_model=None)
async def unread_count(
    request: Request,
    auth: CurrentAuth,
    session: Session,
) -> StreamingResponse | UnreadCountRead:
    """Return unread notification count.

    Returns SSE signals fragment when called by Datastar ($$get),
    plain JSON otherwise.
    """
    result = await session.exec(
        select(func.count()).where(
            Notification.user_id == auth.user.id,
            Notification.read == False,  # noqa: E712
        )
    )
    count = result.one()

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        signals = json.dumps({"count": count})

        async def _stream() -> AsyncGenerator[str]:
            yield f"event: datastar-merge-signals\ndata: signals {signals}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    return UnreadCountRead(count=count)


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    auth: CurrentAuth,
    session: Session,
    limit: int = Query(default=20, ge=1, le=100),  # noqa: B008
    before: datetime | None = Query(default=None),  # noqa: B008
    unread_only: bool = Query(default=False),  # noqa: B008
) -> list[NotificationRead]:
    """List notifications for the current user, newest first."""
    stmt = select(Notification).where(Notification.user_id == auth.user.id)
    if unread_only:
        stmt = stmt.where(Notification.read == False)  # noqa: E712
    if before:
        stmt = stmt.where(Notification.created_at < before)
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    result = await session.exec(stmt)
    return [NotificationRead.model_validate(n) for n in result.all()]


@router.patch("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(
    notification_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> NotificationRead:
    """Mark a single notification as read."""
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != auth.user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.read = True
    session.add(notification)
    await session.commit()
    await session.refresh(notification)
    return NotificationRead.model_validate(notification)


@router.post("/mark-all-read", status_code=204)
async def mark_all_read(
    auth: CurrentAuth,
    session: Session,
) -> None:
    """Mark all notifications for the current user as read."""
    result = await session.exec(
        select(Notification).where(
            Notification.user_id == auth.user.id,
            Notification.read == False,  # noqa: E712
        )
    )
    for notification in result.all():
        notification.read = True
        session.add(notification)
    await session.commit()
