"""ARQ worker — task queue backed by Redis.

Jobs are registered here. Import this module as the ARQ WorkerSettings path.
Run with: uv run arq trevor.worker.WorkerSettings
"""

from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.notification import NotificationEventType
from trevor.settings import Settings, get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------


async def agent_review_job(ctx: dict[str, Any], request_id: str) -> None:
    """Run automated agent review on a submitted AirlockRequest."""
    from trevor.agent.agent import AGENT_ACTOR_ID, run_agent_review
    from trevor.agent.rules import assess_object
    from trevor.models.request import (
        AirlockRequest,
        AirlockRequestStatus,
        OutputObject,
        OutputObjectMetadata,
        OutputObjectState,
    )
    from trevor.models.review import Review, ReviewDecision, ReviewerType
    from trevor.services import audit_service

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings: Settings = ctx["settings"]

    async with session_factory() as session:
        import uuid

        req_uuid = uuid.UUID(request_id)
        req = await session.get(AirlockRequest, req_uuid)
        if req is None:
            logger.error("agent_review_job: request %s not found", request_id)
            return
        if req.status != AirlockRequestStatus.SUBMITTED:
            logger.warning(
                "agent_review_job: request %s in status %s, expected SUBMITTED",
                request_id,
                req.status,
            )
            return

        # Transition → AGENT_REVIEW
        req.status = AirlockRequestStatus.AGENT_REVIEW
        session.add(req)
        await audit_service.emit(
            session,
            event_type="request.agent_review_started",
            actor_id=AGENT_ACTOR_ID,
            request_id=req.id,
        )
        await session.commit()

        try:
            # Load output objects
            result = await session.exec(
                select(OutputObject).where(
                    OutputObject.request_id == req.id,
                    OutputObject.state == OutputObjectState.PENDING,
                )
            )
            objects = list(result.all())

            assessments: list[tuple[Any, str]] = []
            for obj in objects:
                # Load metadata
                meta = await session.get(OutputObjectMetadata, obj.logical_object_id)
                if meta is None:
                    meta = OutputObjectMetadata(logical_object_id=obj.logical_object_id)

                # Fetch file content from S3 (skip in dev mode)
                file_content = b""
                if not settings.dev_auth_bypass:
                    from trevor.storage import download_object

                    file_content = await download_object(
                        bucket=settings.s3_quarantine_bucket,
                        key=obj.storage_key,
                        settings=settings,
                    )

                assessment = assess_object(
                    object_id=obj.id,
                    output_type=obj.output_type,
                    statbarn=obj.statbarn,
                    file_content=file_content,
                    filename=obj.filename,
                    metadata=meta,
                    min_cell_count=settings.agent_min_cell_count,
                    dominance_p=settings.agent_dominance_p,
                )
                assessments.append((assessment, obj.filename))

            # Run agent (rule results → optional LLM → review data)
            review_data = await run_agent_review(
                assessments,
                llm_enabled=settings.agent_llm_enabled,
                openai_base_url=settings.agent_openai_base_url,
                model_name=settings.agent_model_name,
                api_key=settings.agent_api_key,
            )

            # Create Review record
            review = Review(
                request_id=req.id,
                reviewer_id=None,
                reviewer_type=ReviewerType.AGENT,
                decision=ReviewDecision(review_data["decision"]),
                summary=review_data["summary"],
                findings=review_data["findings"],
            )
            session.add(review)

            # Transition → HUMAN_REVIEW
            req.status = AirlockRequestStatus.HUMAN_REVIEW
            session.add(req)
            await audit_service.emit(
                session,
                event_type="review.created",
                actor_id=AGENT_ACTOR_ID,
                request_id=req.id,
                payload={"review_id": str(review.id), "decision": review_data["decision"]},
            )
            await session.commit()
            logger.info("agent_review_job: completed for request %s", request_id)

            # Notify checkers that agent review is ready
            if "redis" in ctx:
                await ctx["redis"].enqueue_job(
                    "send_notifications_job", "agent_review.ready", request_id
                )

        except Exception:
            logger.exception("agent_review_job: failed for request %s", request_id)
            # Emit failure audit event
            async with session_factory() as err_session:
                req2 = await err_session.get(AirlockRequest, req_uuid)
                if req2:
                    await audit_service.emit(
                        err_session,
                        event_type="request.agent_review_failed",
                        actor_id=AGENT_ACTOR_ID,
                        request_id=req2.id,
                        payload={"error": "Agent review job failed"},
                    )
                    await err_session.commit()
            raise


async def release_job(ctx: dict[str, Any], request_id: str) -> None:
    """Assemble RO-Crate and release an approved request."""
    from trevor.services.release_service import assemble_and_release

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    settings: Settings = ctx["settings"]

    import uuid

    req_uuid = uuid.UUID(request_id)

    async with session_factory() as session:
        try:
            await assemble_and_release(req_uuid, session, settings)
            logger.info("release_job: completed for request %s", request_id)
            # Notify researcher that release is complete
            if "redis" in ctx:
                await ctx["redis"].enqueue_job(
                    "send_notifications_job", "request.released", request_id
                )
        except Exception:
            logger.exception("release_job: failed for request %s", request_id)
            raise


