"""Tests for OIDC login/logout flow, session cookies, and JWT validation."""

from __future__ import annotations

import base64
import hashlib
import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import serialization

# ── Test RSA key pair for JWT signing ──
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from trevor.app import create_app
from trevor.database import get_session
from trevor.oidc import generate_pkce, validate_id_token
from trevor.session import (
    SessionData,
    create_pkce_cookie,
    create_session_cookie,
    make_session_data,
    read_pkce_cookie,
    read_session_cookie,
)
from trevor.settings import Settings, get_settings

_test_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_test_private_pem = _test_private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_test_public_key = _test_private_key.public_key()
_test_public_numbers = _test_public_key.public_numbers()


def _int_to_base64url(n: int, length: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


_test_jwks = {
    "keys": [
        {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": "test-kid",
            "n": _int_to_base64url(_test_public_numbers.n, 256),
            "e": _int_to_base64url(_test_public_numbers.e, 3),
        }
    ]
}

TEST_ISSUER = "http://keycloak:8080/realms/karectl"
TEST_AUDIENCE = "trevor"
TEST_SECRET = "test-secret-key-for-cookies"
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


def _make_test_jwt(
    sub: str = "test-sub",
    exp: int | None = None,
    aud: str = TEST_AUDIENCE,
    iss: str = TEST_ISSUER,
    **extra_claims: object,
) -> str:
    claims = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "exp": exp or int(time.time()) + 3600,
        "iat": int(time.time()),
        "preferred_username": "testuser",
        "email": "test@example.com",
        "given_name": "Test",
        "family_name": "User",
        "realm_access": {"roles": ["researcher"]},
        **extra_claims,
    }
    return jose_jwt.encode(
        claims, _test_private_pem, algorithm="RS256", headers={"kid": "test-kid"}
    )


# ── Unit tests: PKCE ──


def test_generate_pkce():
    verifier, challenge = generate_pkce()
    assert len(verifier) > 40
    # Verify challenge = base64url(sha256(verifier))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_generate_pkce_unique():
    v1, _ = generate_pkce()
    v2, _ = generate_pkce()
    assert v1 != v2


# ── Unit tests: session cookie ──


def test_session_cookie_roundtrip():
    data = SessionData(
        sub="sub-1",
        username="alice",
        display_name="Alice User",
        email="alice@example.com",
        realm_roles=["researcher"],
        exp=int(time.time()) + 3600,
    )
    cookie = create_session_cookie(data, TEST_SECRET)
    result = read_session_cookie(cookie, TEST_SECRET, max_age=3600)
    assert result is not None
    assert result.sub == "sub-1"
    assert result.username == "alice"
    assert result.realm_roles == ["researcher"]


def test_session_cookie_tampered():
    data = SessionData(
        sub="sub-1",
        username="alice",
        display_name="Alice User",
        email="alice@example.com",
        realm_roles=["researcher"],
        exp=int(time.time()) + 3600,
    )
    cookie = create_session_cookie(data, TEST_SECRET)
    result = read_session_cookie(cookie + "tampered", TEST_SECRET, max_age=3600)
    assert result is None


def test_session_cookie_wrong_key():
    data = SessionData(
        sub="sub-1",
        username="alice",
        display_name="Alice User",
        email="alice@example.com",
        realm_roles=["researcher"],
        exp=int(time.time()) + 3600,
    )
    cookie = create_session_cookie(data, TEST_SECRET)
    result = read_session_cookie(cookie, "wrong-key", max_age=3600)
    assert result is None


# ── Unit tests: PKCE cookie ──


def test_pkce_cookie_roundtrip():
    cookie = create_pkce_cookie("verifier-123", "state-abc", TEST_SECRET)
    result = read_pkce_cookie(cookie, TEST_SECRET)
    assert result is not None
    assert result["code_verifier"] == "verifier-123"
    assert result["state"] == "state-abc"


# ── Unit tests: make_session_data ──


def test_make_session_data():
    claims = {
        "sub": "sub-1",
        "preferred_username": "alice",
        "given_name": "Alice",
        "family_name": "User",
        "email": "alice@example.com",
        "realm_roles": ["researcher"],
    }
    data = make_session_data(claims, ttl_seconds=600)
    assert data.sub == "sub-1"
    assert data.display_name == "Alice User"
    assert data.exp > time.time()


