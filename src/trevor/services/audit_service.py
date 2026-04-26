"""AuditEvent emission helpers."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.request import AuditEvent


async def emit(
    session: AsyncSession,
    *,
    event_type: str,
    actor_id: str,
    payload: dict[str, Any] | None = None,
    request_id: uuid.UUID | None = None,
) -> AuditEvent:
    event = AuditEvent(
        request_id=request_id,
        actor_id=actor_id,
        event_type=event_type,
        payload=payload or {},
        timestamp=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(event)
    return event
