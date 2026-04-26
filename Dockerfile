# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_SYSTEM_PYTHON=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

WORKDIR /app

# ── deps layer ──────────────────────────────────────────────────────────────
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── final image ─────────────────────────────────────────────────────────────
FROM base AS final
COPY --from=deps /app/.venv /app/.venv
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src/ ./src/
COPY alembic/ ./alembic/
RUN uv sync --frozen --no-dev --no-editable

# .venv/bin on PATH so trevor, alembic, arq are directly callable
# (avoids `uv run` which re-syncs at runtime and fails as non-root)
ENV PATH="/app/.venv/bin:$PATH"

# Non-root user (C-07 / K8s best practice)
RUN useradd --uid 1000 --no-create-home trevor
USER trevor

EXPOSE 8000
ENTRYPOINT ["trevor"]
