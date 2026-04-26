# Iteration 12.5 Spec — Checker UI Redesign (Split-Pane Review)

## Goal

Redesign the output checker UI from a flat review queue into a three-level drill-down (project list → request list → split-pane review) inspired by the SACRO Outputs Viewer desktop application, adapted for trevor's domain model (statbarn categories, agent rule checks, disclosure risk — not ACRO).

---

## Current state (before this iteration)

| Component | Status |
|---|---|
| `/ui/review` — flat review queue | Working — table of all HUMAN_REVIEW requests across projects |
| `/ui/review/{id}` — review form | Working — linear card layout, objects listed then form at bottom |
| Agent review summary | Displayed as single block of text |
| Per-object agent assessment | Not surfaced in UI |
| Researcher metadata (justification, suppression notes) | Not surfaced in checker view |
| Statbarn category display | Not surfaced in checker view |
| Object selection | Page-level — each object a separate card |

---

## Scope decisions

| Item | Decision | Rationale |
|---|---|---|
| Navigation model | Project → request list → split-pane | Checkers work per-project; SACRO Viewer drills down similarly |
| Object selection | Client-side via Datastar `data-store` | No page reload when switching objects; responsive UX |
| Template inheritance | Standalone HTML for review form | Split-pane is full-viewport; `base.html` shell incompatible |
| Release model | All-or-none | Domain constraint — no partial release of some outputs |
| Agent findings parsing | Keyed by `object_id` in `Review.findings` JSON | Existing schema stores per-object assessments in findings list |
| Old templates | Preserved on disk, unused | No routes reference them; safe to delete later |

---

## 1. Route structure

### `/ui/review` — Project list (was: flat review queue)

Displays a card grid of projects where the user holds a checker role (`output_checker` or `senior_checker`), or all projects if admin.

Each card shows:
- Project display name
- Count of requests in `HUMAN_REVIEW` state
- Oldest waiting time (humanized: "< 1 hour", "3h", "2d 5h")

Sorted by pending count descending (busiest first).

```
GET /ui/review → checker/project_list.html
```

### `/ui/review/project/{project_id}` — Request list (new)

Table of requests in `HUMAN_REVIEW` for the selected project. Each row shows:
- Title
- Object count (non-superseded)
- Agent decision badge
- Updated timestamp
- "Review" button → `/ui/review/{id}`

Back navigation: `← Projects` button returns to `/ui/review`.

```
GET /ui/review/project/{project_id} → checker/request_list.html
```

### `/ui/review/{request_id}` — Split-pane review (redesigned)

Full-viewport layout, standalone HTML (not extending `base.html`).

**Header bar**: back arrow (→ project request list), brand, request title, status badge, progress counter ("3/5 reviewed"), user info, logout link.

**Left sidebar** (340px, scrollable):
- Object count summary
- Object rows: colored status indicator dot, filename, version/type/statbarn/size metadata, state badge
- Datastar-powered selection: clicking a row sets `$selected` index, highlights row

**Right detail panel** (flex: 1, scrollable):
- Object filename as h2
- Metadata grid: output type, statbarn category, version, size, uploaded date, SHA-256 (truncated)
- Researcher metadata section (if present): description, justification, suppression notes
- Agent assessment section (if present):
  - Statbarn confirmed (pass/fail badge)
  - Disclosure risk level (color-coded: none/low/medium/high)
  - Recommendation badge (approve/changes_requested/escalate)
  - Rule checks list: pass/fail icon, rule name, severity badge, detail text
  - Agent narrative (explanation text in styled box)
- Per-object review controls (for PENDING objects):
  - Approve / Reject button group (toggle active state via DOM)
  - Comment textarea (required for rejection)
- Already-decided objects show state badge + "Already decided" label

**Bottom footer bar** (fixed):
- Overall decision: radio buttons (Approve all / Request changes / Reject)
- Summary text input
- Submit Review button
- Hidden form fields for per-object decisions + CSRF token

```
GET  /ui/review/{request_id} → checker/review_form.html
POST /ui/review/{request_id} → redirect to /ui/review/project/{project_id}
```

---

## 2. Data loading changes

The `review_form` route now loads additional data:

1. **Object metadata**: `OutputObjectMetadata` for each object's `logical_object_id`, passed as `object_metadata` dict keyed by UUID.
2. **Agent assessments**: Parsed from `Review.findings` JSON list — each finding with an `object_id` key is extracted into `agent_assessments` dict keyed by string object ID.
3. **Agent review**: Still loaded as before for fallback summary display.

---

## 3. Helper functions

### `_humanize_timedelta(dt: datetime) -> str`

Converts a datetime to human-readable relative time. Used for "oldest waiting" on project cards.

### `_checker_project_ids(user_id, session, is_admin) -> list[UUID]`

Returns project IDs where user holds checker role, or all project IDs if admin. Extracted from inline query in old `review_queue` route for reuse.

---

## 4. CSS additions

All reviewer styles prefixed with `rv-` to avoid collision with existing styles. ~170 lines added to `style.css`.

Key classes:
- `.rv-shell`, `.rv-header`, `.rv-main`, `.rv-sidebar`, `.rv-detail`, `.rv-footer` — layout
- `.rv-file-row`, `.rv-file-selected`, `.rv-file-indicator` — sidebar object list
- `.rv-meta-grid`, `.rv-meta-label`, `.rv-meta-value` — detail metadata
- `.rv-pass-badge`, `.rv-fail-badge`, `.rv-risk-*` — assessment indicators
- `.rv-rule`, `.rv-severity-*` — rule check display
- `.rv-btn-group`, `.rv-approve-btn`, `.rv-reject-btn` — per-object review controls
- `.review-project-grid`, `.review-project-card` — project list cards

---

## 5. Templates

| File | Type | Description |
|---|---|---|
| `checker/project_list.html` | New | Project card grid, extends `base.html` |
| `checker/request_list.html` | New | Request table for project, extends `base.html` |
| `checker/review_form.html` | Rewritten | Standalone full-viewport split-pane |
| `checker/review_queue.html` | Preserved | No longer referenced by any route |

---

## 6. Tests

| Test | What it verifies |
|---|---|
| `test_review_project_list_html` | Project list page renders for admin |
| `test_review_project_request_list` | Request list renders for specific project |
| `test_review_split_pane_renders` | Full split-pane with objects, metadata, statbarn visible |
| `test_review_project_404` | Non-existent project returns 404 |

---

## 7. Design principles (from reference)

The SACRO Outputs Viewer HTML mockup provided the visual direction:
- Clean white background, minimal borders, system fonts
- Left sidebar with file list and colored status indicators
- Right detail panel with structured metadata grid
- Approve/Reject button group per object (green/neutral, active state feedback)
- Footer bar for overall decision

Adaptations for trevor:
- **No ACRO terminology**: "statbarn category" instead of "ACRO status", "agent assessment" instead of "ACRO risk profile"
- **Agent rule checks**: individual rule pass/fail with severity badges — richer than SACRO's single pass/fail
- **Disclosure risk levels**: none/low/medium/high with color coding
- **Researcher metadata**: justification and suppression notes visible to checker
- **All-or-none release**: overall decision applies to entire request; per-object decisions feed into overall
- **Datastar interactivity**: client-side object switching without page reload
