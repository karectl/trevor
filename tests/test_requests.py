"""Tests for AirlockRequest, OutputObject, and AuditEvent endpoints."""

import io
import uuid


async def test_create_request_non_member(client):
    """Non-member gets 403."""
    r = await client.post(
        "/requests",
        json={
            "project_id": str(uuid.uuid4()),
            "direction": "egress",
            "title": "Test",
        },
    )
    assert r.status_code == 403


async def test_create_request(researcher_setup):
    client, project_id = researcher_setup
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "My Request",
            "description": "Testing",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "DRAFT"
    assert data["title"] == "My Request"
    assert data["direction"] == "egress"
    assert data["submitted_at"] is None


async def test_list_requests(researcher_setup):
    client, project_id = researcher_setup
    await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "R1"},
    )
    r = await client.get("/requests")
    assert r.status_code == 200
    assert len(r.json()) >= 1


async def test_get_request(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "R2"},
    )
    req_id = create.json()["id"]
    r = await client.get(f"/requests/{req_id}")
    assert r.status_code == 200
    assert r.json()["id"] == req_id
    assert "objects" in r.json()


async def test_get_request_not_found(client):
    r = await client.get(f"/requests/{uuid.uuid4()}")
    assert r.status_code in (403, 404)


async def test_upload_object(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Upload test"},
    )
    req_id = create.json()["id"]

    file_content = b"col1,col2\n1,2\n3,4"
    r = await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("data.csv", io.BytesIO(file_content), "text/csv")},
        data={"output_type": "tabular", "statbarn": "T1"},
    )
    assert r.status_code == 201
    obj = r.json()
    assert obj["filename"] == "data.csv"
    assert obj["state"] == "PENDING"
    assert obj["size_bytes"] == len(file_content)
    assert len(obj["checksum_sha256"]) == 64
    assert obj["statbarn"] == "T1"


async def test_upload_blocked_after_submit(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Upload fail"},
    )
    req_id = create.json()["id"]

    await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")},
        data={"output_type": "other"},
    )
    await client.post(f"/requests/{req_id}/submit")

    r = await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("y.txt", io.BytesIO(b"data"), "text/plain")},
        data={"output_type": "other"},
    )
    assert r.status_code == 409


async def test_submit_request(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Submit test"},
    )
    req_id = create.json()["id"]

    await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("f.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")},
        data={"output_type": "tabular"},
    )

    r = await client.post(f"/requests/{req_id}/submit")
    assert r.status_code == 200
    assert r.json()["status"] == "SUBMITTED"
    assert r.json()["submitted_at"] is not None


async def test_submit_requires_objects(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "No objects"},
    )
    req_id = create.json()["id"]

    r = await client.post(f"/requests/{req_id}/submit")
    assert r.status_code == 422


async def test_submit_already_submitted(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Double submit"},
    )
    req_id = create.json()["id"]

    await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("f.txt", io.BytesIO(b"data"), "text/plain")},
        data={"output_type": "other"},
    )
    await client.post(f"/requests/{req_id}/submit")
    r = await client.post(f"/requests/{req_id}/submit")
    assert r.status_code == 409


async def test_update_and_get_metadata(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Meta test"},
    )
    req_id = create.json()["id"]

    upload = await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("m.csv", io.BytesIO(b"x"), "text/csv")},
        data={"output_type": "tabular"},
    )
    obj_id = upload.json()["id"]

    r = await client.patch(
        f"/requests/{req_id}/objects/{obj_id}/metadata",
        json={
            "title": "My Output",
            "description": "Desc",
            "researcher_justification": "Needed for analysis",
            "suppression_notes": "Cell suppression applied",
            "tags": {"key": "value"},
        },
    )
    assert r.status_code == 200
    meta = r.json()
    assert meta["title"] == "My Output"
    assert meta["tags"] == {"key": "value"}

    r2 = await client.get(f"/requests/{req_id}/objects/{obj_id}/metadata")
    assert r2.status_code == 200
    assert r2.json()["title"] == "My Output"


async def test_list_audit_events(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Audit test"},
    )
    req_id = create.json()["id"]

    r = await client.get(f"/requests/{req_id}/audit")
    assert r.status_code == 200
    events = r.json()
    assert len(events) >= 1
    assert events[0]["event_type"] == "request.created"


async def test_upload_audit_event(researcher_setup):
    client, project_id = researcher_setup
    create = await client.post(
        "/requests",
        json={"project_id": str(project_id), "direction": "egress", "title": "Audit upload"},
    )
    req_id = create.json()["id"]

    await client.post(
        f"/requests/{req_id}/objects",
        files={"file": ("a.csv", io.BytesIO(b"1,2"), "text/csv")},
        data={"output_type": "tabular"},
    )

    r = await client.get(f"/requests/{req_id}/audit")
    event_types = [e["event_type"] for e in r.json()]
    assert "object.uploaded" in event_types


# --- Iteration 5: Revision cycle ---


async def _setup_changes_requested(client, project_id, db_session):
    """Create request with object in CHANGES_REQUESTED state."""
    from trevor.models.request import (
        AirlockRequest,
        AirlockRequestStatus,
        OutputObject,
        OutputObjectState,
    )

    # Create request
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Revision test",
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

    # Move request to CHANGES_REQUESTED and object to CHANGES_REQUESTED
    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.CHANGES_REQUESTED
    db_session.add(req)

    obj = await db_session.get(OutputObject, uuid.UUID(object_id))
    obj.state = OutputObjectState.CHANGES_REQUESTED
    db_session.add(obj)
    await db_session.commit()

    return request_id, object_id


