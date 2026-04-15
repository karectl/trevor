"""User routes — GET /users/me."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.auth import CurrentAuth
from trevor.database import get_session
from trevor.schemas.user import UserMeRead
from trevor.services.membership_service import list_memberships_for_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserMeRead)
async def get_me(
    auth: CurrentAuth,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserMeRead:
    memberships = await list_memberships_for_user(auth.user.id, session)
    return UserMeRead(
        id=auth.user.id,
        keycloak_sub=auth.user.keycloak_sub,
        email=auth.user.email,
        display_name=auth.user.display_name,
        created_at=auth.user.created_at,
        memberships=[m.model_dump() for m in memberships],  # type: ignore[union-attr]
        realm_roles=auth.realm_roles,
        is_admin=auth.is_admin,
    )
