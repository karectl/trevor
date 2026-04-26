# Iteration 19 Spec — Production Readiness

## Goal

Harden trevor for production deployment: verify PostgreSQL migration path, tighten Helm chart defaults, improve CI pipeline security, and produce operational documentation (runbook + security checklist).

---

## Current state

| Component | Status |
|---|---|
| Alembic migrations | Implemented; tested against SQLite only |
| Helm chart (`helm/trevor/`) | 105-line `values.yaml` with dev-appropriate defaults |
| CI pipeline (`.github/workflows/ci.yml`) | lint → test → Docker build; no PG, no Helm lint, no image scan |
| Dockerfile | Multi-stage, non-root user |
| Telemetry (`src/trevor/telemetry.py`) | OTLP tracing when `OTEL_ENABLED=true` |
| Structured logging (`src/trevor/logging_config.py`) | JSON logging implemented |
| Test suite | 160+ tests, all in-memory SQLite |
| Runbook | Missing |
| Security checklist | Missing |
| `values.production.yaml` | Missing |
| GitHub Actions SHA pinning | Not done (uses `@v4`/`@v5`/`@v6` tags) |
| Trivy image scan | Not configured |
| Helm lint in CI | Not configured |
| PostgreSQL CI job | Not configured |

---

## Scope

| Item | Decision |
|---|---|
| PostgreSQL migration testing | Add CI job + optional test fixture |
| Helm values hardening | Production overlay + lint + chart tests |
| CI improvements | PG job, Helm lint, Trivy scan, SHA pinning |
| Runbook | `docs/runbook.md` |
| Security checklist | `docs/security-checklist.md` |
| New application code | None — this iteration is CI/config/docs only |

---

## 1. PostgreSQL migration testing

### 1a. CI job with PostgreSQL service container

Add a new job `test-postgres` to `.github/workflows/ci.yml`:

```yaml
test-postgres:
  name: Test (PostgreSQL)
  runs-on: ubuntu-latest
  needs: lint-test
  services:
    postgres:
      image: postgres:16
      env:
        POSTGRES_USER: trevor
        POSTGRES_PASSWORD: trevor
        POSTGRES_DB: trevor_test
      ports:
        - 5432:5432
      options: >-
        --health-cmd pg_isready
        --health-interval 10s
        --health-timeout 5s
        --health-retries 5
  steps:
    - uses: actions/checkout@<SHA>
    - name: Install uv
      uses: astral-sh/setup-uv@<SHA>
      with:
        version: "0.11.2"
    - name: Set up Python
      uses: actions/setup-python@<SHA>
      with:
        python-version-file: ".python-version"
    - name: Install dependencies
      run: uv sync --frozen
    - name: Run Alembic migrations
      env:
        DATABASE_URL: "postgresql+asyncpg://trevor:trevor@localhost:5432/trevor_test"
      run: uv run alembic upgrade head
    - name: Run tests against PostgreSQL
      env:
        TEST_DATABASE_URL: "postgresql+asyncpg://trevor:trevor@localhost:5432/trevor_test"
        DEV_AUTH_BYPASS: "true"
      run: uv run pytest -v
```

Pin all action references to full commit SHAs (see section 5).

### 1b. Fix SQLite-specific SQL in migrations

Audit all files in `alembic/versions/` for:

- `batch_alter_table` — required for SQLite but causes issues on PostgreSQL. Use conditional logic:
  ```python
  from alembic import op, context

  def upgrade():
      if context.get_context().dialect.name == "sqlite":
          with op.batch_alter_table("...") as batch_op:
              ...
      else:
          op.alter_column(...)
  ```
- SQLite-specific type mappings (e.g. `sa.JSON` stored as text)
- Any raw SQL strings with SQLite syntax

### 1c. Optional PostgreSQL test fixture

Modify `tests/conftest.py` to read `TEST_DATABASE_URL` from environment:

```python
import os

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

@pytest.fixture
async def engine():
    if TEST_DATABASE_URL:
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        yield engine
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
        await engine.dispose()
    else:
        # existing in-memory SQLite path
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        ...
```

When `TEST_DATABASE_URL` is set, each test gets a clean schema via `create_all` / `drop_all`. This is slower than in-memory SQLite but validates real PostgreSQL behavior.

### 1d. Verify all 160+ tests pass

All existing tests must pass against PostgreSQL without modification (apart from any SQLite-specific assertions, which should be made dialect-aware).

---

## 2. Helm values hardening

