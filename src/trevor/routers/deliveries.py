"""Routers for ingress delivery endpoints.

Handles pre-signed PUT URL generation, upload confirmation, and
workspace delivery for ingress AirlockRequests.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth, RequireAdmin
from trevor.database import get_session
from trevor.models.project import ProjectMembership, ProjectRole
from trevor.models.release import DeliveryRecord
from trevor.models.request import (
    AirlockDirection,
    AirlockRequest,
    AirlockRequestStatus,
    OutputObject,
    OutputObjectState,
)
from trevor.schemas.release import (
    DeliveryObjectUrl,
    DeliveryRecordRead,
    DeliveryResponse,
    UploadUrlResponse,
)
from trevor.schemas.request import OutputObjectRead
from trevor.services import audit_service
from trevor.settings import Settings, get_settings

router = APIRouter(prefix="/requests", tags=["deliveries"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_PRESIGNED_PUT_TTL = 3600  # 1 hour


async def _get_request_or_404(request_id: uuid.UUID, session: AsyncSession) -> AirlockRequest:
    req = await session.get(AirlockRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


async def _assert_ingress_creator(
    project_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool, session: AsyncSession
) -> None:
    if is_admin:
        return
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
            ProjectMembership.role == ProjectRole.SENIOR_CHECKER,
        )
    )
    if result.first() is None:
        raise HTTPException(
            status_code=403, detail="Admin or senior_checker role required for ingress"
        )


async def _assert_project_member(
    project_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool, session: AsyncSession
) -> None:
    if is_admin:
        return
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=403, detail="Not a member of this project")


@router.post(
    "/{request_id}/objects/{object_id}/upload-url",
    response_model=UploadUrlResponse,
)
async def generate_upload_url(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
) -> UploadUrlResponse:
    """Generate pre-signed PUT URL for external upload of an ingress object."""
    req = await _get_request_or_404(request_id, session)
    if req.direction != AirlockDirection.INGRESS:
        raise HTTPException(status_code=409, detail="Only valid for ingress requests")
    if req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Request not in DRAFT state")
    await _assert_ingress_creator(req.project_id, auth.user.id, auth.is_admin, session)

    obj = await session.get(OutputObject, object_id)
    if obj is None or obj.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")

    if settings.dev_auth_bypass:
        upload_url = f"https://mock-s3.example.com/{obj.storage_key}?presigned=put"
    else:
        from trevor.storage import generate_presigned_put_url

        upload_url = await generate_presigned_put_url(
            bucket=settings.s3_quarantine_bucket,
            key=obj.storage_key,
            expires_in=_PRESIGNED_PUT_TTL,
            settings=settings,
        )

    obj.upload_url_generated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    await audit_service.emit(
        session,
        event_type="object.upload_url_generated",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"object_id": str(object_id), "storage_key": obj.storage_key},
    )
    await session.commit()

    return UploadUrlResponse(
        upload_url=upload_url,
        expires_in=_PRESIGNED_PUT_TTL,
        storage_key=obj.storage_key,
    )


@router.post(
    "/{request_id}/objects/{object_id}/confirm-upload",
    response_model=OutputObjectRead,
)
async def confirm_upload(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
) -> OutputObject:
    """Confirm external upload completed. Fetch HEAD, compute checksum, update object."""
    req = await _get_request_or_404(request_id, session)
    if req.direction != AirlockDirection.INGRESS:
        raise HTTPException(status_code=409, detail="Only valid for ingress requests")
    if req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Request not in DRAFT state")
    await _assert_ingress_creator(req.project_id, auth.user.id, auth.is_admin, session)

    obj = await session.get(OutputObject, object_id)
    if obj is None or obj.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")
    if obj.upload_url_generated_at is None:
        raise HTTPException(status_code=409, detail="Upload URL not yet generated for this object")

    if settings.dev_auth_bypass:
        # Dev mode: assume upload succeeded, set dummy checksum
        obj.checksum_sha256 = hashlib.sha256(obj.storage_key.encode()).hexdigest()
        obj.size_bytes = 1024
    else:
        from trevor.storage import download_object, head_object

        meta = await head_object(
            bucket=settings.s3_quarantine_bucket,
            key=obj.storage_key,
            settings=settings,
        )
        obj.size_bytes = meta["content_length"]

        # Download to compute SHA-256 (authoritative checksum)
        raw = await download_object(
            bucket=settings.s3_quarantine_bucket,
            key=obj.storage_key,
            settings=settings,
        )
        obj.checksum_sha256 = hashlib.sha256(raw).hexdigest()

    session.add(obj)
    await audit_service.emit(
        session,
        event_type="object.upload_confirmed",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={
            "object_id": str(object_id),
            "checksum_sha256": obj.checksum_sha256,
            "size_bytes": obj.size_bytes,
        },
    )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.post(
    "/{request_id}/deliver",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeliveryResponse,
)
async def deliver_request(
    request_id: uuid.UUID,
    _admin: RequireAdmin,
    session: Session,
    settings: SettingsDep,
) -> DeliveryResponse:
    """Generate workspace delivery URLs for an approved ingress request."""
    req = await _get_request_or_404(request_id, session)
    if req.direction != AirlockDirection.INGRESS:
        raise HTTPException(status_code=409, detail="Only valid for ingress requests")
    if req.status != AirlockRequestStatus.APPROVED:
        raise HTTPException(status_code=409, detail=f"Request in {req.status}, expected APPROVED")

    existing = await session.exec(
        select(DeliveryRecord).where(DeliveryRecord.request_id == request_id)
    )
    if existing.first() is not None:
        raise HTTPException(status_code=409, detail="Already delivered")

    # Get approved objects
    objects_result = await session.exec(
        select(OutputObject).where(
            OutputObject.request_id == request_id,
            OutputObject.state == OutputObjectState.APPROVED,
        )
    )
    approved_objects = list(objects_result.all())
    if not approved_objects:
        raise HTTPException(status_code=422, detail="No approved objects to deliver")

    # Generate pre-signed GET URLs per object
    object_urls: list[DeliveryObjectUrl] = []
    for obj in approved_objects:
        if settings.dev_auth_bypass:
            download_url = f"https://mock-s3.example.com/{obj.storage_key}?presigned=get"
        else:
            from trevor.storage import generate_presigned_get_url

            download_url = await generate_presigned_get_url(
                bucket=settings.s3_quarantine_bucket,
                key=obj.storage_key,
                expires_in=86400,  # 24h for workspace pull
                settings=settings,
            )
        object_urls.append(
            DeliveryObjectUrl(
                object_id=obj.id,
                filename=obj.filename,
                download_url=download_url,
                checksum_sha256=obj.checksum_sha256,
                size_bytes=obj.size_bytes,
            )
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    record = DeliveryRecord(
        request_id=request_id,
        delivered_at=now,
        delivered_by=_admin.user.id,
        delivery_metadata={
            "object_count": len(object_urls),
            "url_expires_at": (datetime.now(UTC).replace(tzinfo=None).isoformat()),
        },
    )
    session.add(record)

    req.status = AirlockRequestStatus.RELEASING
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.delivering",
        actor_id=str(_admin.user.id),
        request_id=request_id,
        payload={"object_count": len(object_urls)},
    )
    await session.commit()

    # Transition to RELEASED
    req.status = AirlockRequestStatus.RELEASED
    req.closed_at = now
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.released",
        actor_id=str(_admin.user.id),
        request_id=request_id,
        payload={"delivery_record_id": str(record.id)},
    )
    await session.commit()
    await session.refresh(record)

    return DeliveryResponse(
        id=record.id,
        request_id=record.request_id,
        delivery_type=record.delivery_type,
        delivered_at=record.delivered_at,
        delivered_by=record.delivered_by,
        delivery_metadata=record.delivery_metadata,
        object_urls=object_urls,
    )


@router.get(
    "/{request_id}/delivery",
    response_model=DeliveryRecordRead,
)
async def get_delivery(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> DeliveryRecord:
    """Get delivery record for an ingress request."""
    req = await _get_request_or_404(request_id, session)
    await _assert_project_member(req.project_id, auth.user.id, auth.is_admin, session)

    result = await session.exec(
        select(DeliveryRecord).where(DeliveryRecord.request_id == request_id)
    )
    record = result.first()
    if record is None:
        raise HTTPException(status_code=404, detail="No delivery record found")
    return record
