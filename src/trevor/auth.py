"""Auth dependency — Keycloak OIDC or DEV_AUTH_BYPASS.

In production: validates Bearer JWT against Keycloak JWKS.
With DEV_AUTH_BYPASS=true: upserts a dev user and returns it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.database import get_session
from trevor.models.user import User
from trevor.services.user_service import upsert_user
from trevor.settings import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    """Resolved auth context carried through the request."""

    user: User
    realm_roles: list[str] = field(default_factory=list)
    is_admin: bool = False


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthContext:
    """FastAPI dependency — resolves caller identity from JWT and upserts User."""
    if settings.dev_auth_bypass:
        token_val = credentials.credentials if credentials else ""
        is_admin = "admin" in token_val
        sub = "dev-bypass-admin" if is_admin else "dev-bypass-user"
        email = "admin@localhost" if is_admin else "dev@localhost"
        name = "Dev Admin" if is_admin else "Dev User"
        roles = ["tre_admin"] if is_admin else ["researcher"]

        user = await upsert_user(
            keycloak_sub=sub,
            email=email,
            display_name=name,
            username=sub,
            given_name="Dev",
            family_name="User" if not is_admin else "Admin",
            affiliation="dev",
            crd_name="dev-user",
            active=True,
            session=session,
        )
        return AuthContext(user=user, realm_roles=roles, is_admin=is_admin)

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # TODO (later iteration): validate JWT against Keycloak JWKS endpoint,
    # extract sub/email/display_name/realm_roles from claims, upsert_user.
    raise NotImplementedError("Keycloak JWT validation not yet implemented")


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def require_admin(auth: CurrentAuth) -> AuthContext:
    """Raise 403 if caller is not tre_admin."""
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return auth


RequireAdmin = Annotated[AuthContext, Depends(require_admin)]
