# Iteration 7.5 Spec — Datastar UI

## Goal

Server-rendered Datastar UI covering all implemented backend functionality (iterations 1–7). Jinja2 templates, SSE for live updates, no JS build step. Hypermedia-first with reactive signals for interactive elements.

---

## Dependencies to add

```
jinja2>=3.1.6
sse-starlette>=2.3.0
```

`jinja2` is bundled with `fastapi[standard]` but pin explicitly. `sse-starlette` provides `EventSourceResponse` for Datastar SSE endpoints.

---

## Architecture

### Template structure

```
src/trevor/
  templates/
    base.html                   # Shell: <head>, nav, Datastar script, flash area
    components/
      nav.html                  # Top nav: logo, project switcher, user menu, role badge
      flash.html                # Flash message partial
      pagination.html           # Reusable paginator
      status_badge.html         # Request/object status pill
      file_preview.html         # Preview panel (markdown, CSV, code, image, PDF)
    researcher/
      request_list.html         # My requests table (filtered by project)
      request_create.html       # Create request form
      request_detail.html       # Request detail: status, objects, reviews, audit timeline
      object_upload.html        # Upload form (multipart, output_type, statbarn select)
      object_metadata.html      # Metadata form (description, justification, suppression)
      object_replace.html       # Replace object form
      revision_feedback.html    # Checker feedback display + replace/resubmit actions
    checker/
      review_queue.html         # Requests awaiting review (HUMAN_REVIEW status)
      review_form.html          # Review form: agent report, per-object decisions
    admin/
      request_overview.html     # All-projects request table with filters
      metrics_dashboard.html    # Pipeline metrics cards + stuck request list
      audit_log.html            # Filterable audit log table
      membership_manage.html    # Project membership CRUD
  static/
    style.css                   # Minimal custom CSS (utility-first, no framework)
    favicon.ico
```

### Router: `src/trevor/routers/ui.py`

Single router for all HTML views. Every route returns `TemplateResponse` or `EventSourceResponse`.

```
GET  /ui/                           → redirect to /ui/requests
GET  /ui/requests                   → researcher request list
GET  /ui/requests/new               → create request form
GET  /ui/requests/{id}              → request detail
GET  /ui/requests/{id}/upload       → upload object form
GET  /ui/requests/{id}/objects/{oid}/metadata  → metadata form
GET  /ui/requests/{id}/objects/{oid}/replace   → replace form
GET  /ui/requests/{id}/objects/{oid}/preview   → file preview (SSE partial)
GET  /ui/review                     → checker review queue
GET  /ui/review/{id}                → review form
GET  /ui/admin                      → admin request overview
GET  /ui/admin/metrics              → metrics dashboard
GET  /ui/admin/audit                → audit log
GET  /ui/admin/memberships/{project_id}  → membership management
```

### SSE endpoints (Datastar `data-on-load` targets)

```
GET  /ui/sse/request-status/{id}    → stream request status updates
GET  /ui/sse/review-queue           → stream new items arriving in review queue
```

### Form action endpoints (POST, return HTML fragments)

```
POST /ui/requests                   → create request, redirect to detail
POST /ui/requests/{id}/upload       → upload object, return updated object list
POST /ui/requests/{id}/submit       → submit request, return status update
POST /ui/requests/{id}/objects/{oid}/metadata  → save metadata, return confirmation
POST /ui/requests/{id}/objects/{oid}/replace   → upload replacement, return updated list
POST /ui/requests/{id}/resubmit     → resubmit, return status update
POST /ui/review/{id}                → submit review, redirect to queue
POST /ui/admin/memberships          → create membership, return updated list
DELETE /ui/admin/memberships/{id}   → delete membership, return updated list
```

---

## Datastar patterns

### Signal-driven state

```html
<!-- Project switcher -->
<div data-signals="{projectId: '{{current_project_id}}'}">
  <select data-bind="projectId" data-on-change="$$get('/ui/requests?project_id=' + projectId)">
    {% for p in projects %}
    <option value="{{p.id}}">{{p.display_name}}</option>
    {% endfor %}
  </select>
</div>
```

### SSE live updates

```html
<!-- Request detail: live status -->
<div id="status-area"
     data-on-load="$$get('/ui/sse/request-status/{{request.id}}')">
  {% include 'components/status_badge.html' %}
</div>
```

Server sends SSE fragments:

```python
async def sse_request_status(request_id: uuid.UUID):
    async def event_generator():
        last_status = None
        while True:
            req = await get_request(request_id)
            if req.status != last_status:
                last_status = req.status
                html = render_template("components/status_badge.html", request=req)
                yield {"event": "datastar-merge-fragments", "data": f'<div id="status-area">{html}</div>'}
            await asyncio.sleep(2)
    return EventSourceResponse(event_generator())
```

### File preview

```html
<!-- Preview panel: renders server-side based on output_type -->
<div id="preview-{{obj.id}}" data-on-click="$$get('/ui/requests/{{req.id}}/objects/{{obj.id}}/preview')">
  Preview
</div>
```

Server renders preview based on `output_type`:
- **tabular**: `polars.read_csv()` → first 500 rows → HTML table
- **figure/image**: `<img src="presigned-url">`
- **report/markdown**: `mistune.html(content)`
- **code**: `pygments.highlight(content)`
- **model/other**: raw text in `<pre>` block

