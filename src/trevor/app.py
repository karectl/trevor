"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from trevor.database import create_db_and_tables, get_engine
from trevor.routers import memberships, projects, requests, users
from trevor.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup / shutdown lifecycle."""
    settings: Settings = app.state.settings
    engine = get_engine(settings.database_url)
    # In dev/test with SQLite, create tables on startup (Alembic in prod).
    if "sqlite" in settings.database_url:
        # Import models so SQLModel.metadata knows all tables.
        import trevor.models  # noqa: F401

        await create_db_and_tables(engine)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    app.include_router(users.router)
    app.include_router(projects.router)
    app.include_router(memberships.router)
    app.include_router(requests.router)

    return app


app = create_app()
