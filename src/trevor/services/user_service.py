"""User service — upsert shadow record from Keycloak claims."""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.models.user import User


async def upsert_user(
    *,
    keycloak_sub: str,
    email: str,
    display_name: str,
    session: AsyncSession,
) -> User:
    """Create or update the local User shadow record based on Keycloak sub claim.

    Returns the (possibly updated) User row.
    """
    stmt = select(User).where(User.keycloak_sub == keycloak_sub)
    result = await session.exec(stmt)
    user = result.first()

    if user is None:
        user = User(keycloak_sub=keycloak_sub, email=email, display_name=display_name)
        session.add(user)
    else:
        user.email = email
        user.display_name = display_name
        session.add(user)

    await session.commit()
    await session.refresh(user)
    return user