async def send_notifications_job(
    ctx: dict[str, Any],
    event_type: str,
    request_id: str,
) -> None:
    """Dispatch a notification event for a given request and event type."""
    from trevor.models.request import AirlockRequest
    from trevor.services.notification_service import NotificationRouter, create_event, get_router

    settings: Settings = ctx["settings"]
    if not settings.notifications_enabled:
        logger.debug("send_notifications_job: notifications disabled, skipping")
        return

    import uuid as _uuid

    req_uuid = _uuid.UUID(request_id)
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]

    async with session_factory() as session:
        req = await session.get(AirlockRequest, req_uuid)
        if req is None:
            logger.error("send_notifications_job: request %s not found", request_id)
            return

        event = await create_event(event_type, req, session)
        if not event.recipient_user_ids:
            logger.debug("send_notifications_job: no recipients for %s", event_type)
            return

        router: NotificationRouter = ctx.get("notification_router") or get_router(settings)
        await router.dispatch(event, session)
        await session.commit()
        logger.info(
            "send_notifications_job: dispatched %s for request %s to %d recipients",
            event_type,
            request_id,
            len(event.recipient_user_ids),
        )


async def url_expiry_warning_job(ctx: dict[str, Any]) -> None:
    """Cron — notify researchers about pre-signed URLs expiring within warning window."""
    from datetime import UTC, datetime, timedelta

    from sqlmodel import select

    from trevor.models.release import ReleaseRecord
    from trevor.models.request import AirlockRequest
    from trevor.services.notification_service import NotificationRouter, create_event, get_router

    settings: Settings = ctx["settings"]
    if not settings.notifications_enabled:
        return

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    now = datetime.now(UTC).replace(tzinfo=None)
    warn_before = now + timedelta(hours=settings.url_expiry_warning_hours)

    async with session_factory() as session:
        result = await session.exec(
            select(ReleaseRecord).where(
                ReleaseRecord.url_expires_at != None,  # noqa: E711
                ReleaseRecord.url_expires_at <= warn_before,
                ReleaseRecord.url_expires_at > now,
                ReleaseRecord.expiry_warned_at == None,  # noqa: E711
            )
        )
        records = list(result.all())

    warned = 0
    for record in records:
        async with session_factory() as session:
            req = await session.get(AirlockRequest, record.request_id)
            if req is None:
                continue
            event = await create_event(NotificationEventType.PRESIGNED_URL_EXPIRING, req, session)
            if event.recipient_user_ids:
                router: NotificationRouter = ctx.get("notification_router") or get_router(settings)
                await router.dispatch(event, session)
            # Mark warned regardless of recipients to prevent re-dispatch
            rec = await session.get(ReleaseRecord, record.id)
            if rec:
                rec.expiry_warned_at = datetime.now(UTC).replace(tzinfo=None)
                session.add(rec)
            await session.commit()
            warned += 1

    logger.info("url_expiry_warning_job: warned for %d release(s)", warned)


async def stuck_request_alert_job(ctx: dict[str, Any]) -> None:
    """Cron — alert checkers about requests stuck in review beyond SLA threshold."""
    from datetime import UTC, datetime, timedelta

    from sqlmodel import select

    from trevor.models.request import AirlockRequest, AirlockRequestStatus
    from trevor.services.notification_service import NotificationRouter, create_event, get_router

    settings: Settings = ctx["settings"]
    if not settings.notifications_enabled:
        return

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    now = datetime.now(UTC).replace(tzinfo=None)
    threshold = now - timedelta(hours=settings.stuck_request_hours)

    stuck_statuses = [AirlockRequestStatus.SUBMITTED, AirlockRequestStatus.HUMAN_REVIEW]

    async with session_factory() as session:
        result = await session.exec(
            select(AirlockRequest).where(
                AirlockRequest.status.in_(stuck_statuses),  # type: ignore[attr-defined]
                AirlockRequest.updated_at <= threshold,
            )
        )
        stuck = list(result.all())

    alerted = 0
    for req in stuck:
        async with session_factory() as session:
            event = await create_event(NotificationEventType.REQUEST_STUCK, req, session)
            if event.recipient_user_ids:
                router: NotificationRouter = ctx.get("notification_router") or get_router(settings)
                await router.dispatch(event, session)
                await session.commit()
                alerted += 1

    logger.info("stuck_request_alert_job: alerted for %d stuck request(s)", alerted)


async def crd_sync_job(ctx: dict[str, Any]) -> None:
    """Cron — reconcile CR8TOR CRDs into trevor DB every 5 minutes."""
    from trevor.crd import list_group_crds, list_project_crds, list_user_crds
    from trevor.services.crd_sync_service import full_reconcile

    settings: Settings = ctx["settings"]
    if not settings.crd_sync_enabled:
        logger.debug("crd_sync_job: disabled, skipping")
        return

    try:
        project_crds = await list_project_crds(settings.crd_namespace)
        group_crds = await list_group_crds(settings.crd_namespace)
        user_crds = await list_user_crds(settings.crd_namespace)
    except Exception:
        logger.exception("crd_sync_job: failed to list CRDs")
        return

    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    async with session_factory() as session:
        stats = await full_reconcile(project_crds, group_crds, user_crds, session)
        logger.info("crd_sync_job: %s", stats)


# ---------------------------------------------------------------------------
# Startup / shutdown hooks
# ---------------------------------------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:
    from trevor.services.notification_service import get_router

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    ctx["session_factory"] = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    ctx["settings"] = settings
    ctx["notification_router"] = get_router(settings)
    logger.info("ARQ worker starting up")


async def shutdown(ctx: dict[str, Any]) -> None:  # noqa: RUF029
    logger.info("ARQ worker shutting down")


# ---------------------------------------------------------------------------
# WorkerSettings
# ---------------------------------------------------------------------------


class WorkerSettings:
    settings = get_settings()
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    functions = [agent_review_job, release_job, send_notifications_job]
    cron_jobs = [
        cron(url_expiry_warning_job, hour={0}, minute=0, run_at_startup=False),
        cron(stuck_request_alert_job, hour={6}, minute=0, run_at_startup=False),
        cron(
            crd_sync_job,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
