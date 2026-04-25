"""Routers for release endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth, RequireAdmin
from trevor.database import get_session
from trevor.models.project import ProjectMembership
from trevor.models.release import ReleaseRecord
from trevor.models.request import AirlockRequest, AirlockRequestStatus
from trevor.schemas.release import ReleaseRecordRead
from trevor.services import audit_service
from trevor.settings import Settings, get_settings

router = APIRouter(prefix="/requests", tags=["releases"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{request_id}/release",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_release(
    request_id: uuid.UUID,
    _admin: RequireAdmin,
    session: Session,
    settings: SettingsDep,
) -> dict[str, str]:
    """Trigger release of an approved request."""
    req = await session.get(AirlockRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != AirlockRequestStatus.APPROVED:
        raise HTTPException(
            status_code=409,
            detail=f"Request in {req.status}, expected APPROVED",
        )

    # Check no existing release
    existing = await session.exec(
        select(ReleaseRecord).where(ReleaseRecord.request_id == request_id)
    )
    if existing.first() is not None:
        raise HTTPException(status_code=409, detail="Already released")

    # Transition to RELEASING
    req.status = AirlockRequestStatus.RELEASING
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.releasing",
        actor_id=str(_admin.user.id),
        request_id=request_id,
    )
    await session.commit()

    # Run release inline in dev mode, ARQ in prod
    if settings.dev_auth_bypass:
        from trevor.services.release_service import assemble_and_release

        await assemble_and_release(request_id, session, settings)
    else:
        from arq.connections import ArqRedis, create_pool
        from arq.connections import RedisSettings as ArqRedisSettings

        pool: ArqRedis = await create_pool(ArqRedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("release_job", str(request_id))
        await pool.aclose()

    return {"status": "releasing"}


@router.get(
    "/{request_id}/release",
    response_model=ReleaseRecordRead,
)
async def get_release(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> ReleaseRecord:
    """Get the release record for a request."""
    req = await session.get(AirlockRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")

    if not auth.is_admin:
        result = await session.exec(
            select(ProjectMembership).where(
                ProjectMembership.project_id == req.project_id,
                ProjectMembership.user_id == auth.user.id,
            )
        )
        if result.first() is None:
            raise HTTPException(
                status_code=403,
                detail="Not a member of this project",
            )

    result = await session.exec(select(ReleaseRecord).where(ReleaseRecord.request_id == request_id))
    record = result.first()
    if record is None:
        raise HTTPException(status_code=404, detail="No release record found")
    return record
