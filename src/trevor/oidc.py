"""OIDC client — Keycloak discovery, PKCE, token exchange, JWKS, ID token validation."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time

import httpx
from jose import jwt

# In-process caches
_oidc_config_cache: dict[str, dict] = {}
_jwks_cache: dict[str, dict] = {}
_jwks_cache_time: dict[str, float] = {}


async def fetch_openid_config(keycloak_url: str, realm: str) -> dict:
    """Fetch .well-known/openid-configuration. Cached in-process."""
    cache_key = f"{keycloak_url}/{realm}"
    if cache_key in _oidc_config_cache:
        return _oidc_config_cache[cache_key]

    url = f"{keycloak_url}/realms/{realm}/.well-known/openid-configuration"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        config = resp.json()

    _oidc_config_cache[cache_key] = config
    return config


def clear_oidc_caches() -> None:
    """Clear all in-process OIDC caches. Useful for tests."""
    _oidc_config_cache.clear()
    _jwks_cache.clear()
    _jwks_cache_time.clear()


def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def exchange_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
) -> dict:
    """Exchange authorization code for tokens. Returns token response dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


async def get_jwks(jwks_uri: str, cache_ttl: int = 3600) -> dict:
    """Fetch JWKS keys, cached for cache_ttl seconds."""
    now = time.monotonic()
    if jwks_uri in _jwks_cache and (now - _jwks_cache_time.get(jwks_uri, 0)) < cache_ttl:
        return _jwks_cache[jwks_uri]

    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_uri, timeout=10)
        resp.raise_for_status()
        jwks = resp.json()

    _jwks_cache[jwks_uri] = jwks
    _jwks_cache_time[jwks_uri] = now
    return jwks


def validate_id_token(
    token: str,
    jwks: dict,
    issuer: str,
    audience: str,
) -> dict:
    """Validate and decode an ID/access token. Returns claims dict.

    Validates: signature (RS256), iss, aud, exp.
    Extracts: sub, email, preferred_username, given_name, family_name,
              realm_access.roles.
    Raises ValueError on invalid token.
    """
    try:
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except Exception as e:
        raise ValueError(f"Invalid token: {e}") from e

    # Extract realm roles
    realm_access = claims.get("realm_access", {})
    claims["realm_roles"] = realm_access.get("roles", [])
    return claims