---

## UI views detail

### 1. Base shell (`base.html`)

- Top nav: trevor logo, project dropdown (Datastar signal-bound), user name + role badge, logout
- Flash message area (Datastar `data-show` with auto-dismiss)
- Main content area (`{% block content %}`)
- Datastar CDN script (pinned version)
- Minimal CSS: system font stack, CSS custom properties for status colors, responsive grid

### 2. Researcher: Request list

- Table: title, status (color-coded badge), object count, updated_at, age
- Filter bar: status dropdown, direction dropdown (Datastar signals → SSE reload)
- "New Request" button
- Empty state message

### 3. Researcher: Create request

- Form: project (pre-selected), direction (egress default), title, description
- POST → redirect to detail page

### 4. Researcher: Request detail

- Header: title, status badge (SSE live), project name, submitter
- Tab panel (Datastar signals): Objects | Reviews | Audit
- **Objects tab**: card per object — filename, size, statbarn, status badge, preview button, metadata link. If CHANGES_REQUESTED: checker feedback inline, replace button.
- **Reviews tab**: agent review summary + findings, human review cards
- **Audit tab**: timeline of events
- Action bar (conditional on status):
  - DRAFT: Upload object button, Submit button
  - CHANGES_REQUESTED: Replace/Resubmit buttons
  - APPROVED: "Awaiting release" message (admin sees Release button)
  - RELEASED: Download link (pre-signed URL)

### 5. Researcher: Upload object

- Multipart form: file input, output_type select, statbarn select
- POST returns updated object list fragment (Datastar merge)

### 6. Researcher: Metadata form

- Fields: description, researcher_justification, suppression_notes
- PATCH via POST (form method override), returns confirmation fragment

### 7. Checker: Review queue

- Table: requests in HUMAN_REVIEW, sorted by age descending
- Columns: title, project, submitter, object count, agent decision, age
- SSE: new items appear live via `$$get('/ui/sse/review-queue')`
- Click → review form

### 8. Checker: Review form

- Left panel: object list with expandable preview + agent findings per object
- Right panel: review form
  - Overall decision: approve / changes_requested / reject (radio)
  - Per-object decisions: approve / changes_requested / reject + feedback textarea
  - Summary textarea
- Submit → POST, redirect to queue

### 9. Admin: Request overview

- Full table from `GET /admin/requests`
- Filter bar: status, project, direction (Datastar signals)
- Pagination
- Click → request detail

### 10. Admin: Metrics dashboard

- Cards: total requests, approval rate, median review hours, rejection rate
- Stuck requests alert list (highlighted if > 0)
- Requests per reviewer table
- By-status breakdown (horizontal bar or counts)

### 11. Admin: Audit log

- Table: timestamp, event_type, actor, request link, payload (expandable)
- Filter bar: event_type prefix, actor, date range
- Pagination
- CSV export button (links to `/admin/audit/export`)

### 12. Admin: Membership management

- Per-project table: user, role, assigned_by, actions (delete)
- Add membership form: user select, role select
- Inline feedback on role conflict errors

---

## Auth in UI routes

- All UI routes require auth (reuse `CurrentAuth` dep)
- Researcher views: filter by user's project memberships
- Checker views: filter by projects where user is `output_checker` or `senior_checker`
- Admin views: require `tre_admin` or `senior_checker`
- Unauthenticated → redirect to Keycloak login (dev mode: auto-login as dev-bypass-user)

---

## CSS approach

Minimal custom CSS. No Tailwind, no Bootstrap (no build step constraint). Use:
- CSS custom properties for colors/spacing
- System font stack
- CSS Grid for layout
- Status colors: green (approved/released), yellow (draft/pending), blue (in review), red (rejected), orange (changes requested)
- Responsive: single-column on mobile, sidebar nav on desktop

---

## New files

```
src/trevor/routers/ui.py
src/trevor/templates/base.html
src/trevor/templates/components/{nav,flash,pagination,status_badge,file_preview}.html
src/trevor/templates/researcher/{request_list,request_create,request_detail,object_upload,object_metadata,object_replace,revision_feedback}.html
src/trevor/templates/checker/{review_queue,review_form}.html
src/trevor/templates/admin/{request_overview,metrics_dashboard,audit_log,membership_manage}.html
src/trevor/static/style.css
```

---

## Test plan

UI tests are lightweight — verify template rendering and redirects, not pixel-level:

1. `GET /ui/requests` returns 200 with HTML content-type
2. `GET /ui/requests/{id}` returns request detail HTML
3. `POST /ui/requests` creates request and redirects
4. `GET /ui/review` returns review queue HTML
5. `GET /ui/admin` returns admin overview HTML
6. `GET /ui/admin/metrics` returns metrics dashboard HTML
7. Auth redirect: unauthenticated request → 302 or 403
8. File preview endpoint returns rendered HTML fragment

---

## Implementation order

1. Dependencies (`jinja2` pin, `sse-starlette`)
2. Base template + static CSS + nav component
3. Researcher views (request list → create → detail → upload → metadata)
4. File preview component
5. Checker views (queue → review form)
6. Admin views (overview → metrics → audit → memberships)
7. SSE endpoints (request status, review queue)
8. Tests
