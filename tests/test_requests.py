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
