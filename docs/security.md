# Security

## Input validation

All request bodies are validated by Pydantic v2 models before reaching business logic. FastAPI rejects malformed JSON and invalid enum values with `422 Unprocessable Entity`.

| Input type | Validation |
|---|---|
| Request bodies | Pydantic v2 schema validation |
| UUID path parameters | FastAPI `uuid.UUID` type annotation |
| Enum fields (`OutputType`, `AirlockDirection`, etc.) | Pydantic enum validation |
| File uploads | Size checked against `MAX_UPLOAD_SIZE_MB` (default 500 MB); SHA-256 computed at upload |
| Form fields | FastAPI `Form()` annotation; string fields unbounded except where noted |

### Upload size limit

`POST /requests/{id}/objects` and `POST /requests/{id}/objects/{oid}/replace` enforce `MAX_UPLOAD_SIZE_MB`. Oversized uploads receive `413 Request Entity Too Large`.

### CSRF protection

All state-mutating UI form POSTs (`POST /ui/...`) require a valid `csrf_token` hidden field. Tokens are signed with `itsdangerous` `URLSafeTimedSerializer` using `SECRET_KEY` and expire after 1 hour. API endpoints are CSRF-safe (JWT auth via `Authorization` header, not cookies).

### Rate limiting

`slowapi` per-IP rate limits applied to sensitive endpoints:

| Endpoint | Limit |
|---|---|
| `POST /requests/{id}/submit` | 10/minute |
| `POST /requests/{id}/resubmit` | 10/minute |
| `POST /memberships` | 30/minute |

### Role separation

- C-04: Submitter cannot review their own request. Researcher cannot be checker on same project.
- C-02: Researchers never receive S3 credentials. trevor proxies all storage access.
- `tre_admin` realm role verified on every request from JWT `realm_access.roles`. Not cached locally.

## Pen test checklist

| Category | Item | Status |
|---|---|---|
| Auth | JWT signature verified via Keycloak JWKS | Implemented |
| Auth | DEV_AUTH_BYPASS disabled in prod | Enforced by env |
| Auth | No local credential store | C-10 constraint |
| CSRF | Hidden token on all UI form POSTs | Implemented |
| CSRF | API endpoints CSRF-safe (header auth) | By design |
| Input | Pydantic validation on all request bodies | Implemented |
| Input | Upload size limit | Implemented |
| Input | UUID path param validation | Implemented |
| Audit | Append-only audit log | C-05 constraint |
| Audit | All state transitions emit AuditEvent | Implemented |
| Storage | No S3 credentials in API responses | C-02 constraint |
| Storage | SHA-256 checksum verified at state transitions | Implemented |
| Rate limiting | Sensitive endpoints rate limited | Implemented |
| Error handling | 403/404/500 error pages, no stack traces in HTML | Implemented |
| Headers | No sensitive data in response headers | Review before prod |
| TLS | Terminate at ingress/load balancer | Kubernetes infra |
| Secrets | SECRET_KEY, S3 creds from Kubernetes Secrets | Helm values |

## Production configuration

Required environment variables that must be changed from defaults:

| Variable | Default | Prod requirement |
|---|---|---|
| `SECRET_KEY` | `dev-secret-key-change-in-prod` | Strong random secret (32+ bytes) |
| `DEV_AUTH_BYPASS` | `false` | Must remain `false` |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | empty | Inject from Kubernetes Secret |
| `DATABASE_URL` | SQLite | `postgresql+asyncpg://...` |
