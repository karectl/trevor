# Iteration 12 Spec — OIDC Login/Logout Flow & JWT Validation

## Goal

Implement the full browser login/logout UX via Keycloak OIDC (Authorization Code Flow with PKCE), JWT validation for API Bearer tokens, and dual auth strategy (cookie for UI, Bearer for API). This completes the auth story prescribed by ADR-0007.

---

## Current state

| Component | Status |
|---|---|
| `DEV_AUTH_BYPASS` | Working — tests and local dev |
| Bearer token extraction | Working — `HTTPBearer` in `auth.py` |
| JWT validation via JWKS | `raise NotImplementedError` at `auth.py:70` |
| OIDC login redirect | Missing |
| OIDC callback (code → token exchange) | Missing |
| Session cookie management | Missing |
| Logout flow | Missing |
| Login/logout links in nav | Missing |
| 401 → login redirect for UI routes | Missing |

---

## Scope decisions

| Item | Decision | Rationale |
|---|---|---|
| Session store | Signed cookie (`itsdangerous`) | Stateless (C-08), already a dependency, ADR-0007 prescribes this |
| Dual auth | Cookie for `/ui/*`, Bearer for API | ADR-0007 prescribes both paths |
| PKCE | Required | Public client, no client secret — PKCE prevents auth code interception |
| Token refresh | Not in scope | Expired session → redirect to Keycloak re-auth. TRE sessions should be short-lived. |
| New ADR | Not needed | ADR-0007 already covers all decisions |

---

## 1. OIDC client module

### File: `src/trevor/oidc.py`

Pure functions and a thin httpx-based client for Keycloak OIDC.

#### OIDC discovery

```python
async def fetch_openid_config(keycloak_url: str, realm: str) -> dict:
    """Fetch .well-known/openid-configuration. Cached in-process."""
    url = f"{keycloak_url}/realms/{realm}/.well-known/openid-configuration"
    # httpx.AsyncClient GET, cache result
```

Returns `authorization_endpoint`, `token_endpoint`, `jwks_uri`, `end_session_endpoint`, `issuer`.

#### PKCE helpers

```python
def generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(96)  # 128 chars
    challenge = base64url(sha256(verifier))
    return verifier, challenge
```

#### Token exchange

```python
async def exchange_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
) -> dict:
    """Exchange authorization code for tokens. Returns token response dict."""
    # POST to token_endpoint with grant_type=authorization_code
```

#### JWKS fetch + cache

```python
_jwks_cache: dict = {}
_jwks_cache_time: float = 0

async def get_jwks(jwks_uri: str, cache_ttl: int = 3600) -> dict:
    """Fetch JWKS keys, cached for cache_ttl seconds."""
```

#### ID token validation

```python
def validate_id_token(
    token: str,
    jwks: dict,
    issuer: str,
    audience: str,
) -> dict:
    """Validate and decode an ID token. Returns claims dict.

    Validates: signature (RS256), iss, aud, exp.
    Extracts: sub, email, preferred_username, given_name, family_name,
              realm_access.roles.
    Raises ValueError on invalid token.
    """
```

Uses `python-jose` (`jose.jwt.decode`).

---

## 2. Session cookie module

### File: `src/trevor/session.py`

Stateless signed cookie using `itsdangerous.URLSafeTimedSerializer`.

#### Cookie payload

```python
@dataclass
class SessionData:
    sub: str              # Keycloak subject ID
    username: str         # preferred_username
    display_name: str     # given_name + family_name
    email: str
    realm_roles: list[str]
    exp: int              # Unix timestamp — session expiry
```

#### Functions

```python
def create_session_cookie(
    data: SessionData,
    secret_key: str,
) -> str:
    """Serialize and sign session data into a cookie value."""

def read_session_cookie(
    cookie_value: str,
    secret_key: str,
    max_age: int,
) -> SessionData | None:
    """Deserialize and validate session cookie. Returns None if invalid/expired."""

def clear_session_cookie(response: Response) -> Response:
    """Delete session cookie from response."""
```

Cookie attributes:
- Name: `trevor_session` (configurable via `SESSION_COOKIE_NAME`)
- `HttpOnly=True`
- `Secure=True` when not `DEV_AUTH_BYPASS` (HTTPS in prod)
- `SameSite=Lax`
- `Path=/`
- `Max-Age` = `SESSION_TTL_SECONDS`

---

## 3. Auth routes

### File: `src/trevor/routers/auth_routes.py`

Router prefix: `/auth`

### `GET /auth/login`

