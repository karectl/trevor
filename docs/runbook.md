# Runbook

Operational guide for trevor in production.

## Service overview

trevor is a stateless FastAPI service. State is in PostgreSQL and Redis. Kubernetes deployment with 2+ replicas behind a load balancer.

Components:

| Component | Image | Port |
|---|---|---|
| trevor API | `trevor:latest` | 8000 |
| ARQ worker | `trevor:latest` (cmd: `arq trevor.worker.WorkerSettings`) | — |
| PostgreSQL | managed or `bitnami/postgresql` | 5432 |
| Redis | `bitnami/redis` | 6379 |
| MinIO (non-AWS) | `minio/minio` | 9000 |

## Health check

```
GET /health → {"status": "ok", "version": "x.y.z"}
```

Kubernetes liveness and readiness probes target `/health`.

## Startup

```bash
uv run alembic upgrade head   # run migrations (once, or as init container)
uv run trevor                  # start API server (uvicorn on :8000)
uv run arq trevor.worker.WorkerSettings  # start ARQ worker
```

## Metrics

Prometheus scrape target: `GET /metrics` (no auth, plain text).

Key metrics:

| Metric | Description |
|---|---|
| `trevor_requests_submitted_total` | Airlock requests submitted (by direction) |
| `trevor_requests_approved_total` | Requests approved |
| `trevor_requests_rejected_total` | Requests rejected |
| `trevor_objects_uploaded_total` | Output objects uploaded |
| `trevor_agent_reviews_total` | Agent reviews completed |
| `http_requests_total` | HTTP requests by method/path/status |
| `http_request_duration_seconds` | Request latency histogram |

## Logging

Structured JSON logs written to stdout. Fields: `level`, `timestamp`, `event`, plus context fields (`request_id`, `user_id`, `trace_id` where available).

Log levels: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. Set via `LOG_LEVEL` env var.

## Tracing

OpenTelemetry tracing disabled by default. Enable with `OTEL_ENABLED=true`. Configure endpoint via `OTEL_EXPORTER_OTLP_ENDPOINT` (default: `http://otel-collector:4317`).

## ARQ worker

Processes `agent_review_job` tasks. Concurrency: default ARQ settings (1 worker, 10 concurrent tasks). Scale horizontally by running multiple worker pods. Each job is idempotent — duplicate processing harmless (state check prevents re-review).

Stuck jobs: requests stuck in `AGENT_REVIEW` or `HUMAN_REVIEW` for more than `STUCK_REQUEST_HOURS` (default 72) are surfaced in the admin metrics dashboard.

## Incident response

### Request stuck in AGENT_REVIEW

1. Check ARQ worker logs for errors.
2. Check Redis connectivity.
3. Check LLM endpoint (`AGENT_OPENAI_BASE_URL`) if LLM enabled.
4. If worker is healthy, check `audit_events` for the request — confirm `request.submitted` event exists.
5. If stuck, manually advance via DB update (last resort — create audit event too).

### Database connection failure

1. Check `DATABASE_URL` env var.
2. Check PostgreSQL pod/service health.
3. trevor returns 500 on all requests — check `/health` endpoint.
4. Restart trevor pods after DB is restored (connection pool auto-reconnects but may need flush).

### S3 / MinIO unreachable

1. File uploads return 500. Object downloads fail.
2. Check `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.
3. Verify bucket exists: `trevor-quarantine`, `trevor-release`.
4. trevor does not cache S3 connections — resumes automatically when S3 is restored.

### High error rate (5xx)

1. Check structured logs: `level=ERROR` events.
2. Check Prometheus: `http_requests_total{status=~"5.."}`
3. Check DB and Redis connectivity.
4. Check OTel traces if enabled.

## Backup and recovery

- PostgreSQL: standard pg_dump / WAL archiving. Include all tables. `audit_events` is append-only — critical for compliance.
- Redis: transient (ARQ queues). Loss means queued jobs must be resubmitted manually or will be retried on restart.
- S3: enable versioning and cross-region replication on `trevor-release` bucket (release artifacts are permanent).

## Migrations

```bash
# Check pending migrations
uv run alembic current
uv run alembic history

# Apply migrations
uv run alembic upgrade head

# Rollback one step (dev only)
uv run alembic downgrade -1
```

In production, run migrations as a Kubernetes Job or init container before rolling out new API pods.

## Scaling

trevor API is stateless — scale horizontally. ARQ workers can also scale horizontally (multiple pods reading from same Redis queue).

Recommended minimums for production:
- API: 2 replicas, 256m CPU, 256Mi memory
- Worker: 1 replica, 256m CPU, 256Mi memory
- PostgreSQL: 1 primary + 1 replica
- Redis: single instance (or sentinel for HA)