async def test_replace_object(researcher_setup, db_session):
    client, project_id = researcher_setup
    request_id, object_id = await _setup_changes_requested(client, project_id, db_session)

    r = await client.post(
        f"/requests/{request_id}/objects/{object_id}/replace",
        files={"file": ("data_v2.csv", b"a,b\n10,20\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": "freq_table"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["version"] == 2
    assert data["replaces_id"] == object_id
    assert data["state"] == "PENDING"
    assert data["filename"] == "data_v2.csv"

    # Original should be SUPERSEDED
    r = await client.get(f"/requests/{request_id}/objects/{object_id}")
    assert r.json()["state"] == "SUPERSEDED"


async def test_replace_wrong_state(researcher_setup, db_session):
    """Cannot replace in DRAFT state."""
    client, project_id = researcher_setup

    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Draft",
        },
    )
    request_id = r.json()["id"]

    r = await client.post(
        f"/requests/{request_id}/objects",
        files={"file": ("d.csv", b"x\n1\n", "text/csv")},
        data={"output_type": "tabular"},
    )
    object_id = r.json()["id"]

    r = await client.post(
        f"/requests/{request_id}/objects/{object_id}/replace",
        files={"file": ("d2.csv", b"x\n2\n", "text/csv")},
        data={"output_type": "tabular"},
    )
    assert r.status_code == 409
    assert "DRAFT" in r.json()["detail"]


async def test_replace_approved_object_blocked(researcher_setup, db_session):
    """Cannot replace an approved object."""
    from trevor.models.request import OutputObject, OutputObjectState

    client, project_id = researcher_setup
    request_id, object_id = await _setup_changes_requested(client, project_id, db_session)

    # Set object to APPROVED
    obj = await db_session.get(OutputObject, uuid.UUID(object_id))
    obj.state = OutputObjectState.APPROVED
    db_session.add(obj)
    await db_session.commit()

    r = await client.post(
        f"/requests/{request_id}/objects/{object_id}/replace",
        files={"file": ("new.csv", b"x\n1\n", "text/csv")},
        data={"output_type": "tabular"},
    )
    assert r.status_code == 409
    assert "APPROVED" in r.json()["detail"]


async def test_version_history(researcher_setup, db_session):
    client, project_id = researcher_setup
    request_id, object_id = await _setup_changes_requested(client, project_id, db_session)

    # Replace to create v2
    r = await client.post(
        f"/requests/{request_id}/objects/{object_id}/replace",
        files={"file": ("v2.csv", b"a\n10\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": "test"},
    )
    assert r.status_code == 201
    new_object_id = r.json()["id"]

    # Get versions via original
    r = await client.get(f"/requests/{request_id}/objects/{object_id}/versions")
    assert r.status_code == 200
    versions = r.json()
    assert len(versions) == 2
    assert versions[0]["version"] == 1
    assert versions[1]["version"] == 2

    # Get versions via new object
    r = await client.get(f"/requests/{request_id}/objects/{new_object_id}/versions")
    assert len(r.json()) == 2


async def test_resubmit(researcher_setup, db_session):
    client, project_id = researcher_setup
    request_id, object_id = await _setup_changes_requested(client, project_id, db_session)

    # Replace object (creates PENDING v2)
    r = await client.post(
        f"/requests/{request_id}/objects/{object_id}/replace",
        files={"file": ("v2.csv", b"a\n10\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": "test"},
    )
    assert r.status_code == 201

    # Resubmit
    r = await client.post(f"/requests/{request_id}/resubmit")
    assert r.status_code == 200
    assert r.json()["status"] == "SUBMITTED"


async def test_resubmit_wrong_state(researcher_setup):
    client, project_id = researcher_setup

    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Draft resubmit",
        },
    )
    request_id = r.json()["id"]

    r = await client.post(f"/requests/{request_id}/resubmit")
    assert r.status_code == 409
    assert "DRAFT" in r.json()["detail"]


async def test_resubmit_requires_pending(researcher_setup, db_session):
    """Resubmit without pending objects fails."""
    from trevor.models.request import AirlockRequest, AirlockRequestStatus

    client, project_id = researcher_setup

    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "No pending",
        },
    )
    request_id = r.json()["id"]

    # Move to CHANGES_REQUESTED (no objects)
    req = await db_session.get(AirlockRequest, uuid.UUID(request_id))
    req.status = AirlockRequestStatus.CHANGES_REQUESTED
    db_session.add(req)
    await db_session.commit()

    r = await client.post(f"/requests/{request_id}/resubmit")
    assert r.status_code == 422
    assert "pending" in r.json()["detail"].lower()


async def test_metadata_preserved_on_replace(researcher_setup, db_session):
    """Metadata carries forward on replacement (same logical_object_id)."""
    client, project_id = researcher_setup
    request_id, object_id = await _setup_changes_requested(client, project_id, db_session)

    # Update metadata on v1
    r = await client.patch(
        f"/requests/{request_id}/objects/{object_id}/metadata",
        json={
            "title": "My Table",
            "researcher_justification": "Important analysis",
        },
    )
    assert r.status_code == 200

    # Replace
    r = await client.post(
        f"/requests/{request_id}/objects/{object_id}/replace",
        files={"file": ("v2.csv", b"a\n10\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": "test"},
    )
    new_object_id = r.json()["id"]

    # Metadata on v2 should be same (shared logical_object_id)
    r = await client.get(f"/requests/{request_id}/objects/{new_object_id}/metadata")
    assert r.status_code == 200
    assert r.json()["title"] == "My Table"
    assert r.json()["researcher_justification"] == "Important analysis"
