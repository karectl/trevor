"""Database engine, session factory, and base model helpers.

Engine and session factory are created per-settings to allow test isolation.
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

__all__ = ["AsyncSession", "SQLModel", "get_engine", "get_session", "get_session_factory"]


@lru_cache(maxsize=4)
def get_engine(database_url: str) -> AsyncEngine:
    """Return (or create) an async engine for the given URL. Cached by URL."""
    return create_async_engine(database_url, echo=False, future=True)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency — yields an async DB session.

    Overridden in tests via app.dependency_overrides.
    """
    # Import here to break circular dep with settings → database at module level.
    from trevor.settings import get_settings

    settings = get_settings()
    engine = get_engine(settings.database_url)
    factory = get_session_factory(engine)
    async with factory() as session:
        yield session


async def create_db_and_tables(engine: AsyncEngine) -> None:
    """Create all tables — for local dev/tests only. Use Alembic in production."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
