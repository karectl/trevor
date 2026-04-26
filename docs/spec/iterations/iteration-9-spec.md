# Iteration 9 Spec — Hardening & Observability

## Goal

Production-ready hardening. Prometheus metrics, structured JSON logging, OpenTelemetry tracing, CSRF protection, error pages, input validation audit, Helm review, and runbook.

---

## Scope decisions

| Item | Decision |
|---|---|
| Prometheus `/metrics` | Implement via `prometheus-fastapi-instrumentator` |
| Structured logging | `structlog` with JSON renderer in prod, console in dev |
| OpenTelemetry tracing | `opentelemetry-sdk` + OTLP exporter; auto-instrumentation for FastAPI + SQLAlchemy |
| CSRF | `itsdangerous` signed tokens; all state-mutating UI forms get hidden `csrf_token` field |
| Rate limiting | `slowapi` (Starlette middleware); apply to auth-sensitive endpoints |
| Error pages | Jinja2 templates for 403, 404, 422, 500; FastAPI exception handlers |
| Input validation audit | No new code — verify existing Pydantic models cover all inputs; document findings |
| Horizontal scaling | Document ARQ concurrency settings; no new code |
| Pen test checklist | Markdown doc in `docs/` |
| Runbook | Markdown doc in `docs/` |
| Helm values review | Update `helm/trevor/values.yaml` with prod recommendations |

---

## Dependencies to add

```
prometheus-fastapi-instrumentator>=7.0.0
structlog>=24.0.0
opentelemetry-sdk>=1.25.0
opentelemetry-exporter-otlp-proto-grpc>=1.25.0
opentelemetry-instrumentation-fastapi>=0.46b0
opentelemetry-instrumentation-sqlalchemy>=0.46b0
itsdangerous>=2.2.0
slowapi>=0.1.9
```

---

## 1. Prometheus metrics

### Endpoint

`GET /metrics` — returns Prometheus text format. No auth (scrape target).

### Implementation

```python
from prometheus_fastapi_instrumentator import Instrumentator

def create_app(settings):
    app = FastAPI(...)
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    ...
```

Exposed metrics:
- `http_requests_total` — by method, path, status
- `http_request_duration_seconds` — histogram
- `http_requests_in_progress` — gauge

Custom metrics (in `src/trevor/metrics.py`):
```python
from prometheus_client import Counter, Gauge

requests_submitted_total = Counter("trevor_requests_submitted_total", "Total submitted airlock requests", ["direction"])
requests_approved_total = Counter("trevor_requests_approved_total", "Total approved requests", ["direction"])
requests_rejected_total = Counter("trevor_requests_rejected_total", "Total rejected requests", ["direction"])
agent_reviews_total = Counter("trevor_agent_reviews_total", "Total agent reviews completed")
```

Increment custom counters in routers at state transitions.

---

## 2. Structured JSON logging

### Settings

New env vars:

| Variable | Purpose | Default |
|---|---|---|
| `LOG_LEVEL` | Log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) | `INFO` |
| `LOG_FORMAT` | `json` or `console` | `json` in prod, `console` if `DEV_AUTH_BYPASS` |

### Implementation (`src/trevor/logging.py`)

```python
import structlog

def configure_logging(log_level: str, log_format: str) -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(processors=processors, ...)
```

Called from `lifespan` in `app.py`. All `logging.getLogger(__name__)` calls replaced with `structlog.get_logger()`.

Structured fields added automatically:
- `request_id` — set via `structlog.contextvars` in middleware
- `user_id` — set after auth resolution
- `trace_id` — injected by OTel middleware

---

## 3. OpenTelemetry tracing

### Settings

| Variable | Purpose | Default |
|---|---|---|
| `OTEL_ENABLED` | Enable OTel export | `false` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP gRPC endpoint | `http://otel-collector:4317` |
| `OTEL_SERVICE_NAME` | Service name in traces | `trevor` |

### Implementation (`src/trevor/telemetry.py`)

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

def configure_telemetry(settings: Settings, engine) -> None:
    if not settings.otel_enabled:
        return
    provider = TracerProvider(...)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(...)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
```

---

## 4. CSRF protection

### Scope

All state-mutating UI form POSTs (not API endpoints — API uses JWT auth which is CSRF-safe).

### Implementation

`src/trevor/csrf.py`:

```python
from itsdangerous import URLSafeTimedSerializer

def generate_csrf_token(secret_key: str, session_id: str) -> str:
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(session_id)

