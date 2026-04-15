"""Tests for GET /users/me."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_me_dev_bypass(client: AsyncClient):
    """DEV_AUTH_BYPASS with no token returns dev user."""
    response = await client.get("/users/me")
    assert response.status_code == 200
    data = response.json()
    assert data["keycloak_sub"] == "dev-bypass-user"
    assert data["email"] == "dev@localhost"
    assert data["is_admin"] is False
    assert "researcher" in data["realm_roles"]


@pytest.mark.asyncio
async def test_get_me_admin_bypass(admin_client: AsyncClient):
    """DEV_AUTH_BYPASS with admin token returns admin user."""
    response = await admin_client.get("/users/me")
    assert response.status_code == 200
    data = response.json()
    assert data["keycloak_sub"] == "dev-bypass-admin"
    assert data["is_admin"] is True
    assert "tre_admin" in data["realm_roles"]


@pytest.mark.asyncio
async def test_get_me_upserts_user(client: AsyncClient):
    """Calling /users/me twice with same identity returns same user ID."""
    r1 = await client.get("/users/me")
    r2 = await client.get("/users/me")
    assert r1.json()["id"] == r2.json()["id"]
