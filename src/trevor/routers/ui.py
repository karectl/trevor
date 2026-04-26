"""UI router — Datastar-powered HTML views."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import AuthContext, CurrentAuth, RequireAdmin
from trevor.csrf import generate_csrf_token
from trevor.database import get_session
from trevor.models.project import Project, ProjectMembership, ProjectRole
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
from trevor.models.review import Review, ReviewDecision, ReviewerType
from trevor.services import audit_service
from trevor.settings import Settings, get_settings

router = APIRouter(prefix="/ui", tags=["ui"])
logger = logging.getLogger(__name__)

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _base_ctx(request: Request, auth: AuthContext) -> dict:
    """Common template context."""
    is_admin = auth.is_admin
    is_checker = (
        any(r in ("output_checker", "senior_checker") for r in auth.realm_roles) or is_admin
    )
    settings: Settings = request.app.state.settings
    return {
        "request": request,
        "user": auth.user,
        "is_admin": is_admin,
        "is_checker": is_checker,
        "csrf_token": generate_csrf_token(settings.secret_key),
    }


async def _user_projects(
    user_id: uuid.UUID, session: AsyncSession, *, is_admin: bool = False
) -> list[Project]:
    """Projects the user has membership in (or all if admin)."""
    if is_admin:
        result = await session.exec(select(Project))
        return list(result.all())
    memberships = await session.exec(
        select(ProjectMembership.project_id).where(ProjectMembership.user_id == user_id)
    )
    pids = list(memberships.all())
    if not pids:
        return []
    result = await session.exec(select(Project).where(Project.id.in_(pids)))
    return list(result.all())


# ---------------------------------------------------------------------------
# Researcher views
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/requests", status_code=302)


@router.get("/requests", response_class=HTMLResponse)
async def request_list(
    request: Request,
    auth: CurrentAuth,
    session: Session,
    status: str | None = None,
    project_id: str | None = None,
) -> HTMLResponse:
    projects = await _user_projects(auth.user.id, session, is_admin=auth.is_admin)
    query = select(AirlockRequest)
    if not auth.is_admin:
        pids = [p.id for p in projects]
        query = query.where(AirlockRequest.project_id.in_(pids)) if pids else query.where(False)
    if status:
        query = query.where(AirlockRequest.status == status)
    if project_id:
        query = query.where(AirlockRequest.project_id == uuid.UUID(project_id))
    query = query.order_by(AirlockRequest.updated_at.desc())
    result = await session.exec(query)
    reqs = list(result.all())

    # Attach object counts as plain dicts to avoid SQLModel field validation
    req_rows = []
    for req in reqs:
        obj_result = await session.exec(
            select(OutputObject).where(
                OutputObject.request_id == req.id,
                OutputObject.state != OutputObjectState.SUPERSEDED,
            )
        )
        req_rows.append({"req": req, "object_count": len(list(obj_result.all()))})

    ctx = _base_ctx(request, auth)
    ctx.update(
        requests=req_rows,
        projects=projects,
        statuses=[s.value for s in AirlockRequestStatus],
        status_filter=status or "",
        project_id=project_id or "",
    )
    return templates.TemplateResponse("researcher/request_list.html", ctx)


@router.get("/requests/new", response_class=HTMLResponse)
async def request_create_form(
    request: Request,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    projects = await _user_projects(auth.user.id, session, is_admin=auth.is_admin)
    ctx = _base_ctx(request, auth)
    ctx["projects"] = projects
    return templates.TemplateResponse("researcher/request_create.html", ctx)


@router.post("/requests", response_class=HTMLResponse, response_model=None)
async def request_create(
    request: Request,
    auth: CurrentAuth,
    session: Session,
    project_id: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    direction: Annotated[str, Form()] = "egress",
    description: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    projects = await _user_projects(auth.user.id, session, is_admin=auth.is_admin)
    ctx = _base_ctx(request, auth)
    ctx["projects"] = projects
    ctx["form"] = {
        "project_id": project_id,
        "title": title,
        "direction": direction,
        "description": description,
    }

    # Validate
    errors: list[str] = []
    if not project_id:
        errors.append("Project is required.")
    if not title or not title.strip():
        errors.append("Title is required.")
    if direction not in ("egress", "ingress"):
        errors.append("Invalid direction.")

    if errors:
        ctx["errors"] = errors
        return templates.TemplateResponse("researcher/request_create.html", ctx, status_code=422)

    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        ctx["errors"] = ["Invalid project."]
        return templates.TemplateResponse("researcher/request_create.html", ctx, status_code=422)

    req = AirlockRequest(
        project_id=pid,
        direction=AirlockDirection(direction),
        title=title.strip(),
        description=description,
        submitted_by=auth.user.id,
    )
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.created",
        actor_id=str(auth.user.id),
        request_id=req.id,
        payload={"direction": direction, "title": title},
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{req.id}", status_code=303)


@router.get("/requests/{request_id}", response_class=HTMLResponse)
async def request_detail(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    ctx = await _build_request_detail_ctx(request, request_id, auth, session)
    return templates.TemplateResponse("researcher/request_detail.html", ctx)


async def _build_request_detail_ctx(
    request: Request,
    request_id: uuid.UUID,
    auth: AuthContext,
    session: AsyncSession,
) -> dict:
    """Shared helper to build the full request_detail template context."""
    req = await session.get(AirlockRequest, request_id)
    if not req:
        raise HTTPException(status_code=404)
    project = await session.get(Project, req.project_id)

    obj_result = await session.exec(
        select(OutputObject)
        .where(
            OutputObject.request_id == request_id,
            OutputObject.state != OutputObjectState.SUPERSEDED,
        )
        .order_by(OutputObject.uploaded_at)
    )
    objects = list(obj_result.all())

    object_metadata: dict[uuid.UUID, OutputObjectMetadata] = {}
    for obj in objects:
        meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
        if meta:
            object_metadata[obj.logical_object_id] = meta

    rev_result = await session.exec(
        select(Review).where(Review.request_id == request_id).order_by(Review.created_at)
    )
    reviews = list(rev_result.all())

    audit_result = await session.exec(
        select(AuditEvent).where(AuditEvent.request_id == request_id).order_by(AuditEvent.timestamp)
    )
    audit_events = list(audit_result.all())

    return {
        **_base_ctx(request, auth),
        "request": request,
        "airlock_request": req,
        "project": project,
        "objects": objects,
        "object_metadata": object_metadata,
        "reviews": reviews,
        "audit_events": audit_events,
        "settings": get_settings(),
    }


@router.get("/requests/{request_id}/upload", response_class=HTMLResponse)
async def object_upload_form(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    req = await session.get(AirlockRequest, request_id)
    if not req:
        raise HTTPException(status_code=404)
    ctx = _base_ctx(request, auth)
    ctx.update(request_id=request_id, request_title=req.title)
    return templates.TemplateResponse("researcher/object_upload.html", ctx)


@router.post("/requests/{request_id}/upload", response_class=HTMLResponse)
async def object_upload(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
    file: UploadFile,
    output_type: Annotated[str, Form()],
    statbarn: Annotated[str, Form()] = "",
    obj_title: Annotated[str, Form()] = "",
    obj_description: Annotated[str, Form()] = "",
    researcher_justification: Annotated[str, Form()] = "",
    suppression_notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    import hashlib

    req = await session.get(AirlockRequest, request_id)
    if not req or req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Request not in DRAFT state")

    raw = await file.read()
    checksum = hashlib.sha256(raw).hexdigest()
    logical_object_id = uuid.uuid4()
    object_id = uuid.uuid4()
    storage_key = f"{req.project_id}/{req.id}/{logical_object_id}/1/{object_id}-{file.filename}"

    if not settings.dev_auth_bypass and (settings.s3_endpoint_url or settings.s3_access_key_id):
        import io as _io

        from trevor.storage import upload_fileobj

        await upload_fileobj(
            bucket=settings.s3_quarantine_bucket,
            key=storage_key,
            fileobj=_io.BytesIO(raw),
            content_type=file.content_type or "application/octet-stream",
            settings=settings,
        )

    obj = OutputObject(
        id=object_id,
        request_id=request_id,
        version=1,
        logical_object_id=logical_object_id,
        filename=file.filename or "unknown",
        output_type=OutputType(output_type),
        statbarn=statbarn,
        storage_key=storage_key,
        checksum_sha256=checksum,
        size_bytes=len(raw),
        uploaded_by=auth.user.id,
    )
    session.add(obj)
    meta = OutputObjectMetadata(
        logical_object_id=logical_object_id,
        title=obj_title,
        description=obj_description,
        researcher_justification=researcher_justification,
        suppression_notes=suppression_notes,
    )
    session.add(meta)
    await audit_service.emit(
        session,
        event_type="object.uploaded",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"object_id": str(object_id), "filename": file.filename},
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


@router.get(
    "/requests/{request_id}/objects/{object_id}/metadata",
    response_class=HTMLResponse,
)
async def object_metadata_form(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    obj = await session.get(OutputObject, object_id)
    if not obj or obj.request_id != request_id:
        raise HTTPException(status_code=404)
    meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
    if not meta:
        meta = OutputObjectMetadata(logical_object_id=obj.logical_object_id)
    ctx = _base_ctx(request, auth)
    ctx.update(request_id=request_id, object=obj, metadata=meta)
    return templates.TemplateResponse("researcher/object_metadata.html", ctx)


@router.post(
    "/requests/{request_id}/objects/{object_id}/metadata",
    response_class=HTMLResponse,
)
async def object_metadata_save(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    researcher_justification: Annotated[str, Form()] = "",
    suppression_notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    obj = await session.get(OutputObject, object_id)
    if not obj or obj.request_id != request_id:
        raise HTTPException(status_code=404)
    meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
    if not meta:
        meta = OutputObjectMetadata(logical_object_id=obj.logical_object_id)
        session.add(meta)
    meta.title = title
    meta.description = description
    meta.researcher_justification = researcher_justification
    meta.suppression_notes = suppression_notes
    session.add(meta)
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


@router.get(
    "/requests/{request_id}/objects/{object_id}/replace",
    response_class=HTMLResponse,
)
async def object_replace_form(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    obj = await session.get(OutputObject, object_id)
    if not obj or obj.request_id != request_id:
        raise HTTPException(status_code=404)
    ctx = _base_ctx(request, auth)
    ctx.update(request_id=request_id, object=obj)
    return templates.TemplateResponse("researcher/object_replace.html", ctx)


@router.post(
    "/requests/{request_id}/objects/{object_id}/replace",
    response_class=HTMLResponse,
)
async def object_replace(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
    file: UploadFile,
    output_type: Annotated[str, Form()],
    statbarn: Annotated[str, Form()] = "",
) -> RedirectResponse:
    import hashlib

    req = await session.get(AirlockRequest, request_id)
    if not req or req.status != AirlockRequestStatus.CHANGES_REQUESTED:
        raise HTTPException(status_code=409)
    original = await session.get(OutputObject, object_id)
    if not original or original.request_id != request_id:
        raise HTTPException(status_code=404)

    raw = await file.read()
    checksum = hashlib.sha256(raw).hexdigest()
    new_version = original.version + 1
    new_id = uuid.uuid4()
    storage_key = (
        f"{req.project_id}/{req.id}/{original.logical_object_id}"
        f"/{new_version}/{new_id}-{file.filename}"
    )

    original.state = OutputObjectState.SUPERSEDED
    session.add(original)

    new_obj = OutputObject(
        id=new_id,
        request_id=request_id,
        version=new_version,
        replaces_id=original.id,
        logical_object_id=original.logical_object_id,
        filename=file.filename or "unknown",
        output_type=OutputType(output_type),
        statbarn=statbarn,
        storage_key=storage_key,
        checksum_sha256=checksum,
        size_bytes=len(raw),
        uploaded_by=auth.user.id,
    )
    session.add(new_obj)
    await audit_service.emit(
        session,
        event_type="object.replaced",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"original_id": str(original.id), "new_id": str(new_id)},
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


@router.post(
    "/requests/{request_id}/objects/{object_id}/delete",
    response_class=HTMLResponse,
)
async def object_delete(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> RedirectResponse:
    """Delete an output object from a DRAFT request."""
    req = await session.get(AirlockRequest, request_id)
    if not req or req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Request not in DRAFT state")
    obj = await session.get(OutputObject, object_id)
    if not obj or obj.request_id != request_id:
        raise HTTPException(status_code=404)

    # Delete associated metadata if no other object versions reference this logical object
    other_versions = await session.exec(
        select(OutputObject).where(
            OutputObject.logical_object_id == obj.logical_object_id,
            OutputObject.id != object_id,
        )
    )
    if not list(other_versions.all()):
        meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
        if meta:
            await session.delete(meta)

    await audit_service.emit(
        session,
        event_type="object.deleted",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"object_id": str(object_id), "filename": obj.filename},
    )
    await session.delete(obj)
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/submit", response_class=HTMLResponse)
async def request_submit(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
) -> RedirectResponse:
    from datetime import UTC, datetime

    req = await session.get(AirlockRequest, request_id)
    if not req or req.status != AirlockRequestStatus.DRAFT:
        raise HTTPException(status_code=409)
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
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/resubmit", response_class=HTMLResponse)
async def request_resubmit(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    settings: SettingsDep,
) -> RedirectResponse:
    from datetime import UTC, datetime

    req = await session.get(AirlockRequest, request_id)
    if not req or req.status != AirlockRequestStatus.CHANGES_REQUESTED:
        raise HTTPException(status_code=409)
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
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


@router.post("/requests/{request_id}/release", response_class=HTMLResponse)
async def request_release(
    request: Request,
    request_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
) -> RedirectResponse:
    req = await session.get(AirlockRequest, request_id)
    if not req or req.status != AirlockRequestStatus.APPROVED:
        raise HTTPException(status_code=409)
    # Minimal release — just mark as releasing (real release via ARQ job)
    req.status = AirlockRequestStatus.RELEASING
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.release_started",
        actor_id=str(auth.user.id),
        request_id=req.id,
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


# ---------------------------------------------------------------------------
# Checker views
# ---------------------------------------------------------------------------


def _humanize_timedelta(dt: datetime) -> str:
    """Rough human-readable time since *dt*."""
    from datetime import UTC

    delta = datetime.now(UTC).replace(tzinfo=None) - dt.replace(tzinfo=None)
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "< 1 hour"
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d {hours % 24}h"


async def _checker_project_ids(
    user_id: uuid.UUID, session: AsyncSession, *, is_admin: bool
) -> list[uuid.UUID]:
    """Project IDs where user holds a checker role (or all if admin)."""
    if is_admin:
        result = await session.exec(select(Project.id))
        return list(result.all())
    memberships = await session.exec(
        select(ProjectMembership.project_id).where(
            ProjectMembership.user_id == user_id,
            ProjectMembership.role.in_([
                ProjectRole.OUTPUT_CHECKER,
                ProjectRole.SENIOR_CHECKER,
            ]),
        )
    )
    return list(memberships.all())


@router.get("/review", response_class=HTMLResponse)
async def review_project_list(
    request: Request,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    from sqlmodel import func

    pids = await _checker_project_ids(auth.user.id, session, is_admin=auth.is_admin)
    projects_info: list[dict] = []
    for pid in pids:
        project = await session.get(Project, pid)
        if not project:
            continue
        # Count pending HUMAN_REVIEW requests
        count_result = await session.exec(
            select(func.count(AirlockRequest.id)).where(
                AirlockRequest.project_id == pid,
                AirlockRequest.status == AirlockRequestStatus.HUMAN_REVIEW,
            )
        )
        pending_count = count_result.one()
        # Oldest waiting
        oldest_result = await session.exec(
            select(AirlockRequest.updated_at)
            .where(
                AirlockRequest.project_id == pid,
                AirlockRequest.status == AirlockRequestStatus.HUMAN_REVIEW,
            )
            .order_by(AirlockRequest.updated_at)
            .limit(1)
        )
        oldest = oldest_result.first()
        oldest_wait = _humanize_timedelta(oldest) if oldest else None
        projects_info.append({
            "project": project,
            "pending_count": pending_count,
            "oldest_wait": oldest_wait,
        })

    # Sort: most pending first
    projects_info.sort(key=lambda p: p["pending_count"], reverse=True)

    ctx = _base_ctx(request, auth)
    ctx["projects"] = projects_info
    return templates.TemplateResponse("checker/project_list.html", ctx)


@router.get("/review/project/{project_id}", response_class=HTMLResponse)
async def review_request_list(
    request: Request,
    project_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)

    query = (
        select(AirlockRequest)
        .where(
            AirlockRequest.project_id == project_id,
            AirlockRequest.status == AirlockRequestStatus.HUMAN_REVIEW,
        )
        .order_by(AirlockRequest.updated_at)
    )
    result = await session.exec(query)
    reqs = list(result.all())

    for req in reqs:
        obj_result = await session.exec(
            select(OutputObject).where(
                OutputObject.request_id == req.id,
                OutputObject.state != OutputObjectState.SUPERSEDED,
            )
        )
        req.object_count = len(list(obj_result.all()))  # type: ignore[attr-defined]
        agent_rev = await session.exec(
            select(Review).where(
                Review.request_id == req.id,
                Review.reviewer_type == ReviewerType.AGENT,
            )
        )
        ar = agent_rev.first()
        req.agent_decision = ar.decision if ar else None  # type: ignore[attr-defined]

    ctx = _base_ctx(request, auth)
    ctx.update(project=project, requests=reqs)
    return templates.TemplateResponse("checker/request_list.html", ctx)


@router.get("/review/{request_id}", response_class=HTMLResponse)
async def review_form(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    req = await session.get(AirlockRequest, request_id)
    if not req:
        raise HTTPException(status_code=404)
    obj_result = await session.exec(
        select(OutputObject)
        .where(
            OutputObject.request_id == request_id,
            OutputObject.state != OutputObjectState.SUPERSEDED,
        )
        .order_by(OutputObject.uploaded_at)
    )
    objects = list(obj_result.all())

    # Fetch metadata for each object
    object_metadata: dict[uuid.UUID, OutputObjectMetadata] = {}
    for obj in objects:
        meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
        if meta:
            object_metadata[obj.logical_object_id] = meta

    # Agent review
    agent_result = await session.exec(
        select(Review).where(
            Review.request_id == request_id,
            Review.reviewer_type == ReviewerType.AGENT,
        )
    )
    agent_review = agent_result.first()

    # Parse agent assessments from findings (keyed by object_id)
    agent_assessments: dict[str, dict] = {}
    if agent_review and agent_review.findings:
        for finding in agent_review.findings:
            if isinstance(finding, dict) and "object_id" in finding:
                agent_assessments[str(finding["object_id"])] = finding

    # File previews — best-effort; skip when S3 is not configured
    settings_val: Settings = get_settings()
    object_previews: dict[str, dict] = {}
    if settings_val.s3_endpoint_url or settings_val.s3_access_key_id:
        from trevor.services.preview_service import render_preview
        from trevor.storage import download_object

        for obj in objects:
            try:
                content = await download_object(
                    bucket=settings_val.s3_quarantine_bucket,
                    key=obj.storage_key,
                    settings=settings_val,
                )
                preview = render_preview(obj.filename, content)
                if preview:
                    object_previews[str(obj.id)] = preview
            except Exception:
                logger.debug("preview unavailable for %s", obj.filename, exc_info=True)

    ctx = _base_ctx(request, auth)
    ctx.update(
        airlock_request=req,
        objects=objects,
        object_metadata=object_metadata,
        agent_review=agent_review,
        agent_assessments=agent_assessments,
        object_previews=object_previews,
    )
    return templates.TemplateResponse("checker/review_form.html", ctx)


@router.post("/review/{request_id}", response_class=HTMLResponse)
async def review_submit(
    request: Request,
    request_id: uuid.UUID,
    auth: CurrentAuth,
    session: Session,
    decision: Annotated[str, Form()],
    summary: Annotated[str, Form()] = "",
) -> RedirectResponse:
    req = await session.get(AirlockRequest, request_id)
    if not req:
        raise HTTPException(status_code=404)

    # Parse per-object decisions from form
    form_data = await request.form()
    per_object: list[dict] = []
    for key, val in form_data.items():
        if key.startswith("obj_") and key.endswith("_decision"):
            oid = key.replace("obj_", "").replace("_decision", "")
            feedback_key = f"obj_{oid}_feedback"
            per_object.append({
                "object_id": oid,
                "decision": val,
                "feedback": form_data.get(feedback_key, ""),
            })

    review = Review(
        request_id=request_id,
        reviewer_id=auth.user.id,
        reviewer_type=ReviewerType.HUMAN,
        decision=ReviewDecision(decision),
        summary=summary,
        findings=per_object,
    )
    session.add(review)

    # Apply per-object decisions
    for pod in per_object:
        obj = await session.get(OutputObject, uuid.UUID(pod["object_id"]))
        if obj:
            obj.state = OutputObjectState(pod["decision"].upper())
            session.add(obj)

    # Update request status based on overall decision
    status_map = {
        "approved": AirlockRequestStatus.APPROVED,
        "rejected": AirlockRequestStatus.REJECTED,
        "changes_requested": AirlockRequestStatus.CHANGES_REQUESTED,
    }
    req.status = status_map.get(decision, AirlockRequestStatus.HUMAN_REVIEW)
    session.add(req)

    await audit_service.emit(
        session,
        event_type="review.submitted",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"decision": decision},
    )
    await session.commit()
    return RedirectResponse(f"/ui/review/project/{req.project_id}", status_code=303)


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
async def admin_overview(
    request: Request,
    auth: RequireAdmin,
    session: Session,
    status: str | None = None,
) -> HTMLResponse:
    query = select(AirlockRequest)
    if status:
        query = query.where(AirlockRequest.status == status)
    query = query.order_by(AirlockRequest.updated_at.desc())
    result = await session.exec(query)
    reqs = list(result.all())
    ctx = _base_ctx(request, auth)
    ctx.update(
        requests=reqs,
        statuses=[s.value for s in AirlockRequestStatus],
        status_filter=status or "",
    )
    return templates.TemplateResponse("admin/request_overview.html", ctx)


@router.get("/admin/metrics", response_class=HTMLResponse)
async def admin_metrics(
    request: Request,
    auth: RequireAdmin,
    session: Session,
    settings: SettingsDep,
) -> HTMLResponse:
    from trevor.services.metrics_service import compute_metrics

    metrics = await compute_metrics(session, stuck_hours=settings.stuck_request_hours)
    ctx = _base_ctx(request, auth)
    ctx.update(metrics=metrics, stuck_hours=settings.stuck_request_hours)
    return templates.TemplateResponse("admin/metrics_dashboard.html", ctx)


@router.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit(
    request: Request,
    auth: RequireAdmin,
    session: Session,
    event_type: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    page_size = 50
    query = select(AuditEvent)
    if event_type:
        query = query.where(AuditEvent.event_type.startswith(event_type))
    query = query.order_by(AuditEvent.timestamp.desc())
    # Simple pagination
    all_result = await session.exec(query)
    all_events = list(all_result.all())
    total = len(all_events)
    total_pages = max(1, (total + page_size - 1) // page_size)
    events = all_events[(page - 1) * page_size : page * page_size]

    ctx = _base_ctx(request, auth)
    ctx.update(
        events=events,
        event_type_filter=event_type or "",
        page=page,
        total_pages=total_pages,
        base_url="/ui/admin/audit",
        filter_params={"event_type": event_type} if event_type else {},
    )
    return templates.TemplateResponse("admin/audit_log.html", ctx)


@router.get("/admin/memberships/{project_id}", response_class=HTMLResponse)
async def admin_memberships(
    request: Request,
    project_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
) -> HTMLResponse:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404)
    mem_result = await session.exec(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id)
    )
    memberships = list(mem_result.all())

    from trevor.models.user import User

    user_result = await session.exec(select(User))
    users = list(user_result.all())

    ctx = _base_ctx(request, auth)
    user_map = {str(u.id): u.username for u in users}
    ctx.update(
        project=project,
        memberships=memberships,
        users=users,
        user_map=user_map,
        error_message=None,
    )
    return templates.TemplateResponse("admin/membership_manage.html", ctx)


@router.post("/admin/memberships", response_class=HTMLResponse)
async def admin_membership_create(
    request: Request,
    auth: RequireAdmin,
    session: Session,
    project_id: Annotated[str, Form()],
    user_id: Annotated[str, Form()],
    role: Annotated[str, Form()],
) -> RedirectResponse:
    from trevor.services.membership_service import validate_no_role_conflict

    pid = uuid.UUID(project_id)
    uid = uuid.UUID(user_id)
    try:
        await validate_no_role_conflict(uid, pid, ProjectRole(role), session)
    except HTTPException:
        return RedirectResponse(f"/ui/admin/memberships/{project_id}", status_code=303)
    membership = ProjectMembership(
        user_id=uid,
        project_id=pid,
        role=ProjectRole(role),
        assigned_by=auth.user.id,
    )
    session.add(membership)
    await session.commit()
    return RedirectResponse(f"/ui/admin/memberships/{project_id}", status_code=303)


@router.post(
    "/admin/memberships/{membership_id}/delete",
    response_class=HTMLResponse,
)
async def admin_membership_delete(
    request: Request,
    membership_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
) -> RedirectResponse:
    membership = await session.get(ProjectMembership, membership_id)
    if not membership:
        raise HTTPException(status_code=404)
    project_id = membership.project_id
    await session.delete(membership)
    await session.commit()
    return RedirectResponse(f"/ui/admin/memberships/{project_id}", status_code=303)


# ---------------------------------------------------------------------------
# Ingress admin views
# ---------------------------------------------------------------------------


@router.get("/ingress/new", response_class=HTMLResponse)
async def ingress_create_form(
    request: Request,
    auth: RequireAdmin,
    session: Session,
) -> HTMLResponse:
    projects = await _user_projects(auth.user.id, session, is_admin=auth.is_admin)
    ctx = _base_ctx(request, auth)
    ctx["projects"] = projects
    return templates.TemplateResponse("admin/ingress_create.html", ctx)


@router.post("/requests/ingress", response_class=HTMLResponse)
async def ingress_create(
    request: Request,
    auth: RequireAdmin,
    session: Session,
    project_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
) -> RedirectResponse:
    req = AirlockRequest(
        project_id=uuid.UUID(project_id),
        direction=AirlockDirection.INGRESS,
        title=title,
        description=description,
        submitted_by=auth.user.id,
    )
    session.add(req)
    await audit_service.emit(
        session,
        event_type="request.created",
        actor_id=str(auth.user.id),
        request_id=req.id,
        payload={"direction": "ingress", "title": title},
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{req.id}/ingress-upload", status_code=303)


@router.get("/requests/{request_id}/ingress-upload", response_class=HTMLResponse)
async def ingress_upload_manage(
    request: Request,
    request_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
) -> HTMLResponse:
    req = await session.get(AirlockRequest, request_id)
    if not req:
        raise HTTPException(status_code=404)
    if req.direction != AirlockDirection.INGRESS:
        raise HTTPException(status_code=409, detail="Not an ingress request")

    obj_result = await session.exec(
        select(OutputObject)
        .where(OutputObject.request_id == request_id)
        .order_by(OutputObject.uploaded_at)
    )
    objects = list(obj_result.all())

    ctx = _base_ctx(request, auth)
    ctx.update(airlock_request=req, objects=objects, output_types=[t.value for t in OutputType])
    return templates.TemplateResponse("admin/ingress_upload.html", ctx)


@router.post("/requests/{request_id}/ingress-upload", response_class=HTMLResponse)
async def ingress_add_object_slot(
    request: Request,
    request_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
    filename: Annotated[str, Form()],
    output_type: Annotated[str, Form()],
) -> RedirectResponse:
    req = await session.get(AirlockRequest, request_id)
    if not req or req.direction != AirlockDirection.INGRESS:
        raise HTTPException(status_code=409)

    logical_object_id = uuid.uuid4()
    object_id = uuid.uuid4()
    storage_key = f"{req.project_id}/{req.id}/{logical_object_id}/1/{object_id}-{filename}"

    obj = OutputObject(
        id=object_id,
        request_id=request_id,
        version=1,
        logical_object_id=logical_object_id,
        filename=filename,
        output_type=OutputType(output_type),
        statbarn="",
        storage_key=storage_key,
        checksum_sha256="",
        size_bytes=0,
        uploaded_by=auth.user.id,
    )
    session.add(obj)
    meta = OutputObjectMetadata(logical_object_id=logical_object_id)
    session.add(meta)
    await audit_service.emit(
        session,
        event_type="object.slot_created",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"filename": filename, "object_id": str(object_id)},
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}/ingress-upload", status_code=303)


@router.post(
    "/requests/{request_id}/objects/{object_id}/generate-url",
    response_class=HTMLResponse,
)
async def ingress_generate_url(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
    settings: SettingsDep,
) -> HTMLResponse:
    req = await session.get(AirlockRequest, request_id)
    obj = await session.get(OutputObject, object_id)
    if not req or not obj or obj.request_id != request_id:
        raise HTTPException(status_code=404)

    from datetime import UTC, datetime

    if settings.dev_auth_bypass:
        upload_url = f"https://mock-s3.example.com/{obj.storage_key}?presigned=put"
    else:
        from trevor.storage import generate_presigned_put_url

        upload_url = await generate_presigned_put_url(
            bucket=settings.s3_quarantine_bucket,
            key=obj.storage_key,
            expires_in=3600,
            settings=settings,
        )

    obj.upload_url_generated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(obj)
    await audit_service.emit(
        session,
        event_type="object.upload_url_generated",
        actor_id=str(auth.user.id),
        request_id=request_id,
        payload={"object_id": str(object_id)},
    )
    await session.commit()

    ctx = _base_ctx(request, auth)
    ctx.update(upload_url=upload_url, object=obj, request_id=request_id)
    return templates.TemplateResponse("admin/ingress_upload_url.html", ctx)


@router.post(
    "/requests/{request_id}/objects/{object_id}/confirm",
    response_class=HTMLResponse,
)
async def ingress_confirm_upload(
    request: Request,
    request_id: uuid.UUID,
    object_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
    settings: SettingsDep,
) -> RedirectResponse:
    import hashlib

    req = await session.get(AirlockRequest, request_id)
    obj = await session.get(OutputObject, object_id)
    if not req or not obj or obj.request_id != request_id:
        raise HTTPException(status_code=404)
    if obj.upload_url_generated_at is None:
        raise HTTPException(status_code=409, detail="Upload URL not yet generated")

    if settings.dev_auth_bypass:
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
        payload={"object_id": str(object_id), "checksum_sha256": obj.checksum_sha256},
    )
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}/ingress-upload", status_code=303)


@router.post("/requests/{request_id}/deliver", response_class=HTMLResponse)
async def ingress_deliver(
    request: Request,
    request_id: uuid.UUID,
    auth: RequireAdmin,
    session: Session,
    settings: SettingsDep,
) -> RedirectResponse:
    from datetime import UTC, datetime

    from trevor.models.release import DeliveryRecord

    req = await session.get(AirlockRequest, request_id)
    if not req or req.direction != AirlockDirection.INGRESS:
        raise HTTPException(status_code=409)
    if req.status != AirlockRequestStatus.APPROVED:
        raise HTTPException(status_code=409)

    objects_result = await session.exec(
        select(OutputObject).where(
            OutputObject.request_id == request_id,
            OutputObject.state == OutputObjectState.APPROVED,
        )
    )
    approved_objects = list(objects_result.all())

    now = datetime.now(UTC).replace(tzinfo=None)
    record = DeliveryRecord(
        request_id=request_id,
        delivered_at=now,
        delivered_by=auth.user.id,
        delivery_metadata={"object_count": len(approved_objects)},
    )
    session.add(record)
    req.status = AirlockRequestStatus.RELEASED
    req.closed_at = now
    session.add(req)
    await session.commit()
    return RedirectResponse(f"/ui/requests/{request_id}", status_code=303)


# ---------------------------------------------------------------------------
# Notification list UI
# ---------------------------------------------------------------------------


@router.get("/notifications", response_class=HTMLResponse)
async def notification_list(
    request: Request,
    auth: CurrentAuth,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Render the notification inbox for the current user."""
    from trevor.models.notification import Notification

    result = await session.exec(
        select(Notification)
        .where(Notification.user_id == auth.user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    notifications = result.all()
    ctx = _base_ctx(request, auth)
    ctx["notifications"] = notifications
    return templates.TemplateResponse(request, "notifications/list.html", ctx)


@router.post("/notifications/{notification_id}/read", response_class=HTMLResponse)
async def notification_mark_read(
    notification_id: uuid.UUID,
    auth: CurrentAuth,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RedirectResponse:
    """Mark a notification as read (form POST from UI)."""
    from trevor.models.notification import Notification

    notification = await session.get(Notification, notification_id)
    if notification and notification.user_id == auth.user.id:
        notification.read = True
        session.add(notification)
        await session.commit()
    return RedirectResponse("/ui/notifications", status_code=303)


@router.post("/notifications/mark-all-read", response_class=HTMLResponse)
async def notification_mark_all_read(
    auth: CurrentAuth,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RedirectResponse:
    """Mark all notifications read (form POST from UI)."""
    from sqlmodel import select

    from trevor.models.notification import Notification

    result = await session.exec(
        select(Notification).where(
            Notification.user_id == auth.user.id,
            Notification.read == False,  # noqa: E712
        )
    )
    for n in result.all():
        n.read = True
        session.add(n)
    await session.commit()
    return RedirectResponse("/ui/notifications", status_code=303)
