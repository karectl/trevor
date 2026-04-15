"""Tests for /memberships endpoints + role conflict validation."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_membership_requires_admin(client: AsyncClient, sample_user, sample_project):
    """Non-admin gets 403."""
    response = await client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "researcher",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_membership_admin(admin_client: AsyncClient, sample_user, sample_project):
    """Admin can create membership."""
    response = await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "researcher",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "researcher"
    assert data["user_id"] == str(sample_user.id)


@pytest.mark.asyncio
async def test_list_project_memberships(
    admin_client: AsyncClient, client: AsyncClient, sample_user, sample_project
):
    # Create membership as admin.
    await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "researcher",
        },
    )
    # List as normal user.
    response = await client.get(f"/memberships/project/{sample_project.id}")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_role_conflict_researcher_then_checker(
    admin_client: AsyncClient, sample_user, sample_project
):
    """Cannot add checker if user is already researcher on same project."""
    # First: assign researcher.
    r = await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "researcher",
        },
    )
    assert r.status_code == 201

    # Second: try to assign output_checker — should fail 409.
    r2 = await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "output_checker",
        },
    )
    assert r2.status_code == 409
    assert "researcher" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_role_conflict_checker_then_researcher(
    admin_client: AsyncClient, sample_user, sample_project
):
    """Cannot add researcher if user is already checker on same project."""
    r = await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "output_checker",
        },
    )
    assert r.status_code == 201

    r2 = await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "researcher",
        },
    )
    assert r2.status_code == 409
    assert "checker" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_membership_rejected(
    admin_client: AsyncClient, sample_user, sample_project
):
    """Same user+project+role twice = 409."""
    payload = {
        "user_id": str(sample_user.id),
        "project_id": str(sample_project.id),
        "role": "researcher",
    }
    r1 = await admin_client.post("/memberships", json=payload)
    assert r1.status_code == 201
    r2 = await admin_client.post("/memberships", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_delete_membership(admin_client: AsyncClient, sample_user, sample_project):
    """Admin can delete membership."""
    r = await admin_client.post(
        "/memberships",
        json={
            "user_id": str(sample_user.id),
            "project_id": str(sample_project.id),
            "role": "researcher",
        },
    )
    membership_id = r.json()["id"]
    r2 = await admin_client.delete(f"/memberships/{membership_id}")
    assert r2.status_code == 204

    # Verify gone.
    r3 = await admin_client.get(f"/memberships/project/{sample_project.id}")
    assert len(r3.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_membership(admin_client: AsyncClient):
    r = await admin_client.delete("/memberships/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
