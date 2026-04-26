# trevor Runbook

Operational reference for the trevor egress/airlock microservice.

---

## Deployment

### Prerequisites

- Kubernetes cluster (k3d locally; production: any CNCF-conformant cluster)
- Helm 3
- `kubectl` configured against the target cluster
- Secrets pre-created (see [Secrets](#secrets))

### First-time install

```bash
# Create namespace
kubectl create namespace trevor

# Create secrets (see Secrets section below)
kubectl apply -f secrets/ -n trevor

# Install chart
helm upgrade --install trevor ./helm/trevor \
  -n trevor \
  -f helm/trevor/values.production.yaml \
  --set image.tag=<sha>
```

### Rolling upgrade

```bash
helm upgrade trevor ./helm/trevor \
  -n trevor \
  -f helm/trevor/values.production.yaml \
  --set image.tag=<new-sha>
```

The `migrations.hookEnabled: true` default runs an Alembic `upgrade head` Job as a Helm pre-upgrade hook before the new pods roll out.

### Rollback

```bash
helm rollback trevor -n trevor
```

If the migration hook has already run, you may need to run `alembic downgrade -1` manually before rolling back the chart.

---

## Secrets

All secrets are Kubernetes `Secret` objects. Never put secret values in Helm values files.

| Secret name | Keys |
|---|---|
| `trevor-db` | `DATABASE_URL` — `postgresql+asyncpg://user:pass@host:5432/trevor` |
| `trevor-redis` | `REDIS_URL` — `redis://:pass@host:6379/0` |
| `trevor-keycloak` | `KEYCLOAK_URL`, `KEYCLOAK_INTERNAL_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID` |
| `trevor-s3` | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_QUARANTINE_BUCKET`, `S3_RELEASE_BUCKET` |
| `trevor-app` | `SECRET_KEY` (random 32-byte hex), `TREVOR_BASE_URL` |
| `trevor-smtp` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_ADDRESS`, `SMTP_USERNAME`, `SMTP_PASSWORD` |
| `trevor-agent` | `AGENT_OPENAI_BASE_URL`, `AGENT_MODEL_NAME`, `AGENT_API_KEY` |

Generate `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Migrations

trevor uses Alembic async migrations. All migrations are in `alembic/versions/`.

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Check current revision
uv run alembic current

# Show migration history
uv run alembic history

# Generate a new migration (after model changes)
uv run alembic revision --autogenerate -m "describe the change"
```

**SQLite autogenerate caveats** (local dev only):

- `import sqlmodel` must be present in the generated migration file — add it if missing.
- `projects.status` enum changes are phantom-detected — remove them manually.
- Use `op.batch_alter_table()` for any `ALTER COLUMN` on SQLite.

**Production (PostgreSQL)**: autogenerate is reliable. Review each generated migration before committing.

---

## Failure modes

### App pod CrashLoopBackOff

1. `kubectl logs -n trevor deploy/trevor --previous`
2. Common causes:
   - Missing or malformed secret (check `DATABASE_URL`, `SECRET_KEY`)
   - DB not reachable (check `trevor-db` secret, network policy)
   - Alembic migration not yet applied (run `alembic upgrade head` Job)

### Worker not processing jobs

1. `kubectl logs -n trevor deploy/trevor-worker`
2. Check Redis connectivity (`REDIS_URL` secret)
3. Check `arq` queue: `redis-cli -u $REDIS_URL LLEN arq:queue`
4. Restart worker: `kubectl rollout restart deploy/trevor-worker -n trevor`

### SSE connections not updating

SSE streams poll every 2 seconds for up to 5 minutes. If the UI badge does not update:
1. Check browser dev tools → Network → EventSource for errors
2. Check app pod logs for DB errors
3. Ensure the pod has DB connectivity

### Presigned URL expired

If a researcher reports an expired download link:
1. An admin can regenerate via `/ui/admin/requests/{id}` → "Generate new URL"
2. The `url_expiry_warning_job` cron runs daily at midnight and warns 48h in advance

### Stuck request (SLA breach)

The `stuck_request_alert_job` runs daily at 06:00 and notifies output checkers when a request has been in `SUBMITTED` or `HUMAN_REVIEW` for longer than `STUCK_REQUEST_HOURS` (default 72h).

---

## Monitoring

trevor emits structured JSON logs (`LOG_FORMAT=json`). Recommended Grafana/Loki setup:

- **Loki** — ingest all pod logs; filter on `service=trevor`
- **Alerting**:
  - `level=error` count > 0 in 5 min window
  - `agent_review_job failed` log line
  - Pod restart count > 2 in 10 min

OpenTelemetry: set `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT` to enable trace export. The `OTEL_SERVICE_NAME` defaults to `trevor`.

### Key metrics to watch

| Signal | Source | Threshold |
|---|---|---|
| Request queue depth | `/admin/metrics` | > 20 pending |
| Agent review failure rate | App logs | Any `agent_review_failed` event |
| Worker lag | Redis `arq:queue` length | > 50 |
| Error rate (5xx) | Ingress access logs | > 1% of requests |
| DB connection pool exhaustion | App logs | `QueuePool limit` errors |

---

## Scaling

trevor is stateless — all state is in PostgreSQL and Redis (C-08).

- **Horizontal app scaling**: increase `replicaCount` or enable `autoscaling`. No coordination needed between replicas.
- **Worker scaling**: increase `worker.replicaCount`. ARQ workers are safe to run in parallel — jobs are claimed atomically from the Redis queue.
- **DB connection pooling**: SQLAlchemy's default pool size is 5 per process. With 3 app replicas + 2 workers = 25 connections max. Ensure PostgreSQL `max_connections` allows headroom.
- **S3**: SeaweedFS or any S3-compatible store. No trevor-side pooling — `aioboto3` manages connections per request.

---

## Backup and recovery

- **Database**: standard PostgreSQL backup (pg_dump, WAL archiving). trevor's `AuditEvent` table is append-only — never restore to a state that loses audit rows.
- **S3 quarantine bucket**: objects are immutable after upload. Back up the bucket with versioning enabled.
- **S3 release bucket**: RO-Crate zips. Regenerable from quarantine + DB if lost (run `release_job` again on a restored request).
- **Redis**: transient — ARQ job queue. Jobs can be re-enqueued manually if Redis is lost mid-flight.
