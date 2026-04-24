"""User service — upsert shadow record from CRD or Keycloak."""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.user import User


async def upsert_user(
    *,
    keycloak_sub: str,
    email: str,
    display_name: str,
    session: AsyncSession,
    # Optional fields for CRD sync (use defaults for backward compatibility)
    username: str | None = None,
    given_name: str | None = None,
    family_name: str | None = None,
    affiliation: str | None = None,
    crd_name: str | None = None,
    active: bool | None = None,
) -> User:
    """Create or update the local User shadow record.

    If called from dev/test bypass with only keycloak_sub/email/display_name,
    fill missing fields with sensible defaults (empty strings, False, etc.)
    to satisfy NOT NULL constraints.
    """
    stmt = select(User).where(User.username == (username or keycloak_sub))
    result = await session.exec(stmt)
    user = result.first()

    if user is None:
        user = User(
            username=username or keycloak_sub,
            email=email,
            given_name=given_name or "",
            family_name=family_name or "",
            affiliation=affiliation or "",
            crd_name=crd_name or "",
            active=active if active is not None else True,
            keycloak_sub=keycloak_sub,
        )
        session.add(user)
    else:
        # Update only the fields we were given (never overwrite keycloak_sub with None)
        user.email = email
        if given_name is not None:
            user.given_name = given_name
        if family_name is not None:
            user.family_name = family_name
        if affiliation is not None:
            user.affiliation = affiliation
        if crd_name is not None:
            user.crd_name = crd_name
        if active is not None:
            user.active = active
        if keycloak_sub is not None:
            user.keycloak_sub = keycloak_sub
        session.add(user)

    await session.commit()
    await session.refresh(user)
    return user
