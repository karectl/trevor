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


async def url_expiry_warning_job(ctx: dict[str, Any]) -> None:
    """Cron — check for ReleaseRecords with pre-signed URLs expiring soon.

    Iteration 6 will implement URL expiry notifications here.
    """
    logger.info("url_expiry_warning_job ran (stub)")


# ---------------------------------------------------------------------------
# Startup / shutdown hooks
# ---------------------------------------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    ctx["session_factory"] = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    ctx["settings"] = settings
    logger.info("ARQ worker starting up")


async def shutdown(ctx: dict[str, Any]) -> None:  # noqa: RUF029
    logger.info("ARQ worker shutting down")


# ---------------------------------------------------------------------------
# WorkerSettings
# ---------------------------------------------------------------------------


class WorkerSettings:
    settings = get_settings()
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    functions = [agent_review_job]
    cron_jobs = [
        cron(url_expiry_warning_job, hour={0}, minute=0, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
