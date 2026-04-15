# ADR-0001 — Frontend Framework: Datastar

**Status**: Accepted  
**Date**: 2025-01  
**Deciders**: trevor project lead

---

## Context

trevor requires a web UI that serves two distinct audiences:

1. **Researchers** — uploading files, annotating outputs, monitoring request progress, responding to feedback, previewing their submitted outputs in their native format (rendered markdown, CSV tables, images, PDFs, parquet previews).
2. **Output Checkers / Admins** — reviewing requests, reading agent reports, annotating per-object feedback, managing assignments, monitoring dashboards with metrics.

The tech brief specifies either **htmx** or **Datastar** for the frontend, with a "slick" UI as a quality requirement.

Two options were evaluated:

### Option A: htmx

- Mature, stable, widely adopted in Python/FastAPI ecosystems.
- Hypermedia-only model: server returns HTML fragments, client swaps them in.
- Very limited client-side state — fine for simple CRUD but awkward for:
  - File upload with progress tracking
  - Live file preview panels
  - Dashboard with reactive filtering/sorting
  - Multi-step forms with local validation state
- Requires Alpine.js or similar for any meaningful reactivity, increasing surface area.
- Extensions ecosystem is fragmented.

### Option B: Datastar

- Newer (v0.20+), purpose-built as an htmx successor with reactive signals built in.
- Ships a unified reactive model: `data-signals`, `data-bind`, `data-on`, `data-computed` — no Alpine.js needed.
- Server-Sent Events (SSE) as the primary server-push mechanism — pairs naturally with FastAPI's `EventSourceResponse` and async generators.
- Supports streaming partial updates — well-suited for progressive agent report rendering.
- File upload with progress via `data-on:click` + fetch API wired to signals.
- `data-show` / `data-class` directives make conditional UI (tab panels, preview modals) clean.
- Single ~14kb script, no build step.
- Less mature than htmx; smaller community. This is accepted given the team's development model (Claude Code / spec-driven).

---

## Decision

Use **Datastar** as the frontend framework.

Rationale:
- The "slick UI" requirement pushes against htmx's hypermedia-only constraints without a reactive layer.
- Native SSE support is a first-class match for streaming the agent review report to the UI in real time.
- Signals-based reactivity removes the need for Alpine.js, keeping the dependency surface small.
- File preview panels (render markdown, display images, embed PDFs, tabulate CSV/parquet) benefit from client-side signal-driven state management.
- FastAPI's async streaming + Datastar SSE is a clean, idiomatic combination.

---

## Consequences

- **Positive**: Reactive UI without a JavaScript build pipeline. SSE streaming is first-class. Less JS boilerplate.
- **Positive**: Jinja2 templating for server-rendered HTML remains the source of truth — good for accessibility and simplicity.
- **Negative**: Smaller community; fewer pre-built examples to draw on.
- **Negative**: API surface is newer and may see breaking changes — pin Datastar version explicitly.
- **Mitigation**: Pin to a specific Datastar release in the base template. Abstract all Datastar usage into a small set of Jinja2 macros so migration cost is bounded.

---

## File preview strategy

Output objects must be previewable in the UI without downloading:

| Output type | Preview approach |
|-------------|-----------------|
| `narrative_markdown`, `table_markdown` | Server-renders markdown to HTML via `mistune`; injected into preview panel |
| `table_csv` | Server streams first 500 rows as HTML table |
| `table_parquet` | Server reads via `polars`, converts to CSV preview |
| `plot_image` | Inline `<img>` tag with pre-signed short-lived URL to quarantine bucket |
| `document_pdf` | Browser `<embed>` with pre-signed URL |
| `model_output` | Rendered as code block |
| `code` | Syntax-highlighted code block via server-side `pygments` |
