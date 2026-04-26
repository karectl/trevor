"""Shared pytest fixtures."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.app import create_app
from trevor.database import get_session
from trevor.models.project import Project, ProjectMembership, ProjectRole
from trevor.models.user import User
from trevor.routers.sse import get_sse_session_factory
from trevor.settings import Settings, get_settings

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def dev_settings() -> Settings:
    return Settings(dev_auth_bypass=True, database_url=TEST_DB_URL)


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False, future=True)
    import trevor.models  # noqa: F401

    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def client(dev_settings, engine):
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app = create_app(dev_settings)
    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: dev_settings
    app.dependency_overrides[get_sse_session_factory] = lambda: factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def admin_client(dev_settings, engine):
    """Client that sends 'admin' token — triggers DEV_AUTH_BYPASS admin path."""
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app = create_app(dev_settings)
    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: dev_settings
    app.dependency_overrides[get_sse_session_factory] = lambda: factory

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer admin-token"},
    ) as ac:
        yield ac


@pytest.fixture
async def sample_user(db_session) -> User:
    user = User(
        keycloak_sub="test-sub-1",
        username="testuser1",
        email="test@example.com",
        given_name="Test",
        family_name="User",
        affiliation="Test Org",
        crd_name="testuser1",
        active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def sample_project(db_session) -> Project:
    project = Project(crd_name="test-project-1", display_name="Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


@pytest.fixture
async def sample_membership(db_session, sample_user, sample_project) -> ProjectMembership:
    membership = ProjectMembership(
        user_id=sample_user.id,
        project_id=sample_project.id,
        role=ProjectRole.RESEARCHER,
        assigned_by=sample_user.id,
    )
    db_session.add(membership)
    await db_session.commit()
    await db_session.refresh(membership)
    return membership


@pytest.fixture
async def researcher_setup(client, admin_client, db_session):
    """Upsert dev-bypass user, create project, assign researcher role.

    Returns (client, project_id) where client is authenticated as the researcher.
    """
    # Trigger user upsert for dev-bypass-user
    me = await client.get("/users/me")
    assert me.status_code == 200
    user_id = me.json()["id"]

    # Create project directly in DB
    project = Project(crd_name="req-test-project", display_name="Request Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # Assign researcher membership via admin endpoint
    r = await admin_client.post(
        "/memberships",
        json={
            "user_id": user_id,
            "project_id": str(project.id),
            "role": "researcher",
        },
    )
    assert r.status_code == 201

    return client, project.id
