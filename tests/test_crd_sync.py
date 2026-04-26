"""Tests for CRD sync service (iteration 13).

No Kubernetes cluster needed — CRD data is passed as plain dicts.
"""

from __future__ import annotations

import uuid

import pytest
from sqlmodel import select

from trevor.models.project import Project, ProjectMembership, ProjectRole, ProjectStatus
from trevor.models.user import User
from trevor.services.crd_sync_service import (
    extract_researcher_memberships,
    full_reconcile,
    parse_project_crd,
    parse_user_crd,
    reconcile_memberships,
    reconcile_projects,
    reconcile_users,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ID = str(uuid.uuid4())


def make_project_crd(
    name: str = "proj-1",
    description: str = "Project One",
    project_id: str | None = None,
) -> dict:
    labels = {}
    if project_id:
        labels["cr8tor.io/project-id"] = project_id
    return {
        "metadata": {"name": name, "labels": labels},
        "spec": {"description": description},
    }


def make_user_crd(
    name: str = "alice",
    username: str = "alice",
    given: str = "Alice",
    family: str = "Smith",
    email: str = "alice@example.com",
    affiliation: str = "Uni",
    enabled: bool = True,
) -> dict:
    return {
        "metadata": {"name": name},
        "spec": {
            "username": username,
            "given_name": given,
            "family_name": family,
            "email": email,
            "affiliation": affiliation,
            "enabled": enabled,
        },
    }


def make_group_crd(name: str, members: list[str]) -> dict:
    return {"metadata": {"name": name}, "spec": {"members": members}}


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


def test_parse_project_crd() -> None:
    pid = str(uuid.uuid4())
    raw = make_project_crd("lancs-1", "Lancs Study", project_id=pid)
    parsed = parse_project_crd(raw)
    assert parsed["crd_name"] == "lancs-1"
    assert parsed["display_name"] == "Lancs Study"
    assert parsed["project_id"] == uuid.UUID(pid)


def test_parse_project_crd_no_label() -> None:
    raw = make_project_crd("lancs-1", "Lancs Study")
    parsed = parse_project_crd(raw)
    assert parsed["project_id"] is None


def test_parse_user_crd() -> None:
    raw = make_user_crd()
    parsed = parse_user_crd(raw)
    assert parsed["crd_name"] == "alice"
    assert parsed["username"] == "alice"
    assert parsed["given_name"] == "Alice"
    assert parsed["email"] == "alice@example.com"
    assert parsed["enabled"] is True


def test_extract_researcher_memberships_basic() -> None:
    groups = [
        make_group_crd("proj-1", ["alice", "bob"]),
        make_group_crd("proj-1-analyst", ["charlie"]),
        make_group_crd("proj-1-admin", ["adminuser"]),
    ]
    result = extract_researcher_memberships(groups)
    assert "proj-1" in result
    assert result["proj-1"] == {"alice", "bob", "charlie"}
    # admin group not included
    assert "adminuser" not in result.get("proj-1", set())
    # sub-groups not present as top-level keys
    assert "proj-1-analyst" not in result
    assert "proj-1-admin" not in result


def test_extract_researcher_memberships_dedup() -> None:
    groups = [
        make_group_crd("proj-1", ["alice"]),
        make_group_crd("proj-1-analyst", ["alice", "bob"]),
    ]
    result = extract_researcher_memberships(groups)
    # alice appears in both — should only count once
    assert result["proj-1"] == {"alice", "bob"}


# ---------------------------------------------------------------------------
# reconcile_projects
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_projects_create(db_session) -> None:
    crds = [make_project_crd("proj-1", "Project One")]
    created, updated, archived = await reconcile_projects(crds, db_session)
    assert created == 1
    assert updated == 0
    result = await db_session.exec(select(Project).where(Project.crd_name == "proj-1"))
    p = result.first()
    assert p is not None
    assert p.display_name == "Project One"
    assert p.status == ProjectStatus.ACTIVE


@pytest.mark.anyio
async def test_reconcile_projects_update(db_session) -> None:
    crds = [make_project_crd("proj-1", "Original Name")]
    await reconcile_projects(crds, db_session)
    crds2 = [make_project_crd("proj-1", "Updated Name")]
    created, updated, archived = await reconcile_projects(crds2, db_session)
    assert created == 0
    assert updated == 1
    result = await db_session.exec(select(Project).where(Project.crd_name == "proj-1"))
    p = result.first()
    assert p.display_name == "Updated Name"


@pytest.mark.anyio
async def test_reconcile_projects_archive(db_session) -> None:
    crds = [make_project_crd("proj-1", "Project One")]
    await reconcile_projects(crds, db_session)
    # Now CRD list is empty — proj-1 should be archived
    created, updated, archived = await reconcile_projects([], db_session)
    assert archived == 1
    result = await db_session.exec(select(Project).where(Project.crd_name == "proj-1"))
    p = result.first()
    assert p.status == ProjectStatus.ARCHIVED


@pytest.mark.anyio
async def test_reconcile_projects_preserves_id_from_label(db_session) -> None:
    pid = str(uuid.uuid4())
    crds = [make_project_crd("proj-1", "Project One", project_id=pid)]
    await reconcile_projects(crds, db_session)
    result = await db_session.exec(select(Project).where(Project.crd_name == "proj-1"))
    p = result.first()
    assert str(p.id) == pid


# ---------------------------------------------------------------------------
# reconcile_users
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_users_create(db_session) -> None:
    crds = [make_user_crd("alice", "alice")]
    created, updated = await reconcile_users(crds, db_session)
    assert created == 1
    result = await db_session.exec(select(User).where(User.crd_name == "alice"))
    u = result.first()
    assert u is not None
    assert u.keycloak_sub is None  # not set until first login


@pytest.mark.anyio
async def test_reconcile_users_update(db_session) -> None:
    crds = [make_user_crd("alice", email="old@example.com")]
    await reconcile_users(crds, db_session)
    crds2 = [make_user_crd("alice", email="new@example.com")]
    created, updated = await reconcile_users(crds2, db_session)
    assert updated == 1
    result = await db_session.exec(select(User).where(User.crd_name == "alice"))
    u = result.first()
    assert u.email == "new@example.com"


@pytest.mark.anyio
async def test_reconcile_users_keycloak_sub_untouched(db_session) -> None:
    crds = [make_user_crd("alice")]
    await reconcile_users(crds, db_session)
    # Manually set keycloak_sub
    result = await db_session.exec(select(User).where(User.crd_name == "alice"))
    u = result.first()
    kc_sub = str(uuid.uuid4())
    u.keycloak_sub = kc_sub
    db_session.add(u)
    await db_session.flush()
    # Re-sync — keycloak_sub must not be touched
    await reconcile_users(crds, db_session)
    result2 = await db_session.exec(select(User).where(User.crd_name == "alice"))
    u2 = result2.first()
    assert u2.keycloak_sub == kc_sub


@pytest.mark.anyio
async def test_reconcile_users_disable(db_session) -> None:
    crds = [make_user_crd("alice", enabled=True)]
    await reconcile_users(crds, db_session)
    crds2 = [make_user_crd("alice", enabled=False)]
    await reconcile_users(crds2, db_session)
    result = await db_session.exec(select(User).where(User.crd_name == "alice"))
    u = result.first()
    assert u.active is False


# ---------------------------------------------------------------------------
# reconcile_memberships
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconcile_memberships_create(db_session) -> None:
    # Seed project and user
    project = Project(id=uuid.uuid4(), crd_name="proj-1", display_name="P1")
    user = User(
        id=uuid.uuid4(),
        username="alice",
        email="a@a.com",
        given_name="A",
        family_name="B",
        affiliation="Uni",
        crd_name="alice",
        active=True,
    )
    db_session.add(project)
    db_session.add(user)
    await db_session.flush()

    researcher_map = {"proj-1": {"alice"}}
    created, removed = await reconcile_memberships(researcher_map, db_session)
    assert created == 1
    result = await db_session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project.id,
            ProjectMembership.role == ProjectRole.RESEARCHER,
        )
    )
    assert result.first() is not None


