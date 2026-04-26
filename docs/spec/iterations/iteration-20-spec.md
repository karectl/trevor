# Iteration 20 Spec — Two-Panel Researcher/Checker UI

## Goal

Redesign the researcher request detail page and the checker review form into a consistent two-panel layout: a scrollable left nav showing the object list with file-type icons and per-object state indicators, and a right detail panel with inline upload, inline metadata editing, file preview, and agent assessment. Bring the checker review form into visual parity.

---

## Current state

| Component | Status |
|---|---|
| Researcher detail page (`/ui/requests/{id}`) | Single-column layout; separate pages for upload and metadata edit |
| Checker review form (`/ui/review/{id}`) | Split-pane layout from iter 12.5, but uses deprecated `data-store` (Datastar v0 API); no file-type icons; title not surfaced in metadata section |
| `OutputObjectMetadata.title` field | Exists in DB model; not exposed in any form or UI |
| Object upload form (`/ui/requests/{id}/upload`) | Separate page; does not save metadata at upload time |
| Object metadata form (`/ui/requests/{id}/objects/{id}/metadata`) | Separate page; missing `title` field |
| CSS | No file-type icon palette; no object state dot component |
| Tests | No tests specifically covering the two-panel layout or `title` field |

---

## Scope

| Item | Decision |
|---|---|
| Researcher detail page | Rewrite as two-panel layout |
| Checker review form | Rebuild to match — left nav, `data-signals`, title in metadata |
| Inline upload | Replace separate upload page with inline panel |
| Inline metadata edit | Replace separate metadata page with inline form per object |
| `title` field | Expose in upload, inline metadata edit, and checker review |
| File-type icon palette | Add CSS classes `ft-icon ft-{type}` |
| Object state dots | Add CSS class `obj-state-dot dot-{STATE}` |
| `_build_request_detail_ctx` | Extract shared context helper for reuse |
| New pages/routes | None — existing routes reused |

---

## 1. Researcher detail page

### 1a. Layout

Replace the current single-column layout with a two-panel shell:

```
┌─────────────────────────────────────────────────┐
│ rv-header (title, status badge, progress, logout)│
├───────────────┬─────────────────────────────────┤
│  rv-sidebar   │  rv-detail                      │
│               │                                 │
│  object nav   │  [Files tab content]            │
│  (scrollable) │  or [Reviews tab]               │
│               │  or [Audit tab]                 │
│  [+ Add File] │                                 │
└───────────────┴─────────────────────────────────┘
```

### 1b. Datastar signals

```html
<div class="rv-shell" data-signals="{mainTab:'files', objIdx:0, showUpload:false, showMeta:-1}">
```

| Signal | Type | Purpose |
|---|---|---|
| `mainTab` | string | Active tab: `'files'`, `'reviews'`, `'audit'` |
| `objIdx` | int | Index of selected object in the left nav |
| `showUpload` | bool | Show inline upload form instead of object detail |
| `showMeta` | int | Index of object with metadata form open (`-1` = none) |

### 1c. Left nav — object list

Each row:
- File-type icon badge (see section 3)
- Filename (truncated)
- Secondary line: version, output type, statbarn, size
- State dot (see section 3)
- `data-on-click` sets `objIdx`
- `data-class-rv-file-selected` highlights active row

Footer: `+ Add File` button sets `showUpload = true`.

### 1d. Right panel — object detail

Content shown when `mainTab === 'files'` and `showUpload === false`:

- Object properties grid (type, statbarn, version, size, uploaded, SHA-256)
- Researcher metadata section (title, description, justification, suppression notes) — shown only if metadata exists
- Edit metadata button: sets `showMeta` to the object index, revealing an inline form
- Inline metadata form (when `showMeta === objIdx`): `POST /ui/requests/{id}/objects/{oid}/metadata` with all four fields including `title`; cancels by setting `showMeta = -1`
- File preview section (from `preview_service`)
- Agent assessment section (rule checks, disclosure risk, recommendation, narrative) — shown if agent review exists

### 1e. Right panel — inline upload form

Shown when `showUpload === true`. Replaces the separate `/ui/requests/{id}/upload` GET page (that page remains for backwards compatibility but the detail page links here instead).

Form fields:
- File input
- Output type (combobox)
- Statbarn category (text)
- Title (text, optional)
- Description (text, optional)
- Researcher justification (textarea, optional)
- Suppression notes (textarea, optional)

`POST /ui/requests/{id}/upload` — existing endpoint extended to accept and save all metadata fields to `OutputObjectMetadata` at upload time.

### 1f. Tabs

Tab labels: **Files**, **Reviews**, **Audit**. Controlled by `mainTab` signal.

- **Files** tab: left nav + object detail (default)
- **Reviews** tab: list of reviews with reviewer, decision, summary, timestamp
- **Audit** tab: paginated audit event log

---

## 2. Checker review form

### 2a. Upgrade to Datastar v1 API

Replace `data-store='{...}'` with `data-signals='{selected: 0}'`.

Replace `$selected` references with `selected.value`.

### 2b. Layout

Identical shell to researcher detail page. Left nav shows all objects. Right panel shows per-object detail and decision controls.

### 2c. Metadata section — title

Add `title` to the researcher metadata grid (between the heading and description):

```html
{% if meta.title %}
<div class="rv-meta-label">Title</div>
<div class="rv-meta-value">{{ meta.title }}</div>
{% endif %}
```

### 2d. Per-object decision buttons

Three buttons instead of two: **Approve**, **Request changes**, **Reject**.

Each sets a hidden input `obj_{id}_decision` to the corresponding value string.

### 2e. Footer form

`summary` field is required (add `required` attribute).

---