1. Generate PKCE `code_verifier` + `code_challenge`
2. Generate random `state` (includes original URL from `?next=` query param, base64-encoded)
3. Store `code_verifier` + `state` in a short-lived signed cookie (`trevor_pkce`, 5-min TTL, `HttpOnly`, `SameSite=Lax`)
4. Redirect (302) to Keycloak `authorization_endpoint` with:
   - `response_type=code`
   - `client_id=trevor`
   - `redirect_uri=http://localhost:8000/auth/callback` (built from request base URL)
   - `scope=openid email profile`
   - `state=<state>`
   - `code_challenge=<code_challenge>`
   - `code_challenge_method=S256`

### `GET /auth/callback`

1. Read `code` and `state` from query params
2. Read `trevor_pkce` cookie, validate signature, extract `code_verifier` and expected `state`
3. Verify `state` matches
4. Exchange `code` for tokens via `exchange_code()`
5. Validate ID token via `validate_id_token()`
6. Extract claims: `sub`, `email`, `preferred_username`, `given_name`, `family_name`, `realm_access.roles`
7. `upsert_user()` with extracted claims
8. Create `SessionData`, serialize to session cookie
9. Clear `trevor_pkce` cookie
10. Redirect to original URL (decoded from `state`) or `/ui/requests`

### `GET /auth/logout`

1. Clear `trevor_session` cookie
2. Redirect to Keycloak `end_session_endpoint` with:
   - `post_logout_redirect_uri=http://localhost:8000/ui/requests`
   - `client_id=trevor`

---

## 4. Updated auth dependency

### Changes to `src/trevor/auth.py`

Refactor `get_auth_context` to support three auth strategies in order:

```python
async def get_auth_context(...) -> AuthContext:
    # 1. DEV_AUTH_BYPASS (unchanged)
    if settings.dev_auth_bypass:
        ...

    # 2. Session cookie (for UI routes)
    session_cookie = request.cookies.get(settings.session_cookie_name)
    if session_cookie:
        data = read_session_cookie(session_cookie, settings.secret_key, settings.session_ttl_seconds)
        if data and data.exp > time.time():
            user = await upsert_user(...)
            return AuthContext(user=user, realm_roles=data.realm_roles, ...)
        # Cookie expired or invalid — fall through

    # 3. Bearer token (for API routes)
    if credentials:
        claims = await validate_bearer_token(credentials.credentials, settings)
        user = await upsert_user(...)
        return AuthContext(user=user, realm_roles=claims["realm_roles"], ...)

    # 4. No auth — raise
    raise HTTPException(status_code=401, ...)
```

#### New: `validate_bearer_token`

```python
async def validate_bearer_token(token: str, settings: Settings) -> dict:
    """Validate a Bearer JWT against Keycloak JWKS. Returns claims dict."""
    oidc_config = await fetch_openid_config(settings.keycloak_url, settings.keycloak_realm)
    jwks = await get_jwks(oidc_config["jwks_uri"])
    return validate_id_token(token, jwks, oidc_config["issuer"], settings.keycloak_client_id)
```

#### Dependency signature change

`get_auth_context` needs access to `Request` (for cookies). Add `request: Request` parameter:

```python
async def get_auth_context(
    request: Request,
    credentials: ... | None,
    settings: Settings,
    session: AsyncSession,
) -> AuthContext:
```

This is a **breaking change** for the dependency signature. All existing routes already have `Request` available, so no router changes needed — FastAPI resolves `Request` automatically in dependency chains.

---

## 5. Updated app.py

### 401 error handler

Add a 401 handler that redirects browser requests to `/auth/login`:

```python
@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: Exception):
    if _wants_html(request):
        login_url = f"/auth/login?next={request.url.path}"
        return RedirectResponse(login_url, status_code=302)
    detail = getattr(exc, "detail", "Unauthorized")
    return JSONResponse({"detail": detail}, status_code=401)
```

### Register auth router

```python
from trevor.routers import auth_routes
app.include_router(auth_routes.router)
```

---

## 6. Settings additions

### Changes to `src/trevor/settings.py`

```python
# Session
session_cookie_name: str = "trevor_session"
session_ttl_seconds: int = 3600  # 1 hour
```

No `keycloak_client_secret` needed — trevor is a public OIDC client (PKCE replaces client secret).

---

## 7. Template changes

### `templates/components/nav.html`

Add logout link:

```html
<span class="user-info">
    {{ user.display_name }}
    {% if is_admin %}<span class="badge badge-APPROVED">admin</span>{% endif %}
    <a href="/auth/logout" class="logout-link">Log out</a>
</span>
```

### `templates/errors/401.html` (new)

Standalone 401 page (does not extend `base.html` — error handlers have no auth context):

