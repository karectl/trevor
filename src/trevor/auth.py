"""Auth dependency — Keycloak OIDC or DEV_AUTH_BYPASS.

In production: validates session cookie (UI) or Bearer JWT (API) against Keycloak.
With DEV_AUTH_BYPASS=true: upserts a dev user and returns it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.database import get_session
from trevor.models.user import User
from trevor.oidc import fetch_openid_config, get_jwks, validate_id_token
from trevor.services.user_service import upsert_user
from trevor.session import read_session_cookie
from trevor.settings import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    """Resolved auth context carried through the request."""

    user: User
    realm_roles: list[str] = field(default_factory=list)
    is_admin: bool = False


async def validate_bearer_token(token: str, settings: Settings) -> dict:
    """Validate a Bearer JWT against Keycloak JWKS. Returns claims dict."""
    oidc_config = await fetch_openid_config(settings.keycloak_server_url, settings.keycloak_realm)
    jwks = await get_jwks(oidc_config["jwks_uri"])
    expected_issuer = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
    return validate_id_token(token, jwks, expected_issuer, settings.keycloak_client_id)


async def _user_from_claims(
    claims: dict,
    session: AsyncSession,
) -> tuple[User, list[str], bool]:
    """Upsert user from token claims and return (user, realm_roles, is_admin)."""
    realm_roles = claims.get("realm_roles", [])
    user = await upsert_user(
        keycloak_sub=claims["sub"],
        email=claims.get("email", ""),
        display_name=f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
        or claims.get("preferred_username", claims["sub"]),
        username=claims.get("preferred_username", claims["sub"]),
        given_name=claims.get("given_name", ""),
        family_name=claims.get("family_name", ""),
        affiliation=claims.get("affiliation", ""),
        crd_name=claims.get("preferred_username", claims["sub"]),
        active=True,
        session=session,
    )
    is_admin = "tre_admin" in realm_roles
    return user, realm_roles, is_admin


async def get_auth_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthContext:
    """FastAPI dependency — resolves caller identity from cookie, JWT, or dev bypass."""
    # 1. DEV_AUTH_BYPASS
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

    # 2. Session cookie (UI routes)
    session_cookie = request.cookies.get(settings.session_cookie_name)
    if session_cookie:
        data = read_session_cookie(
            session_cookie, settings.secret_key, settings.session_ttl_seconds
        )
        if data and data.exp > time.time():
            user, realm_roles, is_admin = await _user_from_claims(
                {
                    "sub": data.sub,
                    "preferred_username": data.username,
                    "email": data.email,
                    "given_name": data.display_name.split(" ", 1)[0] if data.display_name else "",
                    "family_name": data.display_name.split(" ", 1)[1]
                    if " " in data.display_name
                    else "",
                    "realm_roles": data.realm_roles,
                },
                session,
            )
            return AuthContext(user=user, realm_roles=realm_roles, is_admin=is_admin)
        # Cookie expired or invalid — fall through

    # 3. Bearer token (API routes)
    if credentials:
        try:
            claims = await validate_bearer_token(credentials.credentials, settings)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
                headers={"WWW-Authenticate": "Bearer"},
            ) from e
        user, realm_roles, is_admin = await _user_from_claims(claims, session)
        return AuthContext(user=user, realm_roles=realm_roles, is_admin=is_admin)

    # 4. No auth
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


def require_admin(auth: CurrentAuth) -> AuthContext:
    """Raise 403 if caller is not tre_admin."""
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return auth


RequireAdmin = Annotated[AuthContext, Depends(require_admin)]
