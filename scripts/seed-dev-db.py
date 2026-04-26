#!/usr/bin/env python3
"""Seed the dev postgres DB with real Keycloak users and project memberships.

Run after `tilt up` when postgres and keycloak are both healthy:

    uv run python scripts/seed-dev-db.py

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

# Project CRD name that must already exist in the DB (created by CR8TOR sync).
PROJECT_CRD = "lancs-tre-proj-1"

# Dev users to seed: (keycloak_username, role_in_project, given, family, affiliation)
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

    # Resolve project
    result = await session.exec(select(Project).where(Project.crd_name == PROJECT_CRD))
    project = result.first()
    if project is None:
        print(
            f"  Project '{PROJECT_CRD}' not found — run migrations and ensure CR8TOR sync has run."
        )  # noqa: E501
        return
    print(f"  Project: {project.display_name} ({project.id})")

    for kc_username, role, given, family, affiliation in DEV_USERS:
        kc_user = kc_users.get(kc_username)
        if kc_user is None:
            print(f"  SKIP {kc_username} — not found in Keycloak")
            continue

        kc_sub = kc_user["id"]
        email = kc_user.get("email", f"{kc_username}@test.local")

        # Upsert User by keycloak_sub
        result = await session.exec(select(User).where(User.keycloak_sub == kc_sub))
        user = result.first()
        if user is None:
            # Also check by username in case created without keycloak_sub
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
            # Update keycloak_sub if missing
            if user.keycloak_sub != kc_sub:
                user.keycloak_sub = kc_sub
                session.add(user)
                await session.flush()
            print(f"  Existing user: {kc_username} ({user.id})")

        if role is None:
            continue  # admin-user has no project membership (admin via realm role)

        project_role = ProjectRole(role)
        result = await session.exec(
            select(ProjectMembership).where(
                ProjectMembership.user_id == user.id,
                ProjectMembership.project_id == project.id,
                ProjectMembership.role == project_role,
            )
        )
        membership = result.first()
        if membership is None:
            membership = ProjectMembership(
                id=uuid.uuid4(),
                user_id=user.id,
                project_id=project.id,
                role=project_role,
                assigned_at=_utcnow(),
            )
            session.add(membership)
            await session.flush()
            print(f"    → membership: {role}")
        else:
            print(f"    → membership exists: {role}")

    # checker-2 also gets senior_checker
    kc_user = kc_users.get("checker-2")
    if kc_user:
        kc_sub = kc_user["id"]
        result = await session.exec(select(User).where(User.keycloak_sub == kc_sub))
        user = result.first()
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
