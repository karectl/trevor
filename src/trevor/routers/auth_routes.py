"""Auth routes — OIDC login, callback, logout."""

from __future__ import annotations

import base64
import json
import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.database import get_session
from trevor.oidc import (
    exchange_code,
    fetch_openid_config,
    generate_pkce,
    get_jwks,
    validate_id_token,
)
from trevor.services.user_service import upsert_user
from trevor.session import (
    clear_session_cookie,
    create_pkce_cookie,
    make_session_data,
    read_pkce_cookie,
    set_session_cookie,
)
from trevor.settings import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_redirect_uri(request: Request) -> str:
    """Build callback URL from request base URL."""
    return str(request.base_url).rstrip("/") + "/auth/callback"


@router.get("/login")
async def login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    next: str = "/ui/requests",
) -> RedirectResponse:
    """Redirect to Keycloak authorization endpoint with PKCE."""
    oidc_config = await fetch_openid_config(settings.keycloak_server_url, settings.keycloak_realm)
    code_verifier, code_challenge = generate_pkce()

    # Encode next URL in state
    state = base64.urlsafe_b64encode(json.dumps({"next": next}).encode()).decode()

    # Rewrite authorization_endpoint to use browser-facing keycloak_url.
    # When keycloak_internal_url differs from keycloak_url the discovery doc
    # returns the internal hostname; the browser cannot reach it.
    auth_endpoint = oidc_config["authorization_endpoint"]
    if settings.keycloak_internal_url and settings.keycloak_internal_url != settings.keycloak_url:
        auth_endpoint = auth_endpoint.replace(
            settings.keycloak_internal_url, settings.keycloak_url, 1
        )

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id": settings.keycloak_client_id,
        "redirect_uri": _build_redirect_uri(request),
        "scope": "openid email profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{auth_endpoint}?{urllib.parse.urlencode(params)}"

    response = RedirectResponse(authorize_url, status_code=302)

    # Store PKCE verifier + state in short-lived cookie
    pkce_value = create_pkce_cookie(code_verifier, state, settings.secret_key)
    secure = not settings.dev_auth_bypass
    response.set_cookie(
        key="trevor_pkce",
        value=pkce_value,
        max_age=300,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    """Handle OIDC callback — exchange code for tokens, create session."""
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code"
        )

    # Read and validate PKCE cookie
    pkce_cookie = request.cookies.get("trevor_pkce")
    if not pkce_cookie:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing PKCE cookie")

    pkce_data = read_pkce_cookie(pkce_cookie, settings.secret_key)
    if not pkce_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired PKCE cookie"
        )

    # Validate state
    if state != pkce_data["state"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="State mismatch")

    # Exchange code for tokens
    oidc_config = await fetch_openid_config(settings.keycloak_server_url, settings.keycloak_realm)
    try:
        token_response = await exchange_code(
            token_endpoint=oidc_config["token_endpoint"],
            code=code,
            redirect_uri=_build_redirect_uri(request),
            client_id=settings.keycloak_client_id,
            code_verifier=pkce_data["code_verifier"],
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Token exchange failed: {e}"
        ) from e

    # Validate ID token
    id_token = token_response.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No id_token in response"
        )

    jwks = await get_jwks(oidc_config["jwks_uri"])
    # The JWT iss claim uses the browser-facing keycloak_url, not the internal
    # URL used for server-side discovery. Derive expected issuer accordingly.
    expected_issuer = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
    try:
        claims = validate_id_token(id_token, jwks, expected_issuer, settings.keycloak_client_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID token: {e}"
        ) from e

    # Upsert user
    display_name = (
        f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
        or claims.get("preferred_username", claims["sub"])
    )
    await upsert_user(
        keycloak_sub=claims["sub"],
        email=claims.get("email", ""),
        display_name=display_name,
        username=claims.get("preferred_username", claims["sub"]),
        given_name=claims.get("given_name", ""),
        family_name=claims.get("family_name", ""),
        affiliation=claims.get("affiliation", ""),
        crd_name=claims.get("preferred_username", claims["sub"]),
        active=True,
        session=session,
    )

    # Build session cookie
    session_data = make_session_data(claims, ttl_seconds=settings.session_ttl_seconds)

    # Decode next URL from state
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state))
        next_url = state_data.get("next", "/ui/requests")
    except Exception:
        next_url = "/ui/requests"

    response = RedirectResponse(next_url, status_code=302)
    secure = not settings.dev_auth_bypass
    set_session_cookie(
        response,
        session_data,
        settings.secret_key,
        cookie_name=settings.session_cookie_name,
        max_age=settings.session_ttl_seconds,
        secure=secure,
    )
    # Clear PKCE cookie
    response.delete_cookie(key="trevor_pkce", path="/")
    return response


@router.get("/logout")
async def logout(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    """Clear session and redirect to Keycloak logout."""
    oidc_config = await fetch_openid_config(settings.keycloak_server_url, settings.keycloak_realm)
    end_session_endpoint = oidc_config.get("end_session_endpoint", "")

    post_logout_uri = str(request.base_url).rstrip("/") + "/ui/requests"

    if end_session_endpoint:
        params = {
            "post_logout_redirect_uri": post_logout_uri,
            "client_id": settings.keycloak_client_id,
        }
        logout_url = f"{end_session_endpoint}?{urllib.parse.urlencode(params)}"
    else:
        logout_url = post_logout_uri

    response = RedirectResponse(logout_url, status_code=302)
    clear_session_cookie(response, cookie_name=settings.session_cookie_name)
    return response
