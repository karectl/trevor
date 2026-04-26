---
icon: lucide/layout-dashboard
---

# UI Guide

trevor's web interface uses [Datastar](https://data-star.dev/) — a hypermedia framework that delivers reactive, server-rendered HTML over SSE. No JavaScript build step required.

## Technology choices

- **Datastar v1** — hypermedia + SSE signals, loaded via CDN
- **Jinja2** — server-side templating
- **`datastar-py`** — official Python SDK for FastAPI integration
- **Minimal CSS** — custom properties, system fonts, no framework

See [ADR-0001](spec/adrs/0001-frontend-framework.md) for the rationale.

## Template structure

```
src/trevor/templates/
  base.html                   # Shell: <head>, nav, Datastar CDN, flash area
  components/
    nav.html                  # Top nav: logo, role-aware links, user info
    flash.html                # Flash message partial
    pagination.html           # Reusable paginator
    status_badge.html         # Request/object status pill
    file_preview.html         # Preview panel (markdown, CSV, code, image)
  researcher/
    request_list.html         # My requests table (filtered by project/status)
    request_create.html       # Create request form
    request_detail.html       # Detail: status, objects, reviews, audit
    object_upload.html        # Upload form (multipart, output_type, statbarn)
    object_metadata.html      # Metadata form (description, justification)
    object_replace.html       # Replace object form
    revision_feedback.html    # Checker feedback display
  checker/
    review_queue.html         # Requests awaiting review
    review_form.html          # Review: agent report + per-object decisions
  admin/
    request_overview.html     # All-projects request table with filters
    metrics_dashboard.html    # Pipeline metrics cards + stuck requests
    audit_log.html            # Filterable audit log table
    membership_manage.html    # Project membership CRUD
```

## Views by role

### Researcher

- **Request list** (`/ui/requests`) — table with status badges, object counts, filters by status and project
- **Create request** (`/ui/requests/new`) — form with project selector, direction, title, description
- **Request detail** (`/ui/requests/{id}`) — tabbed view with Objects, Reviews, and Audit tabs. Action buttons change based on request status (Upload, Submit, Resubmit, Release)
- **Upload object** (`/ui/requests/{id}/upload`) — multipart form with output type and statbarn
- **Metadata** (`/ui/requests/{id}/objects/{oid}/metadata`) — description, justification, suppression notes
- **Replace** (`/ui/requests/{id}/objects/{oid}/replace`) — upload replacement for rejected objects

### Checker

- **Review queue** (`/ui/review`) — requests in `HUMAN_REVIEW` status, shows agent decision
- **Review form** (`/ui/review/{id}`) — agent findings alongside per-object decision form (approve/changes_requested/reject + feedback per object)

### Admin

- **Request overview** (`/ui/admin`) — all-projects view with status filter
- **Metrics dashboard** (`/ui/admin/metrics`) — total requests, approval rate, median review time, rejection rate, by-status breakdown, stuck requests
- **Audit log** (`/ui/admin/audit`) — filterable by event type, paginated, CSV export link
- **Membership management** (`/ui/admin/memberships/{pid}`) — add/remove members with role conflict validation

## CSS approach

Minimal custom CSS in `src/trevor/static/style.css`:

- CSS custom properties for colors and spacing
- System font stack (no web fonts)
- CSS Grid for layout
- Status colors: green (approved/released), yellow (draft/pending), blue (in review), red (rejected), orange (changes requested)
- Light/dark mode via CSS custom properties
- Responsive: single-column on mobile

## Datastar patterns

### Signal-driven state

```html
<!-- Tabs on request detail -->
<div data-signals="{tab: 'objects'}">
  <span class="tab" data-class-active="tab.value === 'objects'"
        data-on-click="tab.value = 'objects'">Objects</span>
</div>
```

### SSE live updates (planned)

```html
<div id="status-area"
     data-on-load="$$get('/ui/sse/request-status/{{id}}')">
  <!-- status badge updated via SSE -->
</div>
```

SSE endpoints use `datastar-py`'s `ServerSentEventGenerator.patch_elements()` to push HTML fragments.
