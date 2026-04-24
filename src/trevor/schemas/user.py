"""User API schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from trevor.schemas.membership import MembershipRead


class UserRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    keycloak_sub: str | None
    username: str
    email: str
    given_name: str
    family_name: str
    affiliation: str
    crd_name: str
    active: bool
    crd_synced_at: datetime
    created_at: datetime


class UserMeRead(UserRead):
    """GET /users/me — includes memberships and realm roles."""

    memberships: list[MembershipRead] = []
    realm_roles: list[str] = []
    is_admin: bool = False
