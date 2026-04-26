"""CRD sync service — reconcile CR8TOR CRDs into trevor DB.

Pure business logic: accepts parsed CRD dicts, performs DB operations.
No direct kubernetes client dependency — fully testable without a cluster.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.project import Project, ProjectMembership, ProjectRole, ProjectStatus
from trevor.models.user import User

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# CRD parsers
# ---------------------------------------------------------------------------


def parse_project_crd(crd_raw: dict) -> dict:
    """Extract project fields from a raw Project CRD dict.

    Returns:
        {
            "project_id": UUID | None,   # from cr8tor.io/project-id label
            "crd_name": str,             # metadata.name
            "display_name": str,         # spec.display_name → spec.description → metadata.name
        }
    """
    metadata = crd_raw.get("metadata", {})
    labels = metadata.get("labels", {})
    spec = crd_raw.get("spec", {})

    project_id_str = labels.get("cr8tor.io/project-id")
    project_id = uuid.UUID(project_id_str) if project_id_str else None

    return {
        "project_id": project_id,
        "crd_name": metadata.get("name", ""),
        "display_name": (
            spec.get("display_name", "") or spec.get("description", "") or metadata.get("name", "")
        ),
    }


def parse_user_crd(crd_raw: dict) -> dict:
    """Extract user fields from a raw User CRD dict.

    Returns:
        {
            "crd_name": str,
            "username": str,
            "given_name": str,
            "family_name": str,
            "email": str,
            "affiliation": str,
            "enabled": bool,
        }
    """
    metadata = crd_raw.get("metadata", {})
    spec = crd_raw.get("spec", {})
    return {
        "crd_name": metadata.get("name", ""),
        "username": spec.get("username", metadata.get("name", "")),
        "given_name": spec.get("given_name", ""),
        "family_name": spec.get("family_name", ""),
        "email": spec.get("email", ""),
        "affiliation": spec.get("affiliation", ""),
        "enabled": spec.get("enabled", True),
    }


def extract_researcher_memberships(groups_raw: list[dict]) -> dict[str, set[str]]:
    """From a list of Group CRD dicts, build {project_crd_name: {username, ...}}.

    Rules (ADR-0012):
    - Parent group ``{project-name}`` → members are researchers
    - Subgroup ``{project-name}-analyst`` → members are researchers
    - Subgroup ``{project-name}-admin`` → ignored (no automatic trevor role)
    - Deduplication across parent + analyst
    """
    # Collect all group names and their members
    groups: dict[str, list[str]] = {}
    for g in groups_raw:
        meta = g.get("metadata", {})
        name = meta.get("name", "")
        spec = g.get("spec", {})
        members = spec.get("members", [])
        if isinstance(members, list):
            groups[name] = [str(m) for m in members]

    result: dict[str, set[str]] = {}

    # Identify parent groups (those without known suffix patterns from any group set)
    all_names = set(groups.keys())
    analyst_suffix = "-analyst"
    admin_suffix = "-admin"

    for name in all_names:
        # Skip sub-groups
        if name.endswith(analyst_suffix) or name.endswith(admin_suffix):
            continue

        project_name = name
        members: set[str] = set(groups.get(name, []))

        # Merge analyst sub-group members
        analyst_name = f"{project_name}{analyst_suffix}"
        if analyst_name in all_names:
            members.update(groups.get(analyst_name, []))

        result[project_name] = members

    return result


# ---------------------------------------------------------------------------
# Reconcile functions
# ---------------------------------------------------------------------------


async def reconcile_projects(
    project_crds: list[dict],
    session: AsyncSession,
) -> tuple[int, int, int]:
    """Sync Project CRDs into DB.

    Returns (created, updated, archived) counts.
    """
    created = updated = archived = 0
    crd_names: set[str] = set()

    for crd_raw in project_crds:
        parsed = parse_project_crd(crd_raw)
        crd_name = parsed["crd_name"]
        if not crd_name:
            continue
        crd_names.add(crd_name)

        result = await session.exec(select(Project).where(Project.crd_name == crd_name))
        project = result.first()

        if project is None:
            project = Project(
                id=parsed["project_id"] or uuid.uuid4(),
                crd_name=crd_name,
                display_name=parsed["display_name"],
                status=ProjectStatus.ACTIVE,
                synced_at=_utcnow(),
            )
            session.add(project)
            created += 1
        else:
            changed = False
            if parsed["display_name"] and project.display_name != parsed["display_name"]:
                project.display_name = parsed["display_name"]
                changed = True
            if project.status == ProjectStatus.ARCHIVED:
                project.status = ProjectStatus.ACTIVE
                changed = True
            project.synced_at = _utcnow()
            session.add(project)
            if changed:
                updated += 1

    # Archive projects no longer in CRDs
    all_result = await session.exec(select(Project))
    for project in all_result.all():
        if project.crd_name not in crd_names and project.status == ProjectStatus.ACTIVE:
            project.status = ProjectStatus.ARCHIVED
            project.synced_at = _utcnow()
            session.add(project)
            archived += 1

    await session.flush()
    return created, updated, archived


async def reconcile_users(
    user_crds: list[dict],
    session: AsyncSession,
) -> tuple[int, int]:
    """Sync User CRDs into DB.

    Returns (created, updated) counts.
    """
    created = updated = 0

    for crd_raw in user_crds:
        parsed = parse_user_crd(crd_raw)
        crd_name = parsed["crd_name"]
        if not crd_name:
            continue

        result = await session.exec(select(User).where(User.crd_name == crd_name))
        user = result.first()

        if user is None:
            user = User(
                id=uuid.uuid4(),
                keycloak_sub=None,  # set on first login
                username=parsed["username"],
                email=parsed["email"],
                given_name=parsed["given_name"],
                family_name=parsed["family_name"],
                affiliation=parsed["affiliation"],
                crd_name=crd_name,
                active=parsed["enabled"],
                crd_synced_at=_utcnow(),
                created_at=_utcnow(),
            )
            session.add(user)
            created += 1
        else:
            changed = False
            for field, val in [
                ("username", parsed["username"]),
                ("email", parsed["email"]),
                ("given_name", parsed["given_name"]),
                ("family_name", parsed["family_name"]),
                ("affiliation", parsed["affiliation"]),
            ]:
                if val and getattr(user, field) != val:
                    setattr(user, field, val)
                    changed = True
            active = parsed["enabled"]
            if user.active != active:
                user.active = active
                changed = True
            user.crd_synced_at = _utcnow()
            session.add(user)
            if changed:
                updated += 1

    await session.flush()
    return created, updated


async def reconcile_memberships(
    researcher_map: dict[str, set[str]],
    session: AsyncSession,
) -> tuple[int, int]:
    """Sync researcher memberships from Group CRDs.

    Returns (created, removed) counts.
    Checker/senior_checker memberships are never touched.
    """
    created = removed = 0

    for project_crd_name, usernames in researcher_map.items():
        proj_result = await session.exec(
            select(Project).where(Project.crd_name == project_crd_name)
        )
        project = proj_result.first()
        if project is None:
            logger.warning("reconcile_memberships: project '%s' not in DB", project_crd_name)
            continue

        # Existing researcher memberships for this project
        existing_result = await session.exec(
            select(ProjectMembership).where(
                ProjectMembership.project_id == project.id,
                ProjectMembership.role == ProjectRole.RESEARCHER,
            )
        )
        existing = {m.user_id: m for m in existing_result.all()}

        # Resolve users from usernames
        desired_user_ids: set[uuid.UUID] = set()
        for username in usernames:
            u_result = await session.exec(select(User).where(User.username == username))
            user = u_result.first()
            if user is None:
                logger.debug("reconcile_memberships: user '%s' not in DB — skipping", username)
                continue

            # Check for role conflict
            conflict_result = await session.exec(
                select(ProjectMembership).where(
                    ProjectMembership.project_id == project.id,
                    ProjectMembership.user_id == user.id,
                    ProjectMembership.role.in_(  # type: ignore[attr-defined]
                        [ProjectRole.OUTPUT_CHECKER, ProjectRole.SENIOR_CHECKER]
                    ),
                )
            )
            if conflict_result.first():
                logger.warning(
                    "reconcile_memberships: role conflict — user '%s' is researcher+checker "
                    "on project '%s'",
                    username,
                    project_crd_name,
                )
                continue

            desired_user_ids.add(user.id)

            if user.id not in existing:
                membership = ProjectMembership(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    project_id=project.id,
                    role=ProjectRole.RESEARCHER,
                    assigned_at=_utcnow(),
                )
                session.add(membership)
                created += 1

        # Remove researcher memberships for users no longer in CRD
        for user_id, membership in existing.items():
            if user_id not in desired_user_ids:
                await session.delete(membership)
                removed += 1

    await session.flush()
    return created, removed


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def full_reconcile(
    project_crds: list[dict],
    group_crds: list[dict],
    user_crds: list[dict],
    session: AsyncSession,
) -> dict:
    """Run full reconcile cycle. Returns stats dict."""
    user_stats = await reconcile_users(user_crds, session)
    project_stats = await reconcile_projects(project_crds, session)
    researcher_map = extract_researcher_memberships(group_crds)
    membership_stats = await reconcile_memberships(researcher_map, session)
    await session.commit()

    stats = {
        "users": {"created": user_stats[0], "updated": user_stats[1]},
        "projects": {
            "created": project_stats[0],
            "updated": project_stats[1],
            "archived": project_stats[2],
        },
        "memberships": {"created": membership_stats[0], "removed": membership_stats[1]},
    }
    logger.info("full_reconcile: %s", stats)
    return stats
