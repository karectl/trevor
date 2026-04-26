"""Routers for AirlockRequest, OutputObject, and AuditEvent."""

import hashlib
import io
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_session
from trevor.limiter import limiter
from trevor.models.project import Project, ProjectMembership, ProjectRole, ProjectStatus
from trevor.models.request import (
    AirlockDirection,
    AirlockRequest,
    AirlockRequestStatus,
    AuditEvent,
    OutputObject,
    OutputObjectMetadata,
    OutputObjectState,
    OutputType,
)
from trevor.schemas.request import (
    AuditEventRead,
    OutputObjectMetadataRead,
    OutputObjectMetadataUpdate,
    OutputObjectRead,
    RequestCreate,
    RequestRead,
    RequestReadWithObjects,
)
from trevor.services import audit_service
from trevor.settings import Settings, get_settings

router = APIRouter(prefix="/requests", tags=["requests"])

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _get_request_or_404(request_id: uuid.UUID, session: AsyncSession) -> AirlockRequest:
    req = await session.get(AirlockRequest, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


async def _assert_project_access(
    project_id: uuid.UUID, user_id: uuid.UUID, session: AsyncSession
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.status == ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=404, detail="Project not found or archived")
    membership = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    if membership.first() is None:
        raise HTTPException(status_code=403, detail="Not a member of this project")
    return project


async def _assert_researcher(
    project_id: uuid.UUID, user_id: uuid.UUID, session: AsyncSession
) -> None:
    result = await session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
            ProjectMembership.role == ProjectRole.RESEARCHER,
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=403, detail="Researcher role required")


async def _assert_ingress_creator(
    project_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool, session: AsyncSession
) -> None:
    """Admin or senior_checker on project can create ingress requests."""
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


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RequestRead)
async def create_request(
    body: RequestCreate,
    auth: CurrentAuth,
    session: Session,
) -> AirlockRequest:
    if body.direction == AirlockDirection.EGRESS:
        await _assert_researcher(body.project_id, auth.user.id, session)
    else:
        await _assert_ingress_creator(body.project_id, auth.user.id, auth.is_admin, session)
    req = AirlockRequest(
        project_id=body.project_id,
        direction=body.direction,
        title=body.title,
        description=body.description,
        submitted_by=auth.user.id,
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.created",
        actor_id=str(auth.user.id),
        request_id=req.id,
        payload={"direction": req.direction, "title": req.title},
    )
    await session.commit()
    await session.refresh(req)
    return req


@router.get("", response_model=list[RequestRead])
async def list_requests(
    auth: CurrentAuth,
    session: Session,
    project_id: uuid.UUID | None = None,
    status_filter: AirlockRequestStatus | None = None,
) -> list[AirlockRequest]:
    query = select(AirlockRequest)
    if project_id:
        query = query.where(AirlockRequest.project_id == project_id)
    if status_filter:
        query = query.where(AirlockRequest.status == status_filter)
    if not auth.is_admin:
        memberships = await session.exec(
            select(ProjectMembership.project_id).where(ProjectMembership.user_id == auth.user.id)
        )
        project_ids = list(memberships.all())
        query = query.where(AirlockRequest.project_id.in_(project_ids))
    result = await session.exec(query)
    return list(result.all())