def validate_csrf_token(secret_key: str, token: str, session_id: str, max_age: int = 3600) -> bool:
    s = URLSafeTimedSerializer(secret_key)
    try:
        s.loads(token, max_age=max_age)
        return True
    except Exception:
        return False
```

New settings:
- `SECRET_KEY` — used for CSRF token signing (required in prod; random default in dev)

Template integration: `base.html` gets `{{ csrf_token }}` injected via context. All form `<form>` tags get `<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`.

Middleware checks `csrf_token` form field on all POST/PUT/DELETE UI routes.

---

## 5. Rate limiting

### Implementation

`slowapi` middleware applied to auth-sensitive paths:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/requests/{id}/submit")
@limiter.limit("10/minute")
async def submit_request(...):
    ...
```

Apply limits:
- `/requests/*/submit` and `/requests/*/resubmit` — 10/minute per IP
- `POST /memberships` — 30/minute per IP
- UI form POSTs — 20/minute per IP

---

## 6. Error pages

### FastAPI exception handlers in `app.py`

```python
@app.exception_handler(403)
async def forbidden_handler(request, exc):
    return templates.TemplateResponse("errors/403.html", {"request": request}, status_code=403)

@app.exception_handler(404)
async def not_found_handler(request, exc):
    return templates.TemplateResponse("errors/404.html", {"request": request}, status_code=404)

@app.exception_handler(500)
async def server_error_handler(request, exc):
    return templates.TemplateResponse("errors/500.html", {"request": request}, status_code=500)
```

Only apply HTML responses for UI routes (check `Accept: text/html`). API routes keep JSON error responses.

### New templates

```
src/trevor/templates/errors/
  403.html    # Forbidden — insufficient permissions
  404.html    # Not found
  422.html    # Validation error
  500.html    # Internal server error
```

---

## 7. Input validation audit

No new code. Audit findings documented in `docs/security.md`:

- All request bodies validated by Pydantic models ✓
- UUID path parameters use `uuid.UUID` type (FastAPI validates) ✓
- File uploads: no size limit currently → add `MAX_UPLOAD_SIZE_MB` setting + check in `upload_object`
- Form fields: string inputs unbounded → add `max_length` to critical fields
- `statbarn` field: free text, no validation → acceptable (checker validates)
- `OutputType` enum enforced by FastAPI ✓

Action items:
- Add `MAX_UPLOAD_SIZE_MB` setting (default: 500)
- Enforce in `upload_object` endpoint

---

## 8. New settings

```python
class Settings(BaseSettings):
    ...
    log_level: str = "INFO"
    log_format: str = "json"
    otel_enabled: bool = False
    otel_exporter_endpoint: str = "http://otel-collector:4317"
    otel_service_name: str = "trevor"
    secret_key: str = "dev-secret-key-change-in-prod"
    max_upload_size_mb: int = 500
```

---

## DB migration

None required.

---

## New files

```
src/trevor/
  logging.py          # structlog configuration
  telemetry.py        # OTel setup
  csrf.py             # CSRF token helpers
  metrics.py          # Custom Prometheus counters
  templates/errors/
    403.html
    404.html
    422.html
    500.html
docs/
  security.md         # Input validation audit, pen test checklist
  runbook.md          # Ops runbook: startup, health checks, incident response
helm/trevor/
  values.yaml         # Updated with prod resource limits, replicas, OTel config
```

---

## Test plan

1. `GET /metrics` → 200, `text/plain`, contains `http_requests_total`
2. `GET /nonexistent` → 404 HTML response for UI accept header
3. `GET /nonexistent` → 404 JSON for API accept header
4. CSRF: POST to UI form without token → 403
5. CSRF: POST to UI form with valid token → 200/303
6. Upload > MAX_UPLOAD_SIZE_MB → 413
7. Structlog: JSON output includes `level`, `timestamp`, `event` fields
8. OTel: disabled by default, no errors on startup

---

## Implementation order

1. New settings (`log_level`, `log_format`, `otel_enabled`, `secret_key`, `max_upload_size_mb`)
2. Structured logging (`logging.py`, wire into lifespan)
3. Prometheus metrics (`metrics.py`, instrumentator in `app.py`)
4. Error page templates + exception handlers
5. Upload size limit
6. CSRF (`csrf.py`, middleware, template injection)
7. Rate limiting (slowapi)
8. OpenTelemetry (`telemetry.py`, wire into lifespan)
9. Tests
10. Docs: `security.md`, `runbook.md`, Helm values update
