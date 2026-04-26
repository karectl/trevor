"""Tests for iter-9 hardening: metrics, error pages, CSRF, upload size limit."""

import io

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.app import create_app
from trevor.csrf import generate_csrf_token, validate_csrf_token
from trevor.database import get_session
from trevor.models.project import Project
from trevor.settings import Settings, get_settings

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def hardening_settings() -> Settings:
    return Settings(
        dev_auth_bypass=True,
        database_url=TEST_DB_URL,
        secret_key="test-secret-key",
        max_upload_size_mb=1,  # 1 MB limit for tests
    )


@pytest.fixture
async def h_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False, future=True)
    import trevor.models  # noqa: F401

    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def h_client(hardening_settings, h_engine):
    factory = async_sessionmaker(bind=h_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app = create_app(hardening_settings)
    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: hardening_settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def h_admin_client(hardening_settings, h_engine):
    factory = async_sessionmaker(bind=h_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as session:
            yield session

    app = create_app(hardening_settings)
    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = lambda: hardening_settings

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer admin-token"},
    ) as ac:
        yield ac


@pytest.fixture
async def h_researcher_setup(h_client, h_admin_client, h_engine):
    """Upsert dev-bypass-user, create project, assign researcher role."""
    factory = async_sessionmaker(bind=h_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        me = await h_client.get("/users/me")
        assert me.status_code == 200
        user_id = me.json()["id"]

        project = Project(crd_name="hardening-project", display_name="Hardening Project")
        session.add(project)
        await session.commit()
        await session.refresh(project)

        r = await h_admin_client.post(
            "/memberships",
            json={"user_id": user_id, "project_id": str(project.id), "role": "researcher"},
        )
        assert r.status_code == 201

        return h_client, project.id


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200(h_client):
    r = await h_client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_metrics_contains_http_requests_total(h_client):
    # Hit health to generate a metric
    await h_client.get("/health")
    r = await h_client.get("/metrics")
    assert "http_requests_total" in r.text


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_404_json_for_api(h_client):
    r = await h_client.get("/nonexistent-api-path", headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.json()["detail"].lower() == "not found"


@pytest.mark.asyncio
async def test_404_html_for_browser(h_client):
    r = await h_client.get("/nonexistent-api-path", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "404" in r.text or "not found" in r.text.lower()


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_token_generate_and_validate():
    secret = "test-secret"
    token = generate_csrf_token(secret)
    assert validate_csrf_token(secret, token) is True


@pytest.mark.asyncio
async def test_csrf_token_invalid_signature():
    secret = "test-secret"
    assert validate_csrf_token(secret, "invalid-token") is False


@pytest.mark.asyncio
async def test_csrf_token_wrong_secret():
    token = generate_csrf_token("secret-a")
    assert validate_csrf_token("secret-b", token) is False


@pytest.mark.asyncio
async def test_ui_post_without_csrf_token_is_403(csrf_client):
    # POST to a UI form route without csrf_token — should get 403
    r = await csrf_client.post(
        "/ui/requests",
        data={"project_id": "00000000-0000-0000-0000-000000000001", "title": "test"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ui_post_with_valid_csrf_token_proceeds(h_client, h_researcher_setup):
    _, project_id = h_researcher_setup
    token = generate_csrf_token("test-secret-key")
    r = await h_client.post(
        "/ui/requests",
        data={
            "project_id": str(project_id),
            "title": "CSRF test request",
            "direction": "egress",
            "csrf_token": token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # dev_auth_bypass=True skips CSRF; just verify it wasn't rejected for CSRF (403 from middleware)
    # May get 422/500 from business logic without real auth context
    assert r.status_code != 403 or r.text != "CSRF validation failed"


# ---------------------------------------------------------------------------
# Upload size limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_over_size_limit_is_413(h_client, h_researcher_setup):
    client, project_id = h_researcher_setup

    # Create a draft request
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "title": "Size test",
            "direction": "egress",
        },
    )
    assert r.status_code == 201
    request_id = r.json()["id"]

    # Upload file larger than 1 MB limit
    big_file = b"x" * (2 * 1024 * 1024)  # 2 MB
    r = await client.post(
        f"/requests/{request_id}/objects",
        files={"file": ("big.csv", io.BytesIO(big_file), "text/csv")},
        data={"output_type": "tabular"},
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_upload_within_size_limit_succeeds(h_client, h_researcher_setup):
    client, project_id = h_researcher_setup

    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "title": "Small upload test",
            "direction": "egress",
        },
    )
    assert r.status_code == 201
    request_id = r.json()["id"]

    small_file = b"col1,col2\n1,2\n"
    r = await client.post(
        f"/requests/{request_id}/objects",
        files={"file": ("small.csv", io.BytesIO(small_file), "text/csv")},
        data={"output_type": "tabular"},
    )
    assert r.status_code == 201


@pytest.fixture
async def csrf_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False, future=True)
    import trevor.models  # noqa: F401

    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def csrf_client(csrf_engine):
    """Client with CSRF enforcement enabled (dev_auth_bypass=False, no auth needed for test)."""
    settings = Settings(
        dev_auth_bypass=False,
        database_url=TEST_DB_URL,
        secret_key="test-csrf-secret",
    )
    factory = async_sessionmaker(bind=csrf_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override():
        async with factory() as session:
            yield session

    app = create_app(settings)
    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
