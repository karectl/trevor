"""SSE utilities for Datastar fragment streaming."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2  # seconds
CONNECTION_TIMEOUT = 300  # 5 minutes


def format_fragment_event(html: str) -> str:
    """Format an HTML fragment as a Datastar merge-fragments SSE event.

    The root element of *html* must carry an ``id`` attribute — Datastar uses
    it to locate and morph-merge the element in the DOM.

    Returns an SSE-formatted string with trailing double newline.
    """
    lines = html.strip().splitlines()
    parts = ["event: datastar-merge-fragments\n"]
    parts.append(f"data: fragments {lines[0]}\n")
    for line in lines[1:]:
        parts.append(f"data: {line}\n")
    parts.append("\n")
    return "".join(parts)


async def sse_stream(
    request: Request,
    poll_fn: Callable[[], Awaitable[str]],
    *,
    poll_interval: float = POLL_INTERVAL,
    timeout: float = CONNECTION_TIMEOUT,
    send_unchanged: bool = False,
) -> AsyncGenerator[str]:
    """Generic SSE stream that polls *poll_fn* and yields fragment events.

    - First event is always sent immediately (``last_html`` starts as ``None``).
    - Subsequent events are only sent when the HTML differs from the last
      sent value (unless ``send_unchanged=True``).
    - Disconnects are detected each poll cycle via ``request.is_disconnected()``.
    - Errors inside *poll_fn* are logged and skipped; the stream continues.
    """
    elapsed = 0.0
    last_html: str | None = None

    while elapsed < timeout:
        if await request.is_disconnected():
            break

        try:
            html = await poll_fn()
        except Exception:
            logger.exception("sse_stream: poll_fn raised")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            continue

        if last_html is None or html != last_html or send_unchanged:
            yield format_fragment_event(html)
            last_html = html

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval


def sse_response(stream: AsyncGenerator[str]) -> StreamingResponse:
    """Wrap an SSE async generator in a StreamingResponse with correct headers."""
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
