"""Tests for admin dashboard and metrics endpoints."""

import uuid

import pytest

from trevor.models.request import (
    AirlockRequest,
    AirlockRequestStatus,
    AuditEvent,
)

# --- Helpers ---


async def _seed_request(db_session, project_id, user_id, *, status="DRAFT", title="Test"):
    """Create a request directly in DB."""
    req = AirlockRequest(
        project_id=project_id,
        direction="egress",
        status=AirlockRequestStatus(status),
        title=title,
        submitted_by=user_id,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)
    return req


async def _seed_audit(db_session, request_id, event_type, actor_id="system"):
    event = AuditEvent(
        request_id=request_id,
        event_type=event_type,
        actor_id=actor_id,
    )
    db_session.add(event)
    await db_session.commit()
    return event


# --- GET /admin/requests ---


@pytest.mark.asyncio
async def test_admin_requests_empty(admin_client):
    r = await admin_client.get("/admin/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_admin_requests_with_data(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup

    # Create a request via API
    r = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Admin test"},
    )
    assert r.status_code == 201

    r = await admin_client.get("/admin/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Admin test"
    assert "age_hours" in data["items"][0]


@pytest.mark.asyncio
async def test_admin_requests_filter_by_status(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup

    # Create two requests
    await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Draft one"},
    )
    r2 = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Draft two"},
    )
    # Move second to SUBMITTED via DB
    req = await db_session.get(AirlockRequest, uuid.UUID(r2.json()["id"]))
    req.status = AirlockRequestStatus.SUBMITTED
    db_session.add(req)
    await db_session.commit()

    # Filter DRAFT only
    r = await admin_client.get("/admin/requests?status=DRAFT")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_admin_requests_pagination(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup

    for i in range(3):
        await client.post(
            "/requests",
            json={"project_id": str(project_id), "direction": "egress", "title": f"Req {i}"},
        )

    r = await admin_client.get("/admin/requests?limit=2&offset=0")
    assert r.json()["total"] == 3
    assert len(r.json()["items"]) == 2

    r = await admin_client.get("/admin/requests?limit=2&offset=2")
    assert len(r.json()["items"]) == 1


@pytest.mark.asyncio
async def test_admin_requests_non_admin_forbidden(client):
    r = await client.get("/admin/requests")
    assert r.status_code == 403


# --- GET /admin/metrics ---


@pytest.mark.asyncio
async def test_admin_metrics_empty(admin_client):
    r = await admin_client.get("/admin/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["total_requests"] == 0
    assert data["by_status"] == {}
    assert data["stuck_requests"] == []


@pytest.mark.asyncio
async def test_admin_metrics_with_data(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup

    # Create requests in various states
    me = await client.get("/users/me")
    user_id = uuid.UUID(me.json()["id"])

    await _seed_request(db_session, project_id, user_id, status="DRAFT")
    await _seed_request(db_session, project_id, user_id, status="APPROVED")
    await _seed_request(db_session, project_id, user_id, status="REJECTED")

    r = await admin_client.get("/admin/metrics")
    assert r.status_code == 200
    data = r.json()
    assert data["total_requests"] == 3
    assert data["by_status"]["DRAFT"] == 1
    assert data["by_status"]["APPROVED"] == 1
    assert data["approval_rate"] == 0.5  # 1 approved / 2 decided


@pytest.mark.asyncio
async def test_admin_metrics_stuck_detection(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup
    me = await client.get("/users/me")
    user_id = uuid.UUID(me.json()["id"])

    from datetime import UTC, datetime, timedelta

    req = await _seed_request(db_session, project_id, user_id, status="HUMAN_REVIEW")
    # Backdate updated_at to trigger stuck detection
    req.updated_at = datetime.now(UTC) - timedelta(hours=100)
    db_session.add(req)
    await db_session.commit()

    r = await admin_client.get("/admin/metrics")
    data = r.json()
    assert len(data["stuck_requests"]) == 1
    assert data["stuck_requests"][0]["request_id"] == str(req.id)
    assert data["stuck_requests"][0]["waiting_hours"] > 90


@pytest.mark.asyncio
async def test_admin_metrics_non_admin_forbidden(client):
    r = await client.get("/admin/metrics")
    assert r.status_code == 403


# --- GET /admin/audit ---


@pytest.mark.asyncio
async def test_admin_audit_empty(admin_client):
    r = await admin_client.get("/admin/audit")
    assert r.status_code == 200
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_admin_audit_with_events(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup

    # Create request + submit (generates audit events)
    r = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Audit test"},
    )
    request_id = r.json()["id"]

    # Upload object so submit works
    await client.post(
        f"/requests/{request_id}/objects",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": "freq_table"},
    )
    await client.post(f"/requests/{request_id}/submit")

    r = await admin_client.get("/admin/audit")
    assert r.status_code == 200
    assert r.json()["total"] > 0


@pytest.mark.asyncio
async def test_admin_audit_filter_event_type(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup
    me = await client.get("/users/me")
    user_id = uuid.UUID(me.json()["id"])

    req = await _seed_request(db_session, project_id, user_id)
    await _seed_audit(db_session, req.id, "request.submitted")
    await _seed_audit(db_session, req.id, "request.approved")

    r = await admin_client.get("/admin/audit?event_type=request.submitted")
    assert r.status_code == 200
    events = r.json()["items"]
    assert all(e["event_type"] == "request.submitted" for e in events)


@pytest.mark.asyncio
async def test_admin_audit_non_admin_forbidden(client):
    r = await client.get("/admin/audit")
    assert r.status_code == 403


# --- GET /admin/audit/export ---


@pytest.mark.asyncio
async def test_admin_audit_export_csv(researcher_setup, admin_client, db_session):
    client, project_id = researcher_setup
    me = await client.get("/users/me")
    user_id = uuid.UUID(me.json()["id"])

    req = await _seed_request(db_session, project_id, user_id)
    await _seed_audit(db_session, req.id, "request.created")

    r = await admin_client.get("/admin/audit/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().split("\n")
    assert lines[0].strip() == "id,timestamp,event_type,actor_id,request_id,payload"
    assert len(lines) >= 2


@pytest.mark.asyncio
async def test_admin_audit_export_non_admin_forbidden(client):
    r = await client.get("/admin/audit/export")
    assert r.status_code == 403
