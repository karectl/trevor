"""UI router — Datastar-powered HTML views."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import AuthContext, CurrentAuth, RequireAdmin
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
    return {
        "request": request,
        "user": auth.user,
        "is_admin": is_admin,
        "is_checker": is_checker,
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

    # Attach object counts
    for req in reqs:
        obj_result = await session.exec(
            select(OutputObject).where(
                OutputObject.request_id == req.id,
                OutputObject.state != OutputObjectState.SUPERSEDED,
            )
        )
        req.object_count = len(list(obj_result.all()))  # type: ignore[attr-defined]

    ctx = _base_ctx(request, auth)
    ctx.update(
        requests=reqs,
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


@router.post("/requests", response_class=HTMLResponse)
async def request_create(
    request: Request,
    auth: CurrentAuth,
    session: Session,
    project_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
    direction: Annotated[str, Form()] = "egress",
    description: Annotated[str, Form()] = "",
) -> RedirectResponse:
    pid = uuid.UUID(project_id)
    req = AirlockRequest(
        project_id=pid,
        direction=AirlockDirection(direction),
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

    rev_result = await session.exec(
        select(Review).where(Review.request_id == request_id).order_by(Review.created_at)
    )
    reviews = list(rev_result.all())

    audit_result = await session.exec(
        select(AuditEvent).where(AuditEvent.request_id == request_id).order_by(AuditEvent.timestamp)
    )
    audit_events = list(audit_result.all())

    ctx = _base_ctx(request, auth)
    # Template uses 'request' for both FastAPI Request and AirlockRequest.
    # Rename the domain object.
    ctx.update(
        request=request,  # keep Starlette request for url generation
        airlock_request=req,
        project=project,
        objects=objects,
        reviews=reviews,
        audit_events=audit_events,
    )
    # The template references {{ request.* }} for both — we'll fix by passing as 'req'
    # Actually, Jinja2Templates needs 'request' as Starlette Request.
    # We pass domain object as separate name.
    return templates.TemplateResponse(
        "researcher/request_detail.html",
        {
            **_base_ctx(request, auth),
            "request": request,  # Starlette
            "airlock_request": req,
            "project": project,
            "objects": objects,
            "reviews": reviews,
            "audit_events": audit_events,
        },
    )


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
    meta = OutputObjectMetadata(logical_object_id=logical_object_id)
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
    req.submitted_at = datetime.now(UTC)
    req.updated_at = datetime.now(UTC)
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
    req.submitted_at = datetime.now(UTC)
    req.updated_at = datetime.now(UTC)
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


@router.get("/review", response_class=HTMLResponse)
async def review_queue(
    request: Request,
    auth: CurrentAuth,
    session: Session,
) -> HTMLResponse:
    query = select(AirlockRequest).where(AirlockRequest.status == AirlockRequestStatus.HUMAN_REVIEW)
    if not auth.is_admin:
        # Only projects where user is checker
        memberships = await session.exec(
            select(ProjectMembership.project_id).where(
                ProjectMembership.user_id == auth.user.id,
                ProjectMembership.role.in_([
                    ProjectRole.OUTPUT_CHECKER,
                    ProjectRole.SENIOR_CHECKER,
                ]),
            )
        )
        pids = list(memberships.all())
        query = query.where(AirlockRequest.project_id.in_(pids)) if pids else query.where(False)
    query = query.order_by(AirlockRequest.updated_at)
    result = await session.exec(query)
    reqs = list(result.all())

    # Attach object counts and agent decisions
    for req in reqs:
        obj_result = await session.exec(
            select(OutputObject).where(
                OutputObject.request_id == req.id,
                OutputObject.state != OutputObjectState.SUPERSEDED,
            )
        )
        req.object_count = len(list(obj_result.all()))  # type: ignore[attr-defined]
        # Find agent review
        agent_rev = await session.exec(
            select(Review).where(
                Review.request_id == req.id,
                Review.reviewer_type == ReviewerType.AGENT,
            )
        )
        ar = agent_rev.first()
        req.agent_decision = ar.decision if ar else None  # type: ignore[attr-defined]

    ctx = _base_ctx(request, auth)
    ctx["requests"] = reqs
    return templates.TemplateResponse("checker/review_queue.html", ctx)


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

    # Agent review
    agent_result = await session.exec(
        select(Review).where(
            Review.request_id == request_id,
            Review.reviewer_type == ReviewerType.AGENT,
        )
    )
    agent_review = agent_result.first()

    ctx = _base_ctx(request, auth)
    # 'request' key is Starlette request; pass domain object separately
    ctx.update(
        airlock_request=req,
        objects=objects,
        agent_review=agent_review,
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
    return RedirectResponse("/ui/review", status_code=303)


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
    ctx.update(
        project=project,
        memberships=memberships,
        users=users,
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
