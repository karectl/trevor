"""Tests for agent review job and review endpoints."""

import uuid

import pytest

from trevor.agent.agent import decide_overall, run_agent_review
from trevor.agent.schemas import ObjectAssessment, RuleResult
from trevor.models.request import (
    AirlockRequest,
    AirlockRequestStatus,
    OutputObject,
    OutputObjectMetadata,
    OutputObjectState,
    OutputType,
)
from trevor.models.review import Review, ReviewDecision, ReviewerType

# --- decide_overall ---


def test_decide_overall_all_approve():
    assessments = [
        ObjectAssessment(object_id=uuid.uuid4(), statbarn_confirmed=True, recommendation="approve"),
        ObjectAssessment(object_id=uuid.uuid4(), statbarn_confirmed=True, recommendation="approve"),
    ]
    assert decide_overall(assessments) == "approved"


def test_decide_overall_with_escalate():
    assessments = [
        ObjectAssessment(object_id=uuid.uuid4(), statbarn_confirmed=True, recommendation="approve"),
        ObjectAssessment(
            object_id=uuid.uuid4(), statbarn_confirmed=True, recommendation="escalate"
        ),
    ]
    assert decide_overall(assessments) == "changes_requested"


def test_decide_overall_changes_requested():
    assessments = [
        ObjectAssessment(
            object_id=uuid.uuid4(),
            statbarn_confirmed=True,
            recommendation="changes_requested",
        ),
    ]
    assert decide_overall(assessments) == "changes_requested"


# --- run_agent_review ---


@pytest.mark.asyncio
async def test_run_agent_review_no_llm():
    oid = uuid.uuid4()
    assessment = ObjectAssessment(
        object_id=oid,
        statbarn_confirmed=True,
        rule_checks=[
            RuleResult(rule="file_not_empty", passed=True, detail="OK", severity="critical"),
        ],
        disclosure_risk="none",
        recommendation="approve",
    )
    result = await run_agent_review(
        [(assessment, "data.csv")],
        llm_enabled=False,
    )
    assert result["decision"] == "approved"
    assert "Reviewed 1 objects" in result["summary"]
    assert len(result["findings"]) == 1
    assert result["findings"][0]["object_id"] == str(oid)


# --- Agent review job integration test ---


@pytest.mark.asyncio
async def test_agent_review_job_integration(engine, db_session):
    """Test the full agent review flow with DB."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    from trevor.models.project import Project, ProjectMembership, ProjectRole
    from trevor.models.user import User
    from trevor.settings import Settings

    # Create user + project + membership
    user = User(
        keycloak_sub="agent-test-sub",
        username="agent-test-user",
        email="agent@test.com",
        given_name="Agent",
        family_name="Tester",
        affiliation="Test Org",
        crd_name="agent-test",
        active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    project = Project(crd_name="agent-test-proj", display_name="Agent Test")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    membership = ProjectMembership(
        user_id=user.id,
        project_id=project.id,
        role=ProjectRole.RESEARCHER,
        assigned_by=user.id,
    )
    db_session.add(membership)
    await db_session.commit()

    # Create request in SUBMITTED state
    req = AirlockRequest(
        project_id=project.id,
        direction="egress",
        title="Test agent review",
        submitted_by=user.id,
        status=AirlockRequestStatus.SUBMITTED,
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    # Create output object
    obj = OutputObject(
        request_id=req.id,
        filename="test.csv",
        output_type=OutputType.TABULAR,
        statbarn="freq_table",
        storage_key=f"{project.id}/{req.id}/test/1/test.csv",
        checksum_sha256="abc123",
        size_bytes=100,
        state=OutputObjectState.PENDING,
        uploaded_by=user.id,
    )
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)

    # Create metadata
    meta = OutputObjectMetadata(
        logical_object_id=obj.logical_object_id,
        researcher_justification="Frequency table for paper",
    )
    db_session.add(meta)
    await db_session.commit()

    # Build context mimicking ARQ worker
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(dev_auth_bypass=True, database_url="sqlite+aiosqlite:///:memory:")

    ctx = {"session_factory": session_factory, "settings": settings}

    from trevor.worker import agent_review_job

    await agent_review_job(ctx, str(req.id))

    # Verify: request should be in HUMAN_REVIEW
    await db_session.refresh(req)
    assert req.status == AirlockRequestStatus.HUMAN_REVIEW

    # Verify: review record created
    from sqlmodel import select

    result = await db_session.exec(select(Review).where(Review.request_id == req.id))
    review = result.first()
    assert review is not None
    assert review.reviewer_type == ReviewerType.AGENT
    assert review.reviewer_id is None
    assert review.decision in [d.value for d in ReviewDecision]
    assert len(review.findings) > 0


# --- Review endpoint tests ---


@pytest.mark.asyncio
async def test_list_reviews_empty(researcher_setup):
    client, project_id = researcher_setup

    # Create a request
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Review test",
        },
    )
    assert r.status_code == 201
    request_id = r.json()["id"]

    # List reviews — should be empty
    r = await client.get(f"/requests/{request_id}/reviews")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_get_review_not_found(researcher_setup):
    client, project_id = researcher_setup

    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "Review test 2",
        },
    )
    request_id = r.json()["id"]

    fake_review_id = str(uuid.uuid4())
    r = await client.get(f"/requests/{request_id}/reviews/{fake_review_id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_reviews_after_agent_review(researcher_setup, db_session):
    """Create review directly in DB and verify endpoint returns it."""
    client, project_id = researcher_setup

    # Create request
    r = await client.post(
        "/requests",
        json={
            "project_id": str(project_id),
            "direction": "egress",
            "title": "With review",
        },
    )
    request_id = r.json()["id"]

    # Insert review directly
    review = Review(
        request_id=uuid.UUID(request_id),
        reviewer_id=None,
        reviewer_type=ReviewerType.AGENT,
        decision=ReviewDecision.APPROVED,
        summary="All checks passed",
        findings=[{"object_id": str(uuid.uuid4()), "statbarn_confirmed": True}],
    )
    db_session.add(review)
    await db_session.commit()
    await db_session.refresh(review)

    # List reviews
    r = await client.get(f"/requests/{request_id}/reviews")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["decision"] == "approved"
    assert data[0]["reviewer_type"] == "agent"

    # Get single review
    r = await client.get(f"/requests/{request_id}/reviews/{review.id}")
    assert r.status_code == 200
    assert r.json()["summary"] == "All checks passed"
