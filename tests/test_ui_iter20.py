"""Tests for iteration-20 two-panel researcher/checker UI.

Covers:
- Two-panel request detail: Datastar signals present, object nav, tabs
- Inline metadata save (including title field)
- Rebuilt checker review form: left nav panel, title in metadata section
"""

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_request_and_upload(client, project_id, *, filename="data.csv"):
    """Create a DRAFT request and upload one object. Returns (req_id, obj_id)."""
    r = await client.post(
        "/ui/requests",
        data={
            "project_id": str(project_id),
            "title": "Two-panel test request",
            "direction": "egress",
            "description": "iter-20 test",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    req_id = r.url.path.split("/")[-1]

    r2 = await client.post(
        f"/ui/requests/{req_id}/upload",
        files={"file": (filename, b"col_a,col_b\n1,2\n3,4\n", "text/csv")},
        data={"output_type": "tabular", "statbarn": ""},
        follow_redirects=True,
    )
    assert r2.status_code == 200
    # Extract object id from the detail page (look for hidden input or href)
    # We use the JSON API to get the object id reliably

    obj_resp = await client.get(f"/requests/{req_id}/objects")
    assert obj_resp.status_code == 200
    objects = obj_resp.json()
    assert len(objects) >= 1
    obj_id = objects[0]["id"]
    return req_id, obj_id


# ---------------------------------------------------------------------------
# Two-panel detail page structure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_detail_page_has_datastar_signals(researcher_setup) -> None:
    """Detail page must include Datastar signal attributes for panel switching."""
    client, project_id = researcher_setup
    req_id, _ = await _create_request_and_upload(client, project_id)

    r = await client.get(f"/ui/requests/{req_id}")
    assert r.status_code == 200
    html = r.text
    # Datastar signals block for mainTab / objIdx
    assert "data-signals" in html
    assert "mainTab" in html
    assert "objIdx" in html


@pytest.mark.anyio
async def test_detail_page_shows_object_in_nav(researcher_setup) -> None:
    """Uploaded file should appear in the left nav object list."""
    client, project_id = researcher_setup
    req_id, _ = await _create_request_and_upload(client, project_id, filename="results.csv")

    r = await client.get(f"/ui/requests/{req_id}")
    assert r.status_code == 200
    assert "results.csv" in r.text


@pytest.mark.anyio
async def test_detail_page_shows_tabs(researcher_setup) -> None:
    """Detail page must render the Reviews and Audit tab labels."""
    client, project_id = researcher_setup
    req_id, _ = await _create_request_and_upload(client, project_id)

    r = await client.get(f"/ui/requests/{req_id}")
    assert r.status_code == 200
    html = r.text
    assert "Reviews" in html
    assert "Audit" in html


@pytest.mark.anyio
async def test_detail_page_ft_icon_for_csv(researcher_setup) -> None:
    """CSV file should render a tabular ft-icon label."""
    client, project_id = researcher_setup
    req_id, _ = await _create_request_and_upload(client, project_id, filename="summary.csv")

    r = await client.get(f"/ui/requests/{req_id}")
    assert r.status_code == 200
    # ft-tabular icon or CSV label
    assert "ft-tabular" in r.text or "CSV" in r.text


# ---------------------------------------------------------------------------
# Metadata save with title
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metadata_save_stores_title(researcher_setup) -> None:
    """POST to /metadata with title= saves and shows title on detail page."""
    client, project_id = researcher_setup
    req_id, obj_id = await _create_request_and_upload(client, project_id)

    r = await client.post(
        f"/ui/requests/{req_id}/objects/{obj_id}/metadata",
        data={
            "title": "My regression output",
            "description": "Full regression results",
            "researcher_justification": "Required for sign-off",
            "suppression_notes": "",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "My regression output" in r.text


@pytest.mark.anyio
async def test_metadata_save_title_via_api(researcher_setup) -> None:
    """Saving title via UI endpoint should persist to JSON API."""
    client, project_id = researcher_setup
    req_id, obj_id = await _create_request_and_upload(client, project_id)

    await client.post(
        f"/ui/requests/{req_id}/objects/{obj_id}/metadata",
        data={
            "title": "Agent output v2",
            "description": "desc",
            "researcher_justification": "justified",
            "suppression_notes": "",
        },
        follow_redirects=False,
    )

    meta_r = await client.get(f"/requests/{req_id}/objects/{obj_id}/metadata")
    assert meta_r.status_code == 200
    meta = meta_r.json()
    assert meta["title"] == "Agent output v2"


@pytest.mark.anyio
async def test_metadata_save_title_empty_is_ok(researcher_setup) -> None:
    """Empty title is allowed (not a required field)."""
    client, project_id = researcher_setup
    req_id, obj_id = await _create_request_and_upload(client, project_id)

    r = await client.post(
        f"/ui/requests/{req_id}/objects/{obj_id}/metadata",
        data={
            "title": "",
            "description": "no title",
            "researcher_justification": "",
            "suppression_notes": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Checker review form — rebuilt with left nav panel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_review_form_left_nav_has_object(admin_client: AsyncClient, db_session) -> None:
    """Rebuilt checker review form must show file in the left nav sidebar."""
    import uuid

    from trevor.models.project import Project
    from trevor.models.request import (
        AirlockDirection,
        AirlockRequest,
        AirlockRequestStatus,
        OutputObject,
        OutputType,
    )
    from trevor.models.user import User

    user = User(
        keycloak_sub="checker-nav-test",
        username="checker-nav",
        email="checker-nav@test.com",
        given_name="Checker",
        family_name="Nav",
        affiliation="",
        crd_name="checker-nav",
        active=True,
    )
    db_session.add(user)
    project = Project(crd_name="checker-nav-proj", display_name="Nav Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(project)

    req = AirlockRequest(
        project_id=project.id,
        direction=AirlockDirection.EGRESS,
        title="Nav test request",
        submitted_by=user.id,
        status=AirlockRequestStatus.HUMAN_REVIEW,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    obj = OutputObject(
        request_id=req.id,
        logical_object_id=uuid.uuid4(),
        filename="nav_test.csv",
        output_type=OutputType.TABULAR,
        storage_key="test/key",
        checksum_sha256="abc123",
        size_bytes=100,
        uploaded_by=user.id,
    )
    db_session.add(obj)
    await db_session.commit()

    r = await admin_client.get(f"/ui/review/{req.id}")
    assert r.status_code == 200
    html = r.text
    assert "nav_test.csv" in html
    assert "rv-sidebar" in html


@pytest.mark.anyio
async def test_review_form_shows_title_in_metadata(admin_client: AsyncClient, db_session) -> None:
    """Review form metadata section shows title when set."""
    import uuid

    from trevor.models.project import Project
    from trevor.models.request import (
        AirlockDirection,
        AirlockRequest,
        AirlockRequestStatus,
        OutputObject,
        OutputObjectMetadata,
        OutputType,
    )
    from trevor.models.user import User

    user = User(
        keycloak_sub="checker-title-test",
        username="checker-title",
        email="checker-title@test.com",
        given_name="Checker",
        family_name="Title",
        affiliation="",
        crd_name="checker-title",
        active=True,
    )
    db_session.add(user)
    project = Project(crd_name="checker-title-proj", display_name="Title Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(project)

    req = AirlockRequest(
        project_id=project.id,
        direction=AirlockDirection.EGRESS,
        title="Title metadata test",
        submitted_by=user.id,
        status=AirlockRequestStatus.HUMAN_REVIEW,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    logical_id = uuid.uuid4()
    obj = OutputObject(
        request_id=req.id,
        logical_object_id=logical_id,
        filename="titled_output.csv",
        output_type=OutputType.TABULAR,
        storage_key="test/key2",
        checksum_sha256="def456",
        size_bytes=200,
        uploaded_by=user.id,
    )
    db_session.add(obj)

    meta = OutputObjectMetadata(
        logical_object_id=logical_id,
        title="Final regression table",
        description="Contains all coefficients",
        researcher_justification="Needed for publication",
    )
    db_session.add(meta)
    await db_session.commit()

    r = await admin_client.get(f"/ui/review/{req.id}")
    assert r.status_code == 200
    html = r.text
    assert "Final regression table" in html
    assert "Contains all coefficients" in html


@pytest.mark.anyio
async def test_review_form_signals_attribute(admin_client: AsyncClient, db_session) -> None:
    """Review form uses data-signals (Datastar v1 API, not data-store)."""
    import uuid

    from trevor.models.project import Project
    from trevor.models.request import (
        AirlockDirection,
        AirlockRequest,
        AirlockRequestStatus,
        OutputObject,
        OutputType,
    )
    from trevor.models.user import User

    user = User(
        keycloak_sub="checker-signals-test",
        username="checker-signals",
        email="checker-signals@test.com",
        given_name="Checker",
        family_name="Signals",
        affiliation="",
        crd_name="checker-signals",
        active=True,
    )
    db_session.add(user)
    project = Project(crd_name="checker-signals-proj", display_name="Signals Test Project")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(user)
    await db_session.refresh(project)

    req = AirlockRequest(
        project_id=project.id,
        direction=AirlockDirection.EGRESS,
        title="Signals test",
        submitted_by=user.id,
        status=AirlockRequestStatus.HUMAN_REVIEW,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    obj = OutputObject(
        request_id=req.id,
        logical_object_id=uuid.uuid4(),
        filename="signals_test.csv",
        output_type=OutputType.TABULAR,
        storage_key="test/key3",
        checksum_sha256="aaa111",
        size_bytes=50,
        uploaded_by=user.id,
    )
    db_session.add(obj)
    await db_session.commit()

    r = await admin_client.get(f"/ui/review/{req.id}")
    assert r.status_code == 200
    html = r.text
    assert "data-signals" in html
    assert "data-store=" not in html