@pytest.mark.anyio
async def test_reconcile_memberships_remove(db_session) -> None:
    project = Project(id=uuid.uuid4(), crd_name="proj-1", display_name="P1")
    user = User(
        id=uuid.uuid4(),
        username="alice",
        email="a@a.com",
        given_name="A",
        family_name="B",
        affiliation="Uni",
        crd_name="alice",
        active=True,
    )
    db_session.add(project)
    db_session.add(user)
    await db_session.flush()
    # Create membership first
    m = ProjectMembership(
        id=uuid.uuid4(),
        user_id=user.id,
        project_id=project.id,
        role=ProjectRole.RESEARCHER,
    )
    db_session.add(m)
    await db_session.flush()

    # Now reconcile with empty set — alice removed from project
    created, removed = await reconcile_memberships({"proj-1": set()}, db_session)
    assert removed == 1
    result = await db_session.exec(
        select(ProjectMembership).where(ProjectMembership.project_id == project.id)
    )
    assert result.first() is None


@pytest.mark.anyio
async def test_reconcile_memberships_preserve_checker(db_session) -> None:
    project = Project(id=uuid.uuid4(), crd_name="proj-1", display_name="P1")
    user = User(
        id=uuid.uuid4(),
        username="bob",
        email="b@b.com",
        given_name="B",
        family_name="C",
        affiliation="Uni",
        crd_name="bob",
        active=True,
    )
    db_session.add(project)
    db_session.add(user)
    await db_session.flush()
    checker_m = ProjectMembership(
        id=uuid.uuid4(),
        user_id=user.id,
        project_id=project.id,
        role=ProjectRole.OUTPUT_CHECKER,
    )
    db_session.add(checker_m)
    await db_session.flush()

    # Reconcile with bob as researcher — conflict → skipped, checker preserved
    created, removed = await reconcile_memberships({"proj-1": {"bob"}}, db_session)
    assert created == 0  # conflict — not added as researcher
    result = await db_session.exec(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project.id,
            ProjectMembership.role == ProjectRole.OUTPUT_CHECKER,
        )
    )
    assert result.first() is not None  # checker membership preserved


# ---------------------------------------------------------------------------
# full_reconcile
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_reconcile(db_session) -> None:
    project_crds = [make_project_crd("proj-1", "Project One")]
    user_crds = [make_user_crd("alice", "alice")]
    group_crds = [
        make_group_crd("proj-1", ["alice"]),
    ]
    stats = await full_reconcile(project_crds, group_crds, user_crds, db_session)
    assert stats["projects"]["created"] == 1
    assert stats["users"]["created"] == 1
    assert stats["memberships"]["created"] == 1
