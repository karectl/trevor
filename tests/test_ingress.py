"""Tests for ingress flow: request creation, pre-signed PUT, confirm-upload, delivery."""

import uuid

import pytest

from trevor.models.project import Project
from trevor.models.request import AirlockRequest, AirlockRequestStatus, OutputObjectState

# --- Helpers ---


async def _admin_setup(admin_client, db_session):
    """Upsert admin user and create a project. Return (admin_user_id, project_id)."""
    me = await admin_client.get("/users/me")
    assert me.status_code == 200
    admin_id = me.json()["id"]

    project = Project(crd_name="ingress-test-project", display_name="Ingress Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return admin_id, project.id


async def _create_ingress_request(admin_client, project_id):
    r = await admin_client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "ingress",
            "title": "Ingress test request",
        },
    )
    return r


# --- Tests ---


@pytest.mark.asyncio
async def test_admin_creates_ingress_request(admin_client, db_session):
    """Admin can create ingress request."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    assert r.status_code == 201
    data = r.json()
    assert data["direction"] == "ingress"
    assert data["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_researcher_cannot_create_ingress_request(client, admin_client, db_session):
    """Researcher cannot create ingress request."""
    researcher_client, project_id = await _get_researcher_setup(client, admin_client, db_session)
    r = await researcher_client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "ingress",
            "title": "Should fail",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_ingress_object_slot_no_file(admin_client, db_session):
    """Can create ingress object slot without a file (placeholder for external upload)."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["filename"] == "data.csv"
    assert data["checksum_sha256"] == ""
    assert data["size_bytes"] == 0


@pytest.mark.asyncio
async def test_generate_upload_url(admin_client, db_session):
    """Generate pre-signed PUT URL for ingress object."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    object_id = r.json()["id"]

    r = await admin_client.post(f"/requests/{request_id}/objects/{object_id}/upload-url")
    assert r.status_code == 200
    data = r.json()
    assert "upload_url" in data
    assert data["expires_in"] == 3600
    assert "storage_key" in data


@pytest.mark.asyncio
async def test_generate_upload_url_egress_fails(client, admin_client, db_session):
    """Cannot generate upload URL for egress request."""
    researcher_client, project_id = await _get_researcher_setup(client, admin_client, db_session)
    r = await researcher_client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "egress req"},
    )
    request_id = r.json()["id"]

    r = await researcher_client.post(
        f"/requests/{request_id}/objects",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        data={"output_type": "tabular"},
    )
    object_id = r.json()["id"]

    r = await admin_client.post(f"/requests/{request_id}/objects/{object_id}/upload-url")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_confirm_upload(admin_client, db_session):
    """Confirm upload sets checksum and size on ingress object."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    object_id = r.json()["id"]

    # Generate URL first
    await admin_client.post(f"/requests/{request_id}/objects/{object_id}/upload-url")

    # Confirm upload (dev mode: sets dummy values)
    r = await admin_client.post(f"/requests/{request_id}/objects/{object_id}/confirm-upload")
    assert r.status_code == 200
    data = r.json()
    assert data["checksum_sha256"] != ""
    assert data["size_bytes"] > 0


@pytest.mark.asyncio
async def test_confirm_upload_without_url_fails(admin_client, db_session):
    """Confirm upload fails if upload URL was never generated."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    object_id = r.json()["id"]

    r = await admin_client.post(f"/requests/{request_id}/objects/{object_id}/confirm-upload")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_ingress_submit_triggers_review_pipeline(admin_client, db_session):
    """Submitting ingress request transitions to SUBMITTED (dev mode skips ARQ)."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    # Add object slot
    await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )

    r = await admin_client.post(f"/requests/{request_id}/submit")
    assert r.status_code == 200
    assert r.json()["status"] == "SUBMITTED"


@pytest.mark.asyncio
async def test_deliver_approved_ingress_request(admin_client, db_session):
    """Admin can deliver approved ingress request, gets object URLs."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    object_id = r.json()["id"]

    # Force request and object to APPROVED state via DB
    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.APPROVED
    db_session.add(req)

    from trevor.models.request import OutputObject

    obj = await db_session.get(OutputObject, uuid.UUID(object_id))
    obj.state = OutputObjectState.APPROVED
    obj.checksum_sha256 = "abc123"
    obj.size_bytes = 512
    db_session.add(obj)
    await db_session.commit()

    r = await admin_client.post(f"/requests/{request_id}/deliver")
    assert r.status_code == 202
    data = r.json()
    assert "object_urls" in data
    assert len(data["object_urls"]) == 1
    assert "download_url" in data["object_urls"][0]

    # Request should now be RELEASED
    await db_session.refresh(req)
    assert req.status == AirlockRequestStatus.RELEASED


@pytest.mark.asyncio
async def test_deliver_non_approved_fails(admin_client, db_session):
    """Deliver fails if request not in APPROVED state."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(f"/requests/{request_id}/deliver")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_deliver_egress_request_fails(client, admin_client, db_session):
    """Deliver endpoint rejects egress requests."""
    researcher_client, project_id = await _get_researcher_setup(client, admin_client, db_session)
    r = await researcher_client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "egress"},
    )
    request_id = r.json()["id"]

    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.APPROVED
    db_session.add(req)
    await db_session.commit()

    r = await admin_client.post(f"/requests/{request_id}/deliver")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_get_delivery_record(admin_client, db_session):
    """GET /requests/{id}/delivery returns delivery record after deliver."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    object_id = r.json()["id"]

    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.APPROVED
    db_session.add(req)

    from trevor.models.request import OutputObject

    obj = await db_session.get(OutputObject, uuid.UUID(object_id))
    obj.state = OutputObjectState.APPROVED
    obj.checksum_sha256 = "abc123"
    obj.size_bytes = 512
    db_session.add(obj)
    await db_session.commit()

    await admin_client.post(f"/requests/{request_id}/deliver")

    r = await admin_client.get(f"/requests/{request_id}/delivery")
    assert r.status_code == 200
    assert r.json()["request_id"] == request_id


@pytest.mark.asyncio
async def test_double_deliver_fails(admin_client, db_session):
    """Second deliver call returns 409 (already delivered)."""
    _, project_id = await _admin_setup(admin_client, db_session)
    r = await _create_ingress_request(admin_client, project_id)
    request_id = r.json()["id"]

    r = await admin_client.post(
        f"/requests/{request_id}/objects",
        data={"output_type": "tabular", "filename": "data.csv"},
    )
    object_id = r.json()["id"]

    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.APPROVED
    db_session.add(req)

    from trevor.models.request import OutputObject

    obj = await db_session.get(OutputObject, uuid.UUID(object_id))
    obj.state = OutputObjectState.APPROVED
    obj.checksum_sha256 = "abc123"
    obj.size_bytes = 512
    db_session.add(obj)
    await db_session.commit()

    r1 = await admin_client.post(f"/requests/{request_id}/deliver")
    assert r1.status_code == 202

    # Force back to APPROVED so second attempt passes direction/status checks
    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.APPROVED
    db_session.add(req)
    await db_session.commit()

    r2 = await admin_client.post(f"/requests/{request_id}/deliver")
    assert r2.status_code == 409


# --- Helper ---


async def _get_researcher_setup(client, admin_client, db_session):
    me = await client.get("/users/me")
    user_id = me.json()["id"]
    project = Project(crd_name="ingress-res-project", display_name="Ingress Res Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    r = await admin_client.post(
        "/memberships",
        json={"user_id": user_id, "project_id": str(project.id), "role": "researcher"},
    )
    assert r.status_code == 201
    return client, project.id