## 3. CSS additions

### 3a. File-type icon palette

```css
.ft-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.2rem;
  height: 2.2rem;
  border-radius: 0.3rem;
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  flex-shrink: 0;
  color: #fff;
}
.ft-tabular { background: var(--color-tabular, #2563eb); }
.ft-figure  { background: var(--color-figure,  #7c3aed); }
.ft-code    { background: var(--color-code,    #0891b2); }
.ft-report  { background: var(--color-report,  #0d9488); }
.ft-model   { background: var(--color-model,   #b45309); }
.ft-other   { background: var(--color-other,   #64748b); }
```

Label text per type: `CSV`, `IMG`, `CODE`, `DOC`, `MDL`, `OTH`. Falls back to first 3 chars of extension uppercased.

### 3b. Object state dot

```css
.obj-state-dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-PENDING            { background: var(--color-pending,  #94a3b8); }
.dot-APPROVED           { background: var(--color-approved, #16a34a); }
.dot-REJECTED           { background: var(--color-rejected, #dc2626); }
.dot-CHANGES_REQUESTED  { background: var(--color-changes,  #d97706); }
.dot-SUPERSEDED         { background: var(--color-superseded, #94a3b8); opacity: 0.4; }
```

---

## 4. Backend changes

### 4a. `_build_request_detail_ctx` helper

Extract a shared `async def _build_request_detail_ctx(request, request_id, auth, session) -> dict` from the existing `request_detail` handler. Returns the full template context dict including `objects`, `object_metadata`, `reviews`, `audit_events`, `object_previews`, `agent_assessments`, `agent_review`.

Used by:
- `request_detail` GET
- (Optionally) after metadata save POST to avoid redirect then re-fetch

### 4b. `object_upload` POST — save metadata at upload time

Accept additional optional form fields:

```python
title: Annotated[str, Form()] = ""
description: Annotated[str, Form()] = ""
researcher_justification: Annotated[str, Form()] = ""
suppression_notes: Annotated[str, Form()] = ""
```

If any are non-empty, create `OutputObjectMetadata` at upload time.

S3 upload gated on `settings.s3_endpoint_url or settings.s3_access_key_id` (not on `dev_auth_bypass`).

### 4c. `object_metadata_save` POST — add `title` field

```python
title: Annotated[str, Form()] = ""
```

Set `meta.title = title` before commit.

---

## 5. Test plan

| Test | Method | Pass criteria |
|---|---|---|
| Detail page has `data-signals` with `mainTab`/`objIdx` | `test_detail_page_has_datastar_signals` | `data-signals`, `mainTab`, `objIdx` in HTML |
| Uploaded file appears in left nav | `test_detail_page_shows_object_in_nav` | filename in HTML |
| Reviews and Audit tab labels rendered | `test_detail_page_shows_tabs` | `Reviews`, `Audit` in HTML |
| CSV file renders `ft-tabular` icon | `test_detail_page_ft_icon_for_csv` | `ft-tabular` or `CSV` in HTML |
| `title` saved via metadata POST | `test_metadata_save_stores_title` | title visible on detail page |
| `title` persists to JSON API | `test_metadata_save_title_via_api` | `GET /requests/{id}/objects/{oid}/metadata` returns `title` |
| Empty `title` accepted | `test_metadata_save_title_empty_is_ok` | 303 redirect |
| Checker form shows file in left nav | `test_review_form_left_nav_has_object` | filename and `rv-sidebar` in HTML |
| Checker form shows `title` in metadata | `test_review_form_shows_title_in_metadata` | title and description in HTML |
| Checker form uses `data-signals` not `data-store` | `test_review_form_signals_attribute` | `data-signals` present, `data-store=` absent |

10 new tests in `tests/test_ui_iter20.py`. Total: 262.

---

## 6. New/modified files

```
src/trevor/routers/ui.py                               # MODIFIED — _build_request_detail_ctx helper,
                                                       #   object_upload metadata fields, metadata_save title,
                                                       #   dead code removed
src/trevor/templates/researcher/request_detail.html    # REWRITTEN — two-panel layout, Datastar signals
src/trevor/templates/checker/review_form.html          # REWRITTEN — left nav, data-signals, title in metadata
src/trevor/templates/components/object_nav_item.html   # NEW — reusable nav row partial
src/trevor/templates/components/file_preview.html      # MODIFIED — uses preview dict from context
src/trevor/static/style.css                            # MODIFIED — ft-icon palette, obj-state-dot
docs/spec/iterations/iteration-20-spec.md              # NEW — this file
docs/spec/iteration-plan.md                            # MODIFIED — iteration 20 entry
docs/architecture.md                                   # MODIFIED — two-panel UI section, file preview section,
                                                       #   URL_EXPIRY_WARNING_HOURS env var, 262 tests
AGENTS.md                                              # MODIFIED — 262 tests
README.md                                              # MODIFIED — 262 tests
tests/test_ui_iter20.py                                # NEW — 10 tests
```

---

## 7. Implementation order

1. Append CSS additions (`ft-icon`, `obj-state-dot`) to `static/style.css`
2. Create `components/object_nav_item.html` partial
3. Extract `_build_request_detail_ctx` helper in `ui.py`
4. Rewrite `researcher/request_detail.html` with two-panel layout
5. Extend `object_upload` POST to accept and save metadata fields
6. Extend `object_metadata_save` POST to accept `title`
7. Rebuild `checker/review_form.html` — left nav, `data-signals`, title in metadata, three-button decision
8. Write `tests/test_ui_iter20.py`
9. Run ruff + pytest; fix failures
10. Update `docs/architecture.md`, `AGENTS.md`, `README.md`
11. Commit