### 2a. Review defaults in `values.yaml`

Current defaults are already reasonable. Verify and annotate:

| Setting | Current | Action |
|---|---|---|
| `replicaCount` | 2 | Keep — good HA default |
| `resources.requests.cpu` | 250m | Keep |
| `resources.requests.memory` | 256Mi | Keep |
| `resources.limits.cpu` | 1000m | Keep |
| `resources.limits.memory` | 512Mi | Keep |
| `autoscaling.enabled` | false | Keep — opt-in for prod |
| `autoscaling.targetCPUUtilizationPercentage` | 70 | Keep |
| `worker.replicaCount` | 1 | Keep — scales per deployment |
| `networkPolicy.enabled` | false | Keep — opt-in |
| `migrations.hookEnabled` | true | Keep — correct for prod |

No changes needed to `values.yaml` defaults.

### 2b. Add `values.production.yaml`

Create `helm/trevor/values.production.yaml` with production-recommended overrides:

```yaml
# Production overrides — use with: helm upgrade -f values.production.yaml
replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

resources:
  requests:
    cpu: "500m"
    memory: "512Mi"
  limits:
    cpu: "2000m"
    memory: "1Gi"

worker:
  replicaCount: 2
  resources:
    requests:
      cpu: "500m"
      memory: "512Mi"
    limits:
      cpu: "2000m"
      memory: "1Gi"

env:
  LOG_LEVEL: "INFO"
  LOG_FORMAT: "json"
  DEV_AUTH_BYPASS: "false"
  OTEL_ENABLED: "true"
  OTEL_SERVICE_NAME: "trevor"
  MAX_UPLOAD_SIZE_MB: "500"
  STUCK_REQUEST_HOURS: "48"

networkPolicy:
  enabled: true

migrations:
  enabled: false
  hookEnabled: true

podDisruptionBudget:
  enabled: true
  minAvailable: 2
```

### 2c. Add PodDisruptionBudget support

If not already templated, add `templates/pdb.yaml`:

```yaml
{{- if .Values.podDisruptionBudget.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "trevor.fullname" . }}
spec:
  minAvailable: {{ .Values.podDisruptionBudget.minAvailable }}
  selector:
    matchLabels:
      {{- include "trevor.selectorLabels" . | nindent 6 }}
{{- end }}
```

Add defaults to `values.yaml`:

```yaml
podDisruptionBudget:
  enabled: false
  minAvailable: 1
```

### 2d. Helm lint and template validation

```bash
helm lint helm/trevor/
helm template trevor helm/trevor/ > /dev/null
helm template trevor helm/trevor/ -f helm/trevor/values.production.yaml > /dev/null
```

All three must pass without errors or warnings.

### 2e. Helm chart test

Add `helm/trevor/templates/tests/test-connection.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "trevor.fullname" . }}-test-connection"
  labels:
    {{- include "trevor.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
spec:
  containers:
    - name: wget
      image: busybox
      command: ['wget']
      args: ['{{ include "trevor.fullname" . }}:{{ .Values.service.port }}/health']
  restartPolicy: Never
```

---

## 3. CI improvements

### 3a. Pin all GitHub Actions to SHA

Replace tag references with full commit SHAs. Look up the current SHA for each:

| Action | Tag | Pin to SHA |
|---|---|---|
| `actions/checkout` | `v4` | `<lookup current SHA>` |
| `astral-sh/setup-uv` | `v5` | `<lookup current SHA>` |
| `actions/setup-python` | `v5` | `<lookup current SHA>` |
| `docker/setup-buildx-action` | `v3` | `<lookup current SHA>` |
| `docker/build-push-action` | `v6` | `<lookup current SHA>` |

Add a comment with the tag for readability:

```yaml
- uses: actions/checkout@abc123def456  # v4
```

### 3b. Add Helm lint job

```yaml
helm-lint:
  name: Helm Lint
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@<SHA>
    - name: Helm lint
      run: |
        helm lint helm/trevor/
        helm template trevor helm/trevor/ > /dev/null
        helm template trevor helm/trevor/ -f helm/trevor/values.production.yaml > /dev/null
```

### 3c. Add Trivy image scan

Add to the existing `build` job, after the Docker build step:

```yaml
- name: Scan image with Trivy
  uses: aquasecurity/trivy-action@<SHA>  # v0.28.0
  with:
    image-ref: trevor:ci
    format: table
    exit-code: 1
    severity: CRITICAL,HIGH
    ignore-unfixed: true
```

