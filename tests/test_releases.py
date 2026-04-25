"""Tests for release endpoints and RO-Crate assembly."""

import json
import uuid
import zipfile
from io import BytesIO

import pytest

from trevor.models.request import (
    AirlockRequest,
    AirlockRequestStatus,
    OutputObject,
    OutputObjectMetadata,
    OutputObjectState,
)
from trevor.models.review import Review, ReviewDecision, ReviewerType
from trevor.services.release_service import _build_ro_crate_metadata, build_crate_zip

# --- Helpers ---


async def _create_approved_request(client, admin_client, project_id, db_session):
    """Create request, upload, submit, move to APPROVED with agent + human reviews."""
    # Create request
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Release test request",
        },
    )
    assert r.status_code == 201
    request_id = r.json()["id"]

    # Upload object
    r = await client.post(
        f"/requests/{request_id}/objects",
        files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": "freq_table"},
    )
    assert r.status_code == 201
    object_id = r.json()["id"]

    # Submit
    r = await client.post(f"/requests/{request_id}/submit")
    assert r.status_code == 200

    # Move to APPROVED via DB (agent review + human review + state transitions)
    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.APPROVED
    db_session.add(req)

    # Set object state to APPROVED
    obj = await db_session.get(OutputObject, uuid.UUID(object_id))
    obj.state = OutputObjectState.APPROVED
    db_session.add(obj)

    # Add agent review
    agent_review = Review(
        request_id=uuid.UUID(request_id),
        reviewer_id=None,
        reviewer_type=ReviewerType.AGENT,
        decision=ReviewDecision.APPROVED,
        summary="Agent: all checks passed",
        findings=[{"object_id": object_id, "statbarn_confirmed": True}],
    )
    db_session.add(agent_review)

    # Add human review
    me = await admin_client.get("/users/me")
    admin_id = me.json()["id"]
    human_review = Review(
        request_id=uuid.UUID(request_id),
        reviewer_id=uuid.UUID(admin_id),
        reviewer_type=ReviewerType.HUMAN,
        decision=ReviewDecision.APPROVED,
        summary="Human: approved",
        findings=[{"object_id": object_id, "decision": "approved"}],
    )
    db_session.add(human_review)
    await db_session.commit()

    return request_id


# --- Unit tests ---


def test_build_ro_crate_metadata():
    """RO-Crate metadata has correct structure."""
    from trevor.models.user import User

    req = AirlockRequest(
        project_id=uuid.uuid4(),
        submitted_by=uuid.uuid4(),
        title="Test Request",
        description="Test description",
    )
    obj = OutputObject(
        request_id=req.id,
        logical_object_id=uuid.uuid4(),
        filename="data.csv",
        size_bytes=100,
        checksum_sha256="abc123",
        storage_key="some/key",
        output_type="tabular",
        statbarn="freq_table",
    )
    meta = OutputObjectMetadata(
        logical_object_id=obj.logical_object_id,
        description="A table",
        researcher_justification="Needed for paper",
    )
    user = User(
        keycloak_sub="sub-1",
        username="testuser",
        email="test@example.com",
        given_name="Test",
        family_name="User",
        affiliation="Test Org",
    )
    review = Review(
        request_id=req.id,
        reviewer_type=ReviewerType.AGENT,
        decision=ReviewDecision.APPROVED,
        summary="All good",
        findings=[],
    )

    metadata = _build_ro_crate_metadata(req, [(obj, meta)], [review], user)

    assert "@context" in metadata
    assert "@graph" in metadata
    graph = metadata["@graph"]
    # CreativeWork (metadata descriptor) + Dataset (root) + File + Person + Review
    assert len(graph) == 5
    root = graph[1]
    assert root["@type"] == "Dataset"
    assert root["name"] == "Test Request"
    assert len(root["hasPart"]) == 1
    file_entity = graph[2]
    assert file_entity["@type"] == "File"
    assert file_entity["tre:statbarn"] == "freq_table"


def test_build_crate_zip():
    """Zip contains ro-crate-metadata.json and data files."""
    metadata = {"@context": [], "@graph": []}
    files = [("data.csv", b"a,b\n1,2\n")]
    zip_bytes = build_crate_zip(metadata, files)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "ro-crate-metadata.json" in names
        assert "data/data.csv" in names
        content = json.loads(zf.read("ro-crate-metadata.json"))
        assert content == metadata
        assert zf.read("data/data.csv") == b"a,b\n1,2\n"


# --- Integration tests ---


@pytest.mark.asyncio
async def test_trigger_release_happy_path(researcher_setup, admin_client, db_session):
    """POST /requests/{id}/release transitions APPROVED → RELEASED (dev inline)."""
    client, project_id = researcher_setup
    request_id = await _create_approved_request(client, admin_client, project_id, db_session)

    r = await admin_client.post(f"/requests/{request_id}/release")
    assert r.status_code == 202
    assert r.json()["status"] == "releasing"

    # Request should now be RELEASED
    r = await client.get(f"/requests/{request_id}")
    assert r.json()["status"] == "RELEASED"


@pytest.mark.asyncio
async def test_trigger_release_wrong_state(researcher_setup, admin_client, db_session):
    """POST /release on non-APPROVED request → 409."""
    client, project_id = researcher_setup
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Not approved",
        },
    )
    request_id = r.json()["id"]

    r = await admin_client.post(f"/requests/{request_id}/release")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_trigger_release_duplicate(researcher_setup, admin_client, db_session):
    """POST /release twice → 409 on second attempt."""
    client, project_id = researcher_setup
    request_id = await _create_approved_request(client, admin_client, project_id, db_session)

    r = await admin_client.post(f"/requests/{request_id}/release")
    assert r.status_code == 202

    # Second attempt — request is RELEASED now, not APPROVED
    r = await admin_client.post(f"/requests/{request_id}/release")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_get_release_happy_path(researcher_setup, admin_client, db_session):
    """GET /requests/{id}/release returns the release record."""
    client, project_id = researcher_setup
    request_id = await _create_approved_request(client, admin_client, project_id, db_session)

    await admin_client.post(f"/requests/{request_id}/release")

    r = await client.get(f"/requests/{request_id}/release")
    assert r.status_code == 200
    data = r.json()
    assert data["request_id"] == request_id
    assert "crate_checksum_sha256" in data
    assert "presigned_url" in data


@pytest.mark.asyncio
async def test_get_release_not_found(researcher_setup, admin_client, db_session):
    """GET /release when not released → 404."""
    client, project_id = researcher_setup
    request_id = await _create_approved_request(client, admin_client, project_id, db_session)

    r = await client.get(f"/requests/{request_id}/release")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_release_creates_audit_events(researcher_setup, admin_client, db_session):
    """Release creates audit events for releasing and released."""
    client, project_id = researcher_setup
    request_id = await _create_approved_request(client, admin_client, project_id, db_session)

    await admin_client.post(f"/requests/{request_id}/release")

    r = await client.get(f"/requests/{request_id}/audit")
    assert r.status_code == 200
    events = r.json()
    event_types = [e["event_type"] for e in events]
    assert "request.releasing" in event_types
    assert "request.released" in event_types


@pytest.mark.asyncio
async def test_release_non_admin_forbidden(researcher_setup, admin_client, db_session):
    """Non-admin cannot trigger release."""
    client, project_id = researcher_setup
    request_id = await _create_approved_request(client, admin_client, project_id, db_session)

    # Regular client (researcher) tries to release
    r = await client.post(f"/requests/{request_id}/release")
    assert r.status_code == 403