```html
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Log in — trevor</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body>
<main class="error-page">
  <h1>Authentication required</h1>
  <p>You need to log in to access this page.</p>
  <a href="/auth/login" class="btn">Log in with Keycloak</a>
</main>
</body>
</html>
```

---

## 8. Keycloak realm update

### `deploy/dev/keycloak-realm.yaml`

Verify the trevor client has `standardFlowEnabled: true` (required for Authorization Code Flow). The current config only has `publicClient: true` and `directAccessGrantsEnabled: true`. Add explicitly:

```json
{
  "clientId": "trevor",
  "enabled": true,
  "publicClient": true,
  "standardFlowEnabled": true,
  "redirectUris": ["http://localhost:8000/*"],
  "webOrigins": ["http://localhost:8000"],
  "directAccessGrantsEnabled": true
}
```

---

## 9. Test plan

### File: `tests/test_auth_flow.py`

Tests run with `DEV_AUTH_BYPASS=true` for the existing test suite (unchanged). New auth flow tests use a **separate app instance** with `DEV_AUTH_BYPASS=false` and mock the Keycloak endpoints.

#### Unit tests (no network)

| Test | Validates |
|---|---|
| `test_generate_pkce` | Verifier length, challenge is base64url(sha256(verifier)) |
| `test_session_cookie_roundtrip` | Create → read returns same SessionData |
| `test_session_cookie_expired` | read returns None after max_age |
| `test_session_cookie_tampered` | read returns None for modified cookie |
| `test_validate_id_token_valid` | Decodes with correct claims |
| `test_validate_id_token_expired` | Raises on expired token |
| `test_validate_id_token_bad_audience` | Raises on wrong audience |
| `test_validate_id_token_bad_signature` | Raises on bad signature |

#### Integration tests (mocked Keycloak)

| Test | Validates |
|---|---|
| `test_login_redirects_to_keycloak` | GET /auth/login returns 302 to Keycloak authorize endpoint, sets trevor_pkce cookie |
| `test_callback_exchanges_code` | GET /auth/callback with valid code+state sets trevor_session cookie, redirects to /ui/requests |
| `test_callback_invalid_state` | GET /auth/callback with wrong state returns 400 |
| `test_logout_clears_cookie` | GET /auth/logout clears trevor_session, redirects to Keycloak logout |
| `test_ui_route_without_cookie_redirects` | GET /ui/requests without cookie redirects to /auth/login |
| `test_ui_route_with_valid_cookie` | GET /ui/requests with valid session cookie returns 200 |
| `test_api_route_with_bearer_token` | GET /users/me with valid Bearer token returns 200 |
| `test_api_route_without_token_returns_401` | GET /users/me without token returns 401 JSON |
| `test_existing_tests_unaffected` | DEV_AUTH_BYPASS=true path unchanged |

#### Mocking strategy

Use `respx` or `httpx` mock transport to intercept:
- `GET {KEYCLOAK_URL}/realms/{REALM}/.well-known/openid-configuration` → return discovery doc
- `POST {KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token` → return token response
- `GET {KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs` → return JWKS

Generate test JWTs signed with a test RSA key pair.

---

## New / modified files

```
src/trevor/
  oidc.py                              # NEW — OIDC discovery, PKCE, token exchange, JWKS, ID token validation
  session.py                           # NEW — session cookie create/read/clear
  routers/auth_routes.py               # NEW — /auth/login, /auth/callback, /auth/logout
  auth.py                              # MODIFIED — dual auth (cookie + Bearer + dev bypass), JWT validation
  settings.py                          # MODIFIED — session_cookie_name, session_ttl_seconds
  app.py                               # MODIFIED — register auth router, 401 handler with login redirect
  templates/
    components/nav.html                # MODIFIED — logout link
    errors/401.html                    # NEW — standalone login page
tests/
  test_auth_flow.py                    # NEW — OIDC flow + session + JWT tests
deploy/dev/
  keycloak-realm.yaml                  # MODIFIED — add standardFlowEnabled: true
```

---

## Implementation order

1. `oidc.py` — OIDC discovery, PKCE helpers, token exchange, JWKS cache, ID token validation
2. `session.py` — cookie create/read/clear
3. `settings.py` — new session settings
4. `auth.py` — refactor for dual auth, implement `validate_bearer_token`
5. `routers/auth_routes.py` — login/callback/logout
6. `app.py` — register auth router, 401 handler
7. `templates/errors/401.html` — standalone 401 page
8. `templates/components/nav.html` — logout link
9. `deploy/dev/keycloak-realm.yaml` — add `standardFlowEnabled`
10. `tests/test_auth_flow.py` — unit + integration tests
11. Run lint + format + full test suite
