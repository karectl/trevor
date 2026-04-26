"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from trevor.csrf import validate_csrf_token
from trevor.database import create_db_and_tables, get_engine
from trevor.limiter import limiter
from trevor.logging_config import configure_logging
from trevor.routers import (
    admin,
    auth_routes,
    deliveries,
    memberships,
    notifications,
    projects,
    releases,
    requests,
    reviews,
    sse,
    ui,
    users,
)
from trevor.settings import Settings, get_settings
from trevor.telemetry import configure_telemetry

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_DIR = Path(__file__).parent / "templates"


class CSRFMiddleware(BaseHTTPMiddleware):
    """Validate CSRF token on state-mutating UI form POSTs."""

    def __init__(self, app: FastAPI, secret_key: str, dev_bypass: bool = False) -> None:
        super().__init__(app)
        self.secret_key = secret_key
        self.dev_bypass = dev_bypass

    async def dispatch(self, request: Request, call_next: object) -> Response:
        if (
            not self.dev_bypass
            and request.method in ("POST", "PUT", "DELETE", "PATCH")
            and request.url.path.startswith("/ui/")
        ):
            content_type = request.headers.get("content-type", "")
            if (
                "application/x-www-form-urlencoded" in content_type
                or "multipart/form-data" in content_type
            ):
                form = await request.form()
                token = form.get("csrf_token", "")
                if not validate_csrf_token(self.secret_key, str(token)):
                    return Response("CSRF validation failed", status_code=403)
        return await call_next(request)


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup / shutdown lifecycle."""
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, settings.log_format)
    configure_telemetry(settings)
    engine = get_engine(settings.database_url)
    # In dev/test with SQLite, create tables on startup (Alembic in prod).
    if "sqlite" in settings.database_url:
        # Import models so SQLModel.metadata knows all tables.
        import trevor.models  # noqa: F401

        await create_db_and_tables(engine)

    # ARQ pool for enqueueing background jobs (notifications, etc.)
    if settings.notifications_enabled:
        try:
            from arq import create_pool
            from arq.connections import RedisSettings as ArqRedisSettings

            app.state.arq_pool = await create_pool(ArqRedisSettings.from_dsn(settings.redis_url))
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Failed to connect to Redis; notification enqueue disabled"
            )
            app.state.arq_pool = None
    else:
        app.state.arq_pool = None

    yield

    if getattr(app.state, "arq_pool", None) is not None:
        await app.state.arq_pool.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(
        CSRFMiddleware, secret_key=settings.secret_key, dev_bypass=settings.dev_auth_bypass
    )

    # Prometheus instrumentation
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    # Error handlers — HTML for browser requests, JSON for API
    @app.exception_handler(401)
    async def unauthorized_handler(request: Request, exc: Exception) -> Response:
        if _wants_html(request):
            login_url = f"/auth/login?next={request.url.path}"
            return RedirectResponse(login_url, status_code=302)
        detail = getattr(exc, "detail", "Unauthorized")
        return JSONResponse({"detail": detail}, status_code=401)

    @app.exception_handler(403)
    async def forbidden_handler(request: Request, exc: Exception) -> HTMLResponse | JSONResponse:
        if _wants_html(request):
            return templates.TemplateResponse(
                "errors/403.html", {"request": request}, status_code=403
            )
        detail = getattr(exc, "detail", "Forbidden")
        return JSONResponse({"detail": detail}, status_code=403)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception) -> HTMLResponse | JSONResponse:
        if _wants_html(request):
            return templates.TemplateResponse(
                "errors/404.html", {"request": request}, status_code=404
            )
        detail = getattr(exc, "detail", "Not found")
        return JSONResponse({"detail": detail}, status_code=404)

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc: Exception) -> HTMLResponse | JSONResponse:
        if _wants_html(request):
            return templates.TemplateResponse(
                "errors/500.html", {"request": request}, status_code=500
            )
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    app.include_router(auth_routes.router)
    app.include_router(users.router)
    app.include_router(projects.router)
    app.include_router(memberships.router)
    app.include_router(requests.router)
    app.include_router(reviews.router)
    app.include_router(releases.router)
    app.include_router(deliveries.router)
    app.include_router(notifications.router)
    app.include_router(admin.router)
    app.include_router(sse.router)
    app.include_router(ui.router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


app = create_app()
