# ADR-0003 — Database Strategy: SQLite → PostgreSQL Migration Path

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

trevor needs a relational database for its audit trail, workflow state, metadata, and notification records. The requirements are:
- Append-only audit log (no deletes)
- Complex relational queries for the admin dashboard (request metrics, checker performance, project summaries)
- Support for horizontal scaling of the app tier (shared DB, stateless app pods)
- Simple local development setup
- SQLModel (FastAPI ecosystem standard) as the ORM

---

## Decision

Use **SQLite** for early development and **PostgreSQL** for production/staging, with a clean migration path via Alembic.

### Rationale

SQLModel supports both engines without code changes. SQLite is zero-config for local development; PostgreSQL is the production-grade choice for:
- Concurrent writes from multiple pods (SQLite WAL mode handles modest concurrency but is not suitable for distributed horizontal scaling)
- Advanced query features (window functions, JSONB indexing) needed for the audit/metrics queries
- Mature Kubernetes operators (CloudNativePG) available in the karectl cluster

The switch is purely configuration — the SQLModel model definitions and Alembic migration files work identically on both engines.

### Migration strategy

- Alembic manages all schema migrations from day one.
- Migration files are generated from SQLModel model changes (`alembic revision --autogenerate`).
- The database URL is injected via environment variable (`DATABASE_URL`).
- SQLite in development: `sqlite+aiosqlite:///./trevor.db`
- PostgreSQL in production: `postgresql+asyncpg://...`

### Session management

- Async sessions throughout (`AsyncSession`, `async_sessionmaker`).
- A single session factory is created at startup and injected via FastAPI dependency.
- No raw SQL except for complex audit/metrics queries where SQLAlchemy Core expressions are used.

---

## Consequences

- **Positive**: Zero-config local dev. Instant startup.
- **Positive**: Clean production path to PostgreSQL without model changes.
- **Positive**: Alembic migration history serves as a schema changelog.
- **Negative**: SQLite is not suitable for production multi-pod deployments — this must be documented clearly so it is never accidentally used in production.
- **Mitigation**: The Helm chart will require `database.url` to be set explicitly and will fail validation if it detects a SQLite URL in a non-dev environment (via a startup check).

---

## Schema design principles

1. **UUIDs as primary keys** throughout (not integer sequences) — avoids enumeration attacks and simplifies cross-service references.
2. **`created_at` / `updated_at`** on every mutable table, set server-side.
3. **AuditEvent table is insert-only** — no UPDATE or DELETE permissions granted to the application role.
4. **JSON columns** used sparingly (checker findings, notification payload) — not for queryable fields.
5. **Soft deletes** are not used. Objects are immutable; deletion is not a supported operation.
