#!/usr/bin/env python3
"""Seed the dev postgres DB with real Keycloak users and project memberships.

Run after `tilt up` when postgres and keycloak are both healthy.
Also registered as a Tilt local_resource so it runs automatically.

Environment variables (defaults match sample.env / Tilt stack):
    DATABASE_URL          postgres connection string
    KEYCLOAK_URL          browser-facing Keycloak URL
    KEYCLOAK_REALM        realm name (default: karectl)
    KEYCLOAK_ADMIN_USERNAME  admin username (default: admin)
    KEYCLOAK_ADMIN_PASSWORD  admin password (default: admin)
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://trevor:trevor@localhost:5432/trevor",
)
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "karectl")
KC_ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USERNAME", "admin")
KC_ADMIN_PASS = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")

# The project CRD name that is applied by Tilt (deploy/dev/sample-project/).
# CRD sync will create/update the Project row from the CRD; seed just ensures
# the row exists and that checker memberships are assigned (checkers are not
# expressed as Group CRDs — they are trevor-internal roles).
PROJECT_CRD = "interstellar"
PROJECT_DISPLAY_NAME = "Interstellar"
PROJECT_UUID = uuid.UUID("a1b2c3d4-0001-0002-0003-000000000001")

# Dev users: (keycloak_username, role_in_project, given, family, affiliation)
# role=None means no membership (admin-user gets access via realm role only).
# researcher-1's researcher membership comes from CRD sync (Group CRD);
# we still upsert the User row here so the record exists before first login.
DEV_USERS = [
    ("researcher-1", "researcher", "Alice", "Researcher", "Lancaster University"),
    ("checker-1", "output_checker", "Bob", "Checker", "Lancaster University"),
    ("checker-2", "output_checker", "Carol", "Senior", "Lancaster University"),
    ("admin-user", None, "Admin", "User", "Lancaster University"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def get_keycloak_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": KC_ADMIN_USER,
            "password": KC_ADMIN_PASS,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def get_keycloak_users(token: str, client: httpx.AsyncClient) -> dict[str, dict]:
    """Return {username: user_repr} for all users in the realm."""
    resp = await client.get(
        f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}/users",
        headers={"Authorization": f"Bearer {token}"},
        params={"max": 100},
    )
    resp.raise_for_status()
    return {u["username"]: u for u in resp.json()}


async def seed(session: AsyncSession, kc_users: dict[str, dict]) -> None:
    from trevor.models.project import Project, ProjectMembership, ProjectRole
    from trevor.models.user import User

    # ── Project ──────────────────────────────────────────────────────────────
    result = await session.exec(select(Project).where(Project.crd_name == PROJECT_CRD))
    project = result.first()
    if project is None:
        project = Project(
            id=PROJECT_UUID,
            crd_name=PROJECT_CRD,
            display_name=PROJECT_DISPLAY_NAME,
            synced_at=_utcnow(),
        )
        session.add(project)
        await session.flush()
        print(f"  Created project: {project.display_name} ({project.id})")
    else:
        # Ensure display_name is up to date (may have been set by CRD sync).
        if project.display_name != PROJECT_DISPLAY_NAME:
            project.display_name = PROJECT_DISPLAY_NAME
            session.add(project)
            await session.flush()
        print(f"  Project: {project.display_name} ({project.id})")

    # ── Users + memberships ───────────────────────────────────────────────────
    for kc_username, role, given, family, affiliation in DEV_USERS:
        kc_user = kc_users.get(kc_username)
        if kc_user is None:
            print(f"  SKIP {kc_username} — not found in Keycloak")
            continue

        kc_sub = kc_user["id"]
        email = kc_user.get("email") or f"{kc_username}@test.local"

        # Upsert User by keycloak_sub, fall back to username match.
        result = await session.exec(select(User).where(User.keycloak_sub == kc_sub))
        user = result.first()
        if user is None:
            result2 = await session.exec(select(User).where(User.username == kc_username))
            user = result2.first()

        if user is None:
            user = User(
                id=uuid.uuid4(),
                keycloak_sub=kc_sub,
                username=kc_username,
                email=email,
                given_name=given,
                family_name=family,
                affiliation=affiliation,
                crd_name=kc_username,
                active=True,
                crd_synced_at=_utcnow(),
                created_at=_utcnow(),
            )
            session.add(user)
            await session.flush()
            print(f"  Created user: {kc_username} ({user.id})")
        else:
            if user.keycloak_sub != kc_sub:
                user.keycloak_sub = kc_sub
                session.add(user)
                await session.flush()
            print(f"  Existing user: {kc_username} ({user.id})")

        if role is None:
            continue

        # Assign membership — for researcher-1 this is also done by CRD sync,
        # but seeding it here ensures it exists on first tilt up before the
        # first CRD sync cron fires.
        project_role = ProjectRole(role)
        result = await session.exec(
            select(ProjectMembership).where(
                ProjectMembership.user_id == user.id,
                ProjectMembership.project_id == project.id,
                ProjectMembership.role == project_role,
            )
        )
        if result.first() is None:
            session.add(
                ProjectMembership(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    project_id=project.id,
                    role=project_role,
                    assigned_at=_utcnow(),
                )
            )
            await session.flush()
            print(f"    → membership: {role}")
        else:
            print(f"    → membership exists: {role}")

    # checker-2 also gets senior_checker role.
    kc_user = kc_users.get("checker-2")
    if kc_user:
        result = await session.exec(select(User).where(User.keycloak_sub == kc_user["id"]))
        user = result.first()
        if user is None:
            result2 = await session.exec(select(User).where(User.username == "checker-2"))
            user = result2.first()
        if user:
            result = await session.exec(
                select(ProjectMembership).where(
                    ProjectMembership.user_id == user.id,
                    ProjectMembership.project_id == project.id,
                    ProjectMembership.role == ProjectRole.SENIOR_CHECKER,
                )
            )
            if result.first() is None:
                session.add(
                    ProjectMembership(
                        id=uuid.uuid4(),
                        user_id=user.id,
                        project_id=project.id,
                        role=ProjectRole.SENIOR_CHECKER,
                        assigned_at=_utcnow(),
                    )
                )
                await session.flush()
                print("    → membership: senior_checker (checker-2)")

    await session.commit()


async def main() -> None:
    print("Fetching Keycloak users...")
    async with httpx.AsyncClient(timeout=10) as client:
        token = await get_keycloak_token(client)
        kc_users = await get_keycloak_users(token, client)
    print(f"  Found {len(kc_users)} Keycloak users in realm '{KEYCLOAK_REALM}'")

    engine = create_async_engine(DATABASE_URL)
    async with AsyncSession(engine) as session:
        print("Seeding DB...")
        await seed(session, kc_users)

    await engine.dispose()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
