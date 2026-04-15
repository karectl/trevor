"""Tests for /projects endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_projects_empty(client: AsyncClient):
    response = await client.get("/projects")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_projects_with_data(client: AsyncClient, sample_project):
    response = await client.get("/projects")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["crd_name"] == "test-project-1"


@pytest.mark.asyncio
async def test_get_project_by_id(client: AsyncClient, sample_project):
    response = await client.get(f"/projects/{sample_project.id}")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Test Project"


@pytest.mark.asyncio
async def test_get_project_not_found(client: AsyncClient):
    response = await client.get("/projects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