# ── Unit tests: JWT validation ──


def test_validate_id_token_valid():
    token = _make_test_jwt()
    claims = validate_id_token(token, _test_jwks, TEST_ISSUER, TEST_AUDIENCE)
    assert claims["sub"] == "test-sub"
    assert "realm_roles" in claims
    assert "researcher" in claims["realm_roles"]


def test_validate_id_token_expired():
    token = _make_test_jwt(exp=int(time.time()) - 3600)
    with pytest.raises(ValueError, match="Invalid token"):
        validate_id_token(token, _test_jwks, TEST_ISSUER, TEST_AUDIENCE)


def test_validate_id_token_bad_audience():
    token = _make_test_jwt(aud="wrong-client")
    with pytest.raises(ValueError, match="Invalid token"):
        validate_id_token(token, _test_jwks, TEST_ISSUER, TEST_AUDIENCE)


def test_validate_id_token_bad_issuer():
    token = _make_test_jwt(iss="http://evil.com")
    with pytest.raises(ValueError, match="Invalid token"):
        validate_id_token(token, _test_jwks, TEST_ISSUER, TEST_AUDIENCE)


def test_validate_id_token_bad_signature():
    # Sign with different key
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    claims = {
        "sub": "test-sub",
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    token = jose_jwt.encode(claims, other_pem, algorithm="RS256", headers={"kid": "test-kid"})
    with pytest.raises(ValueError, match="Invalid token"):
        validate_id_token(token, _test_jwks, TEST_ISSUER, TEST_AUDIENCE)


# ── Integration test fixtures ──

_FAKE_OIDC_CONFIG = {
    "authorization_endpoint": "http://keycloak:8080/realms/karectl/protocol/openid-connect/auth",
    "token_endpoint": "http://keycloak:8080/realms/karectl/protocol/openid-connect/token",
    "jwks_uri": "http://keycloak:8080/realms/karectl/protocol/openid-connect/certs",
    "end_session_endpoint": "http://keycloak:8080/realms/karectl/protocol/openid-connect/logout",
    "issuer": TEST_ISSUER,
}


@pytest.fixture
def oidc_settings() -> Settings:
    return Settings(
        dev_auth_bypass=False,
        database_url=TEST_DB_URL,
        keycloak_url="http://keycloak:8080",
        keycloak_realm="karectl",
        keycloak_client_id="trevor",
        secret_key=TEST_SECRET,
        session_ttl_seconds=3600,
    )


@pytest.fixture
async def oidc_engine():
    eng = create_async_engine(TEST_DB_URL, echo=False, future=True)
    import trevor.models  # noqa: F401

    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
async def oidc_client(oidc_settings, oidc_engine):
    factory = async_sessionmaker(bind=oidc_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override():
        async with factory() as session:
            yield session

    app = create_app(oidc_settings)
    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[get_settings] = lambda: oidc_settings

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as ac:
        yield ac


# ── Integration tests ──


@pytest.mark.asyncio
async def test_login_redirects_to_keycloak(oidc_client):
    with patch(
        "trevor.routers.auth_routes.fetch_openid_config", new_callable=AsyncMock
    ) as mock_oidc:
        mock_oidc.return_value = _FAKE_OIDC_CONFIG
        resp = await oidc_client.get("/auth/login")

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "keycloak:8080" in location
    assert "code_challenge" in location
    assert "code_challenge_method=S256" in location
    # PKCE cookie set
    assert "trevor_pkce" in resp.cookies


@pytest.mark.asyncio
async def test_login_preserves_next_param(oidc_client):
    with patch(
        "trevor.routers.auth_routes.fetch_openid_config", new_callable=AsyncMock
    ) as mock_oidc:
        mock_oidc.return_value = _FAKE_OIDC_CONFIG
        resp = await oidc_client.get("/auth/login?next=/ui/admin")

    assert resp.status_code == 302
    location = resp.headers["location"]
    # state param should contain encoded next URL
    assert "state=" in location


@pytest.mark.asyncio
async def test_callback_exchanges_code(oidc_client, oidc_settings):
    # First do a login to get PKCE cookie
    with patch(
        "trevor.routers.auth_routes.fetch_openid_config", new_callable=AsyncMock
    ) as mock_oidc:
        mock_oidc.return_value = _FAKE_OIDC_CONFIG
        login_resp = await oidc_client.get("/auth/login")

    pkce_cookie = login_resp.cookies.get("trevor_pkce")
    # Extract state from redirect URL
    import urllib.parse

    location = login_resp.headers["location"]
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params["state"][0]

    # Mock token exchange and JWKS
    test_token = _make_test_jwt()
    token_response = {"id_token": test_token, "access_token": "at-123", "token_type": "Bearer"}

    with (
        patch(
            "trevor.routers.auth_routes.fetch_openid_config", new_callable=AsyncMock
        ) as mock_oidc,
        patch("trevor.routers.auth_routes.exchange_code", new_callable=AsyncMock) as mock_exchange,
        patch("trevor.routers.auth_routes.get_jwks", new_callable=AsyncMock) as mock_jwks,
    ):
        mock_oidc.return_value = _FAKE_OIDC_CONFIG
        mock_exchange.return_value = token_response
        mock_jwks.return_value = _test_jwks

        resp = await oidc_client.get(
            f"/auth/callback?code=test-auth-code&state={state}",
            cookies={"trevor_pkce": pkce_cookie},
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/requests"
    assert "trevor_session" in resp.cookies


@pytest.mark.asyncio
async def test_callback_invalid_state(oidc_client, oidc_settings):
    # Create a PKCE cookie with one state
    pkce_cookie = create_pkce_cookie("verifier", "state-good", oidc_settings.secret_key)

    with patch("trevor.routers.auth_routes.fetch_openid_config", new_callable=AsyncMock):
        resp = await oidc_client.get(
            "/auth/callback?code=test-code&state=state-bad",
            cookies={"trevor_pkce": pkce_cookie},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_missing_code(oidc_client):
    resp = await oidc_client.get("/auth/callback")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_logout_clears_cookie(oidc_client):
    with patch(
        "trevor.routers.auth_routes.fetch_openid_config", new_callable=AsyncMock
    ) as mock_oidc:
        mock_oidc.return_value = _FAKE_OIDC_CONFIG
        resp = await oidc_client.get("/auth/logout")

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "keycloak:8080" in location
    assert "post_logout_redirect_uri" in location


@pytest.mark.asyncio
async def test_api_route_without_token_returns_401(oidc_client):
    resp = await oidc_client.get("/users/me")
    # API request (no Accept: text/html) should get 401 JSON
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


@pytest.mark.asyncio
async def test_api_route_with_valid_bearer(oidc_client):
    test_token = _make_test_jwt()

    with (
        patch("trevor.auth.fetch_openid_config", new_callable=AsyncMock) as mock_oidc,
        patch("trevor.auth.get_jwks", new_callable=AsyncMock) as mock_jwks,
    ):
        mock_oidc.return_value = _FAKE_OIDC_CONFIG
        mock_jwks.return_value = _test_jwks

        resp = await oidc_client.get(
            "/users/me",
            headers={"Authorization": f"Bearer {test_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "testuser"


@pytest.mark.asyncio
async def test_ui_route_without_cookie_redirects(oidc_client):
    resp = await oidc_client.get(
        "/ui/requests",
        headers={"Accept": "text/html"},
    )
    # 401 handler redirects HTML requests to /auth/login
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_ui_route_with_valid_session_cookie(oidc_client, oidc_settings):
    session_data = SessionData(
        sub="test-sub",
        username="testuser",
        display_name="Test User",
        email="test@example.com",
        realm_roles=["researcher"],
        exp=int(time.time()) + 3600,
    )
    cookie = create_session_cookie(session_data, oidc_settings.secret_key)

    resp = await oidc_client.get(
        "/ui/requests",
        cookies={"trevor_session": cookie},
        headers={"Accept": "text/html"},
    )
    assert resp.status_code == 200