### 3d. Add PostgreSQL migration test job

See section 1a above. This runs as a separate job `test-postgres` that depends on `lint-test`.

### 3e. Final CI job graph

```
lint-test ──┬── build (Docker + Trivy)
            ├── test-postgres
            └── helm-lint (independent, no dependency)
```

`helm-lint` has no dependency on `lint-test` (runs in parallel).

---

## 4. Runbook

### File: `docs/runbook.md`

Structure:

```markdown
# trevor Runbook

## Deployment

### Helm upgrade procedure
- Pre-flight: `helm diff upgrade` to review changes
- Run: `helm upgrade trevor helm/trevor/ -f values.production.yaml -n trevor`
- Verify: `kubectl rollout status deployment/trevor -n trevor`
- Alembic migration runs automatically via pre-upgrade hook Job

### Rollback
- `helm rollback trevor <revision> -n trevor`
- Database rollback: `alembic downgrade -1` (manual, from a pod or Job)

## Database migration

### Running migrations manually
- `kubectl exec -it deploy/trevor -n trevor -- alembic upgrade head`
- Verify: `alembic current`

### Creating new migrations
- `uv run alembic revision --autogenerate -m "description"`
- Test locally against SQLite and PostgreSQL before committing

## Common failure modes

### Stuck requests (AGENT_REVIEW > 72h)
- **Symptom**: `/admin/metrics` shows stuck count > 0
- **Cause**: ARQ worker crashed, Redis unavailable, or LLM endpoint down
- **Resolution**: Check worker logs, verify Redis connectivity, check `AGENT_OPENAI_BASE_URL` reachability. Restart worker: `kubectl rollout restart deployment/trevor-worker -n trevor`

### S3 unavailable
- **Symptom**: Upload/download 500 errors, logs show `botocore` connection errors
- **Cause**: MinIO/S3 endpoint unreachable or credentials expired
- **Resolution**: Verify `S3_ENDPOINT_URL` reachability, rotate credentials in K8s secret if expired, restart pods to pick up new secrets

### Keycloak down
- **Symptom**: All authenticated requests return 401/503
- **Cause**: Keycloak pod down or JWKS endpoint unreachable
- **Resolution**: Check Keycloak deployment health. trevor caches JWKS keys briefly; short outages may not be user-visible.

### Redis down
- **Symptom**: Submit returns 500, agent reviews not processing
- **Cause**: Redis pod down or OOM-killed
- **Resolution**: Check Redis pod status, verify `REDIS_URL`. Requests already in SUBMITTED state will be picked up when Redis recovers.

## Monitoring

### Key metrics/logs to watch
- **HTTP 5xx rate**: alert if > 1% of requests
- **Request pipeline duration**: time from SUBMITTED to RELEASED
- **Stuck request count**: `/admin/metrics` endpoint or structured log `stuck_request_count`
- **ARQ job failures**: worker logs with `job_failed` events
- **Pod restarts**: OOM kills indicate memory limit too low
- **Alembic migration Job**: check Job status after each Helm upgrade

### OpenTelemetry
- Enable via `OTEL_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT`
- Traces cover: HTTP requests, DB queries, S3 operations, ARQ jobs

## Backup and restore

### Database
- PostgreSQL: use `pg_dump` / `pg_restore` via CronJob or managed DB snapshots
- RPO target: define per deployment (recommend ≤ 1 hour for prod)
- Test restore procedure quarterly

### S3 buckets
- Enable versioning on `trevor-quarantine` and `trevor-release` buckets
- Cross-region replication recommended for DR

## Scaling

### Horizontal Pod Autoscaler
- Enable in `values.production.yaml`: `autoscaling.enabled: true`
- Scales on CPU utilization (default 70%)
- `minReplicas: 3`, `maxReplicas: 10`

### Worker scaling
- Increase `worker.replicaCount` for higher agent review throughput
- Each worker instance is safe to run in parallel (ARQ handles deduplication)
- Monitor Redis queue depth to determine when to scale
```

---

## 5. Security checklist

### File: `docs/security-checklist.md`

