"""Session cookie management using itsdangerous signed cookies.

Stateless session — all session data serialized into a signed cookie.
No server-side session store (C-08 compliance).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import Response


@dataclass
class SessionData:
    """Payload stored in the session cookie."""

    sub: str  # Keycloak subject ID
    username: str  # preferred_username
    display_name: str  # given_name + family_name
    email: str
    realm_roles: list[str]
    exp: int  # Unix timestamp — session expiry
    id_token: str = ""  # Raw ID token — passed as id_token_hint on logout


def create_session_cookie(
    data: SessionData,
    secret_key: str,
    salt: str = "session",
) -> str:
    """Serialize and sign session data into a cookie value."""
    s = URLSafeTimedSerializer(secret_key, salt=salt)
    return s.dumps(json.dumps(asdict(data)))


def read_session_cookie(
    cookie_value: str,
    secret_key: str,
    max_age: int,
    salt: str = "session",
) -> SessionData | None:
    """Deserialize and validate session cookie. Returns None if invalid/expired."""
    s = URLSafeTimedSerializer(secret_key, salt=salt)
    try:
        raw = s.loads(cookie_value, max_age=max_age)
        d = json.loads(raw)
        return SessionData(**d)
    except (BadSignature, SignatureExpired, json.JSONDecodeError, TypeError, KeyError):
        return None


def set_session_cookie(
    response: Response,
    data: SessionData,
    secret_key: str,
    cookie_name: str = "trevor_session",
    max_age: int = 3600,
    secure: bool = True,
) -> Response:
    """Set session cookie on response."""
    value = create_session_cookie(data, secret_key)
    response.set_cookie(
        key=cookie_name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


def clear_session_cookie(
    response: Response,
    cookie_name: str = "trevor_session",
) -> Response:
    """Delete session cookie from response."""
    response.delete_cookie(key=cookie_name, path="/")
    return response


def create_pkce_cookie(
    code_verifier: str,
    state: str,
    secret_key: str,
    salt: str = "pkce",
) -> str:
    """Create a signed short-lived cookie for PKCE verifier + state."""
    s = URLSafeTimedSerializer(secret_key, salt=salt)
    return s.dumps(json.dumps({"code_verifier": code_verifier, "state": state}))


def read_pkce_cookie(
    cookie_value: str,
    secret_key: str,
    max_age: int = 300,
    salt: str = "pkce",
) -> dict | None:
    """Read PKCE cookie. Returns dict with code_verifier and state, or None."""
    s = URLSafeTimedSerializer(secret_key, salt=salt)
    try:
        raw = s.loads(cookie_value, max_age=max_age)
        return json.loads(raw)
    except (BadSignature, SignatureExpired, json.JSONDecodeError):
        return None


def make_session_data(
    claims: dict,
    ttl_seconds: int = 3600,
    id_token: str = "",
) -> SessionData:
    """Build SessionData from ID token claims."""
    return SessionData(
        sub=claims["sub"],
        username=claims.get("preferred_username", claims["sub"]),
        display_name=f"{claims.get('given_name', '')} {claims.get('family_name', '')}".strip()
        or claims.get("preferred_username", claims["sub"]),
        email=claims.get("email", ""),
        realm_roles=claims.get("realm_roles", []),
        exp=int(time.time()) + ttl_seconds,
        id_token=id_token,
    )
