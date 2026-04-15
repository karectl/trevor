"""ARQ worker — task queue backed by Redis.

Jobs are registered here. Import this module as the ARQ WorkerSettings path.
Run with: uv run arq trevor.worker.WorkerSettings
"""

from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from trevor.settings import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------


async def agent_review_job(ctx: dict[str, Any], request_id: str) -> None:
    """Placeholder — runs automated review on a submitted AirlockRequest.

    Iteration 3 will implement the statbarn rule engine here.
    """
    logger.info("agent_review_job called for request_id=%s (stub)", request_id)


async def url_expiry_warning_job(ctx: dict[str, Any]) -> None:
    """Cron — check for ReleaseRecords with pre-signed URLs expiring soon.

    Iteration 6 will implement URL expiry notifications here.
    """
    logger.info("url_expiry_warning_job ran (stub)")


# ---------------------------------------------------------------------------
# Startup / shutdown hooks
# ---------------------------------------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:  # noqa: RUF029
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