```markdown
# trevor Security Checklist

## Authentication & Authorization
- [x] Keycloak OIDC for all authenticated endpoints (C-10)
- [x] JWT validation with JWKS key rotation
- [x] Role-based access: researcher, output_checker, senior_checker, tre_admin
- [x] Role conflict enforcement: researcher cannot check own project (C-04)
- [x] DEV_AUTH_BYPASS disabled in production (`DEV_AUTH_BYPASS=false`)

## Input validation
- [x] Pydantic v2 schema validation on all request bodies
- [x] File upload size limit enforced (`MAX_UPLOAD_SIZE_MB`)
- [x] SHA-256 checksum verification on all file operations (C-03)
- [x] Content-type validation on uploads

## SQL injection
- [x] SQLModel/SQLAlchemy parameterized queries throughout
- [x] No raw SQL strings in application code
- [x] Alembic migrations use op.* API (not raw DDL)

## CSRF protection
- [x] CSRF middleware implemented for state-changing UI routes
- [x] Token-based CSRF with per-session secrets

## Rate limiting
- [x] Rate limiting middleware on API endpoints
- [x] Configurable limits per endpoint category

## Secret management
- [x] All secrets via Kubernetes Secrets (never in values.yaml or env)
- [x] `envFromSecrets` pattern in Helm chart
- [x] No secrets in container image or git
- [ ] Secret rotation procedure documented

## Container security
- [x] Non-root user in Dockerfile
- [x] Multi-stage build (minimal final image)
- [ ] Read-only root filesystem (verify with `securityContext.readOnlyRootFilesystem`)
- [ ] Drop all capabilities (`securityContext.capabilities.drop: [ALL]`)

## Network security
- [x] NetworkPolicy template in Helm chart
- [x] Ingress restricted to defined namespaces
- [ ] NetworkPolicy enabled in production values

## Dependency scanning
- [ ] Trivy image scan in CI (this iteration)
- [ ] Dependabot or Renovate for dependency updates
- [x] `uv sync --frozen` ensures lockfile integrity

## Audit
- [x] Append-only AuditEvent table (C-05)
- [x] All state transitions logged with actor, timestamp, detail
- [x] Audit log export (CSV) for compliance

## Data protection
- [x] Researchers never hold S3 credentials (C-02)
- [x] Output objects immutable after submission (C-03)
- [x] RO-Crate assembled only at RELEASED state (C-11)
- [ ] Encryption at rest (depends on infrastructure — document requirement)
- [ ] TLS everywhere (ingress controller responsibility — document requirement)
```

---

## 6. Test plan

| Test | Method | Pass criteria |
|---|---|---|
| Alembic migrations on PostgreSQL | CI job `test-postgres` | `alembic upgrade head` succeeds |
| All 160+ tests on PostgreSQL | CI job `test-postgres` | All tests pass |
| Helm lint | CI job `helm-lint` | `helm lint` exits 0 |
| Helm template (default values) | CI job `helm-lint` | `helm template` exits 0 |
| Helm template (production values) | CI job `helm-lint` | `helm template -f values.production.yaml` exits 0 |
| Trivy scan | CI `build` job | No CRITICAL/HIGH unfixed vulnerabilities |
| PDB template renders | Helm template | PDB present when `podDisruptionBudget.enabled: true` |

No new pytest tests — this iteration's validation is CI-level.

---

## 7. New/modified files

```
.github/workflows/ci.yml                          # MODIFIED — PG job, Helm lint, Trivy, SHA pinning
helm/trevor/values.yaml                            # MODIFIED — add podDisruptionBudget defaults
helm/trevor/values.production.yaml                 # NEW — production overrides
helm/trevor/templates/pdb.yaml                     # NEW — PodDisruptionBudget template
helm/trevor/templates/tests/test-connection.yaml   # NEW — Helm chart test
alembic/versions/*.py                              # MODIFIED — dialect-conditional batch_alter_table
tests/conftest.py                                  # MODIFIED — optional TEST_DATABASE_URL fixture
docs/runbook.md                                    # NEW — operational runbook
docs/security-checklist.md                         # NEW — security checklist
```

---

## 8. Implementation order

1. Audit and fix Alembic migrations for PostgreSQL compatibility (`alembic/versions/*.py`)
2. Update `tests/conftest.py` — add `TEST_DATABASE_URL` support
3. Verify all tests pass locally against PostgreSQL (manual, optional)
4. Add `helm/trevor/templates/pdb.yaml` + update `values.yaml` with PDB defaults
5. Create `helm/trevor/values.production.yaml`
6. Add `helm/trevor/templates/tests/test-connection.yaml`
7. Validate: `helm lint` + `helm template` with both value files
8. Update `.github/workflows/ci.yml` — SHA pinning, PG job, Helm lint job, Trivy scan
9. Write `docs/runbook.md`
10. Write `docs/security-checklist.md`
11. Run lint + format + full test suite
