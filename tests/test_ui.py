"""UI router tests — verify HTML rendering and redirects."""

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_ui_root_redirects(client: AsyncClient) -> None:
    r = await client.get("/ui/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/requests"


@pytest.mark.anyio
async def test_request_list_html(client: AsyncClient) -> None:
    r = await client.get("/ui/requests")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "My Requests" in r.text


@pytest.mark.anyio
async def test_request_create_form(client: AsyncClient) -> None:
    r = await client.get("/ui/requests/new")
    assert r.status_code == 200
    assert "Create Request" in r.text


@pytest.mark.anyio
async def test_request_create_and_detail(researcher_setup) -> None:
    client, project_id = researcher_setup
    # Create via form POST
    r = await client.post(
        "/ui/requests",
        data={
            "project_id": str(project_id),
            "title": "UI test request",
            "direction": "egress",
            "description": "test",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    detail_url = r.headers["location"]
    assert "/ui/requests/" in detail_url

    # Follow redirect
    r2 = await client.get(detail_url)
    assert r2.status_code == 200
    assert "UI test request" in r2.text


@pytest.mark.anyio
async def test_upload_form(researcher_setup) -> None:
    client, project_id = researcher_setup
    # Create request first
    r = await client.post(
        "/ui/requests",
        data={
            "project_id": str(project_id),
            "title": "Upload test",
            "direction": "egress",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    # Extract request ID from URL
    req_id = r.url.path.split("/")[-1]

    # Upload form
    r2 = await client.get(f"/ui/requests/{req_id}/upload")
    assert r2.status_code == 200
    assert "Upload Object" in r2.text


@pytest.mark.anyio
async def test_upload_object_via_ui(researcher_setup) -> None:
    client, project_id = researcher_setup
    # Create request
    r = await client.post(
        "/ui/requests",
        data={
            "project_id": str(project_id),
            "title": "Upload obj test",
            "direction": "egress",
        },
        follow_redirects=True,
    )
    req_id = r.url.path.split("/")[-1]

    # Upload file
    r2 = await client.post(
        f"/ui/requests/{req_id}/upload",
        files={"file": ("test.csv", b"a,b\n1,2\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": ""},
        follow_redirects=False,
    )
    assert r2.status_code == 303

    # Detail page should show the object
    r3 = await client.get(f"/ui/requests/{req_id}")
    assert "test.csv" in r3.text


@pytest.mark.anyio
async def test_review_queue_html(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/ui/review")
    assert r.status_code == 200
    assert "Review Queue" in r.text


@pytest.mark.anyio
async def test_admin_overview_html(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/ui/admin")
    assert r.status_code == 200
    assert "All Requests" in r.text


@pytest.mark.anyio
async def test_admin_metrics_html(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/ui/admin/metrics")
    assert r.status_code == 200
    assert "Pipeline Metrics" in r.text


@pytest.mark.anyio
async def test_admin_audit_html(admin_client: AsyncClient) -> None:
    r = await admin_client.get("/ui/admin/audit")
    assert r.status_code == 200
    assert "Audit Log" in r.text


@pytest.mark.anyio
async def test_admin_requires_auth(client: AsyncClient) -> None:
    """Non-admin client should get 403 on admin pages."""
    r = await client.get("/ui/admin")
    assert r.status_code == 403