@router.get("/{request_id}", response_model=RequestReadWithObjects)
async def get_request(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> RequestReadWithObjects:
    req = await _get_request_or_404(request_id, session)
    if not auth.is_admin:
        await _assert_project_access(req.project_id, auth.user.id, session)
    objects_result = await session.exec(
        select(OutputObject).where(OutputObject.request_id == request_id)
    )
    objects = list(objects_result.all())
    data = RequestReadWithObjects.model_validate(req)
    data.objects = [OutputObjectRead.model_validate(o) for o in objects]
    return data


@router.post("/{request_id}/submit", response_model=RequestRead)
@limiter.limit("10/minute")
async def submit_request(
    request_id: uuid.UUID,
    request: Request,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
) -> AirlockRequest:
    req = await _get_request_or_404(request_id, session)
    if req.submitted_by != auth.user.id and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Not request owner")
    if req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409, detail=f"Cannot submit from status {req.status}")
    objects_result = await session.exec(
        select(OutputObject).where(
            OutputObject.request_id == request_id,
            OutputObject.state == OutputObjectState.PENDING,
        )
    )
    if not objects_result.first():
        raise HTTPException(status_code=422, detail="Request has no pending objects")
    req.status = AirlockRequestStatus.SUBMITTED
    req.submitted_at = datetime.now(UTC).replace(tzinfo=None)
    req.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.submitted",
        actor_id=str(auth.user.id),
        request_id=req.id,
    )
    await session.commit()
    await session.refresh(req)

    from trevor.metrics import requests_submitted_total

    requests_submitted_total.labels(direction=req.direction).inc()

    # Enqueue agent review (inline in dev mode, ARQ in prod)
    if settings.dev_auth_bypass:
        import logging

        logging.getLogger(__name__).info(
            "Dev mode: skipping ARQ enqueue for agent_review_job (request %s)", req.id
        )
    else:
        from arq.connections import ArqRedis, create_pool
        from arq.connections import RedisSettings as ArqRedisSettings

        pool: ArqRedis = await create_pool(ArqRedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("agent_review_job", str(req.id))
        await pool.enqueue_job("send_notifications_job", "request.submitted", str(req.id))
        await pool.aclose()

    return req


@router.post(
    "/{request_id}/objects",
    status_code=status.HTTP_201_CREATED,
    response_model=OutputObjectRead,
)
async def upload_object(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
    output_type: Annotated[OutputType, Form()],
    file: UploadFile | None = None,
    filename: Annotated[str, Form()] = "",
    statbarn: Annotated[str, Form()] = "",
) -> OutputObject:
    req = await _get_request_or_404(request_id, session)
    if req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Request not in DRAFT state")

    if req.direction == AirlockDirection.EGRESS:
        await _assert_researcher(req.project_id, auth.user.id, session)
        if file is None:
            raise HTTPException(status_code=422, detail="File required for egress upload")
    else:
        await _assert_ingress_creator(req.project_id, auth.user.id, auth.is_admin, session)

    logical_object_id = uuid.uuid4()
    version = 1
    object_id = uuid.uuid4()
    effective_filename = (file.filename if file else filename) or "unknown"
    storage_key = (
        f"{req.project_id}/{req.id}/{logical_object_id}/{version}/{object_id}-{effective_filename}"
    )

    if file is not None:
        raw = await file.read()
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(raw) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum upload size of {settings.max_upload_size_mb} MB",
            )
        checksum = hashlib.sha256(raw).hexdigest()
        size = len(raw)
        if not settings.dev_auth_bypass:
            from trevor.storage import upload_fileobj

            await upload_fileobj(
                bucket=settings.s3_quarantine_bucket,
                key=storage_key,
                fileobj=io.BytesIO(raw),
                content_type=file.content_type or "application/octet-stream",
                settings=settings,
            )
    else:
        # Ingress placeholder: awaiting external upload via pre-signed PUT
        checksum = ""
        size = 0

    obj = OutputObject(
        id=object_id,
        request_id=request_id,
        version=version,
        logical_object_id=logical_object_id,
        filename=effective_filename,
        output_type=output_type,
        statbarn=statbarn,
        storage_key=storage_key,
        checksum_sha256=checksum,
        size_bytes=size,
        uploaded_by=auth.user.id,
    )
    session.add(obj)

    meta = OutputObjectMetadata(logical_object_id=logical_object_id)
    session.add(meta)

    await audit_service.emit(
        session,
        event_type="object.uploaded",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={
            "object_id": str(object_id),
            "filename": effective_filename,
            "checksum_sha256": checksum,
            "size_bytes": size,
        },
    )
    await session.commit()
    await session.refresh(obj)

    from trevor.metrics import objects_uploaded_total

    objects_uploaded_total.labels(direction=req.direction).inc()
    return obj


