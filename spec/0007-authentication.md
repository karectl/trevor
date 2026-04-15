# ADR-0007 — Authentication and Authorisation: Keycloak OIDC + Local RBAC

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

trevor is a multi-user application with distinct roles. It runs within the karectl platform, which already operates a Keycloak instance as the identity provider for all services. trevor must integrate with this existing IdP rather than introducing its own credential store.

The authorisation model is project-scoped: a user's role in trevor is specific to a project, not global (except for `tre_admin`).

---

## Decision

### Authentication: Keycloak OIDC

trevor uses Keycloak as its sole identity provider via OIDC/OAuth2.

- **Browser sessions**: Authorization Code Flow with PKCE. trevor redirects unauthenticated users to Keycloak login. On callback, trevor validates the ID token and issues a session cookie.
- **API access** (for future CLI/programmatic use): Bearer token (access token) validated against Keycloak's JWKS endpoint.
- **Token validation**: `python-jose` or `authlib` for JWT validation. JWKS cached with configurable TTL to avoid hammering Keycloak.

trevor does not store passwords. It maintains a `User` table as a shadow record for audit FK integrity — populated/updated on first login and token refresh from JWT claims (`sub`, `email`, `preferred_username`).

### Authorisation: Local RBAC with project scope

trevor maintains its own `ProjectMembership` table (see DOMAIN_MODEL.md). This is the authorisation source of truth for project-scoped roles.

Global roles (`tre_admin`) are carried in the Keycloak JWT as a realm role claim (`realm_access.roles`). trevor reads this claim on each request and does not cache it locally (so Keycloak role changes take effect on next token refresh).

Project-scoped roles (`researcher`, `output_checker`, `senior_checker`) are managed in trevor's own DB by `tre_admin` users. They are NOT derived from Keycloak groups (to avoid coupling trevor's RBAC to the karectl-wide group structure).

### Role assignment rules

| Rule | Enforcement |
|------|-------------|
| A user may not hold `output_checker` or `senior_checker` on a project where they hold `researcher` | DB constraint + API validation |
| Checker assignment to a project must be performed by `tre_admin` | API endpoint requires `tre_admin` role |
| A user may hold roles on multiple projects simultaneously | No restriction |
| Removing a checker from a project does not invalidate their past reviews | Reviews are immutable; membership removal only prevents future review |

### Session management

- Server-side sessions using `itsdangerous` signed cookies (no server-side session store required — session data is minimal: user ID + CSRF token).
- Session lifetime matches Keycloak access token TTL. trevor checks token expiry on each request and triggers silent refresh or re-login as needed.
- CSRF protection on all state-changing endpoints (htmx/Datastar requests include `X-CSRFToken` header).

### FastAPI dependency injection

```python
# Injected into route handlers
async def current_user(request: Request, session: AsyncSession) -> User: ...
async def require_role(role: Role) -> Callable: ...
async def require_project_role(project_id: UUID, role: Role) -> Callable: ...
```

---

## Consequences

- **Positive**: No credential management in trevor. SSO across all karectl services.
- **Positive**: Keycloak provides MFA, account lockout, audit logs for authentication events.
- **Positive**: Project-scoped RBAC in trevor's DB is independent of karectl-wide group management — less coupling.
- **Negative**: trevor is non-functional without the Keycloak instance. Local development requires a Keycloak container (included in the dev `docker-compose.yml` / Tilt setup).
- **Mitigation**: Provide a `DEV_AUTH_BYPASS` environment variable for automated testing only (not available in production Helm chart values).