@router.get("/{request_id}/objects", response_model=list[OutputObjectRead])
async def list_objects(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> list[OutputObject]:
    req = await _get_request_or_404(request_id, session)
    if not auth.is_admin:
        await _assert_project_access(req.project_id, auth.user.id, session)
    result = await session.exec(select(OutputObject).where(OutputObject.request_id == request_id))
    return list(result.all())


@router.get("/{request_id}/objects/{object_id}", response_model=OutputObjectRead)
async def get_object(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> OutputObject:
    req = await _get_request_or_404(request_id, session)
    if not auth.is_admin:
        await _assert_project_access(req.project_id, auth.user.id, session)
    obj = await session.get(OutputObject, object_id)
    if obj is None or obj.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")
    return obj


@router.patch(
    "/{request_id}/objects/{object_id}/metadata",
    response_model=OutputObjectMetadataRead,
)
async def update_metadata(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    body: OutputObjectMetadataUpdate,
    auth: CurrentAuth,
    session: Session,
) -> OutputObjectMetadata:
    req = await _get_request_or_404(request_id, session)
    await _assert_researcher(req.project_id, auth.user.id, session)
    obj = await session.get(OutputObject, object_id)
    if obj is None or obj.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")
    meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
    if meta is None:
        meta = OutputObjectMetadata(logical_object_id=obj.logical_object_id)
        session.add(meta)
    meta.title = body.title
    meta.description = body.description
    meta.researcher_justification = body.researcher_justification
    meta.suppression_notes = body.suppression_notes
    meta.tags = body.tags
    meta.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(meta)
    await audit_service.emit(
        session,
        event_type="object.metadata_updated",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={
            "object_id": str(object_id),
            "logical_object_id": str(obj.logical_object_id),
        },
    )
    await session.commit()
    await session.refresh(meta)
    return meta


@router.get(
    "/{request_id}/objects/{object_id}/metadata",
    response_model=OutputObjectMetadataRead,
)
async def get_metadata(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> OutputObjectMetadata:
    req = await _get_request_or_404(request_id, session)
    if not auth.is_admin:
        await _assert_project_access(req.project_id, auth.user.id, session)
    obj = await session.get(OutputObject, object_id)
    if obj is None or obj.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")
    meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Metadata not found")
    return meta


@router.get("/{request_id}/audit", response_model=list[AuditEventRead])
async def list_audit(
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> list[AuditEvent]:
    req = await _get_request_or_404(request_id, session)
    if not auth.is_admin:
        await _assert_project_access(req.project_id, auth.user.id, session)
    result = await session.exec(
        select(AuditEvent).where(AuditEvent.request_id == request_id).order_by(AuditEvent.timestamp)
    )
    return list(result.all())


@router.post(
    "/{request_id}/objects/{object_id}/replace",
    status_code=status.HTTP_201_CREATED,
    response_model=OutputObjectRead,
)
async def replace_object(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
    file: UploadFile,
    output_type: Annotated[OutputType, Form()],
    statbarn: Annotated[str, Form()] = "",
) -> OutputObject:
    """Upload a replacement for an existing output object."""
    req = await _get_request_or_404(request_id, session)
    if req.status != AirlockRequestStatus.CHANGES_REQUESTED:
        raise HTTPException(
            status_code=409,
            detail=f"Request in {req.status}, expected CHANGES_REQUESTED",
        )
    await _assert_researcher(req.project_id, auth.user.id, session)

    original = await session.get(OutputObject, object_id)
    if original is None or original.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")

    replaceable = {OutputObjectState.CHANGES_REQUESTED, OutputObjectState.REJECTED}
    if original.state not in replaceable:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot replace object in {original.state} state",
        )

    raw = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum upload size of {settings.max_upload_size_mb} MB",
        )
    checksum = hashlib.sha256(raw).hexdigest()
    size = len(raw)

    new_version = original.version + 1
    new_id = uuid.uuid4()
    storage_key = (
        f"{req.project_id}/{req.id}/{original.logical_object_id}"
        f"/{new_version}/{new_id}-{file.filename}"
    )

    if not settings.dev_auth_bypass:
        from trevor.storage import upload_fileobj

        await upload_fileobj(
            bucket=settings.s3_quarantine_bucket,
            key=storage_key,
            fileobj=io.BytesIO(raw),
            content_type=file.content_type or "application/octet-stream",
            settings=settings,
        )

    # Supersede original
    original.state = OutputObjectState.SUPERSEDED
    session.add(original)

    # Create replacement
    new_obj = OutputObject(
        id=new_id,
        request_id=request_id,
        version=new_version,
        replaces_id=original.id,
        logical_object_id=original.logical_object_id,
        filename=file.filename or "unknown",
        output_type=output_type,
        statbarn=statbarn,
        storage_key=storage_key,
        checksum_sha256=checksum,
        size_bytes=size,
        uploaded_by=auth.user.id,
    )
    session.add(new_obj)

    # Ensure metadata exists (carries forward via shared logical_object_id)
    meta = await session.get(OutputObjectMetadata, original.logical_object_id)
    if meta is None:
        meta = OutputObjectMetadata(logical_object_id=original.logical_object_id)
        session.add(meta)

    await audit_service.emit(
        session,
        event_type="object.replaced",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={
            "original_object_id": str(original.id),
            "new_object_id": str(new_id),
            "version": new_version,
            "checksum_sha256": checksum,
        },
    )
    await session.commit()
    await session.refresh(new_obj)
    return new_obj


@router.post("/{request_id}/resubmit", response_model=RequestRead)
@limiter.limit("10/minute")
async def resubmit_request(
    request_id: uuid.UUID,
    request: Request,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
) -> AirlockRequest:
    """Resubmit a request after addressing checker feedback."""
    req = await _get_request_or_404(request_id, session)
    if req.submitted_by != auth.user.id and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Not request owner")
    if req.status != AirlockRequestStatus.CHANGES_REQUESTED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resubmit from {req.status}, expected CHANGES_REQUESTED",
        )

    # Must have at least one PENDING object
    result = await session.exec(
        select(OutputObject).where(
            OutputObject.request_id == request_id,
            OutputObject.state == OutputObjectState.PENDING,
        )
    )
    if not result.first():
        raise HTTPException(
            status_code=422,
            detail="No pending objects — upload replacements first",
        )

    req.status = AirlockRequestStatus.SUBMITTED
    req.submitted_at = datetime.now(UTC).replace(tzinfo=None)
    req.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.resubmitted",
        actor_id=str(auth.user.id),
        request_id=req.id,
    )
    await session.commit()
    await session.refresh(req)

    # Enqueue agent review (same as submit)
    if settings.dev_auth_bypass:
        import logging

        logging.getLogger(__name__).info(
            "Dev mode: skipping ARQ enqueue for resubmit (request %s)",
            req.id,
        )
    else:
        from arq.connections import ArqRedis, create_pool
        from arq.connections import RedisSettings as ArqRedisSettings

        pool: ArqRedis = await create_pool(ArqRedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("agent_review_job", str(req.id))
        await pool.aclose()

    return req


@router.get(
    "/{request_id}/objects/{object_id}/versions",
    response_model=list[OutputObjectRead],
)
async def list_object_versions(
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> list[OutputObject]:
    """List all versions of the same logical object."""
    req = await _get_request_or_404(request_id, session)
    if not auth.is_admin:
        await _assert_project_access(req.project_id, auth.user.id, session)

    obj = await session.get(OutputObject, object_id)
    if obj is None or obj.request_id != request_id:
        raise HTTPException(status_code=404, detail="Object not found")

    result = await session.exec(
        select(OutputObject)
        .where(
            OutputObject.request_id == request_id,
            OutputObject.logical_object_id == obj.logical_object_id,
        )
        .order_by(OutputObject.version)
    )
    return list(result.all())
