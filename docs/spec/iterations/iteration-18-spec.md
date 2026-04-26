# Iteration 18 Spec — File Preview Rendering

## Goal

Implement server-side preview rendering for output objects so researchers and checkers can inspect file contents directly in the UI without downloading.

---

## Current state

| Component | Status |
|---|---|
| `templates/components/file_preview.html` | Exists — expects `preview_type`, `preview_content`, `preview_url` context vars |
| `mistune` dependency | Installed |
| `polars` dependency | Installed |
| `pygments` dependency | Installed |
| `nh3` dependency | **Not installed** |
| `storage.download_object()` | Implemented — returns bytes from S3 |
| Preview rendering service | **Missing** |
| UI router integration (request detail, review form) | Template references preview but no data passed |

---

## Scope

| Item | Decision |
|---|---|
| Rendering location | Server-side only. Python renders HTML fragments; Jinja template displays them. |
| Standalone viewer | Out of scope. Preview is embedded in request detail and review form views. |
| Streaming / lazy load | Out of scope (v1). Full render on page load. |
| Client-side rendering | None. No JS renderers. |
| Caching | Out of scope (v1). Re-render on each request. Can add Redis cache later. |

---

## 1. Dependency

### Add `nh3` to `pyproject.toml`

```toml
[project]
dependencies = [
    ...,
    "nh3>=0.2",
]
```

`nh3` is a Rust-based HTML sanitizer (successor to `bleach`). Used to strip dangerous tags/attributes from all rendered HTML before it reaches the template.

---

## 2. Preview service module

### File: `src/trevor/services/preview_service.py`

#### PreviewResult dataclass

```python
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class PreviewResult:
    """Result of rendering a file preview."""

    html: str
    """Sanitized HTML fragment ready for embedding."""

    preview_type: str
    """One of: html_table, markdown, code, image, text, metadata."""

    truncated: bool
    """True if content was truncated (e.g. table rows capped at 50)."""

    error: str | None = None
    """Non-None if preview could not be rendered (missing file, S3 error, etc.)."""
```

#### render_preview() signature

```python
async def render_preview(
    storage_key: str,
    filename: str,
    size_bytes: int | None,
    output_type: str | None,
    *,
    settings: Settings,
) -> PreviewResult:
    """Render a preview HTML fragment for the given output object.

    Args:
        storage_key: S3 key for the object.
        filename: Original filename (used for format detection).
        size_bytes: File size. If > MAX_PREVIEW_BYTES, returns metadata only.
        output_type: OutputObject.output_type hint (optional).
        settings: App settings (S3 config).

    Returns:
        PreviewResult with sanitized HTML ready for template embedding.
    """
```

#### Constants

```python
MAX_PREVIEW_BYTES: int = 10 * 1024 * 1024  # 10 MB
MAX_TABLE_ROWS: int = 50
MAX_TEXT_BYTES: int = 100 * 1024  # 100 KB for plain text / code preview
```

---

## 3. Supported formats

| Format | Detection | Renderer | Output | `preview_type` |
|---|---|---|---|---|
| CSV / TSV | `.csv`, `.tsv` extension | `polars.read_csv()` → `df.head(50).to_pandas().to_html()` | `<table>` | `html_table` |
| Parquet | `.parquet` extension | `polars.read_parquet()` → `df.head(50).to_pandas().to_html()` | `<table>` | `html_table` |
| Markdown | `.md` extension | `mistune.html(text)` | sanitized HTML | `markdown` |
| Python | `.py` extension | `pygments.highlight()` with `PythonLexer` | `<pre>` with spans | `code` |
| R | `.r`, `.R` extension | `pygments.highlight()` with `SLexer` | `<pre>` with spans | `code` |
| SAS | `.sas` extension | `pygments.highlight()` with `SASLexer` | `<pre>` with spans | `code` |
| Stata | `.do` extension | `pygments.highlight()` with `StataLexer` | `<pre>` with spans | `code` |
| SQL | `.sql` extension | `pygments.highlight()` with `SqlLexer` | `<pre>` with spans | `code` |
| Image | `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg` | base64 data URI (< 2 MB) or presigned URL (≥ 2 MB) | `<img>` | `image` |
| Plain text | `.txt`, `.log` | raw text in `<pre>`, HTML-escaped | `<pre>` | `text` |
| PDF | `.pdf` | not rendered | filename + size + download link | `metadata` |
| Other / fallback | any other extension | not rendered | filename + size + checksum | `metadata` |

### Format detection

```python
import pathlib

def _detect_format(filename: str) -> str:
    """Return format key based on file extension."""
    suffix = pathlib.PurePosixPath(filename).suffix.lower()
    FORMAT_MAP = {
        ".csv": "csv", ".tsv": "tsv",
        ".parquet": "parquet",
        ".md": "markdown",
        ".py": "python", ".r": "r",
        ".sas": "sas", ".do": "stata", ".sql": "sql",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".svg": "image",
        ".txt": "text", ".log": "text",
        ".pdf": "pdf",
    }
    return FORMAT_MAP.get(suffix, "other")
```

---

## 4. Rendering details

### 4.1 CSV / TSV

```python
import io
import polars as pl

def _render_table(data: bytes, filename: str) -> PreviewResult:
    separator = "\t" if filename.endswith(".tsv") else ","
    df = pl.read_csv(io.BytesIO(data), separator=separator, infer_schema_length=1000)
    truncated = len(df) > MAX_TABLE_ROWS
    html = df.head(MAX_TABLE_ROWS).to_pandas().to_html(
        index=False, classes="preview-table", border=0
    )
    return PreviewResult(
        html=_sanitize(html), preview_type="html_table", truncated=truncated
    )
```

### 4.2 Parquet

Same as CSV but uses `pl.read_parquet(io.BytesIO(data))`.

### 4.3 Markdown

```python
import mistune

def _render_markdown(data: bytes) -> PreviewResult:
    text = data.decode("utf-8", errors="replace")
    raw_html = mistune.html(text)
    return PreviewResult(
        html=_sanitize(raw_html), preview_type="markdown", truncated=False
    )
```

### 4.4 Code (Python, R, SAS, Stata, SQL)

```python
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name

LEXER_MAP = {
    "python": "python", "r": "splus", "sas": "sas",
    "stata": "stata", "sql": "sql",
}

def _render_code(data: bytes, fmt: str) -> PreviewResult:
    text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    truncated = len(data) > MAX_TEXT_BYTES
    lexer = get_lexer_by_name(LEXER_MAP[fmt])
    formatter = HtmlFormatter(nowrap=False, cssclass="highlight")
    html = highlight(text, lexer, formatter)
    return PreviewResult(
        html=_sanitize(html), preview_type="code", truncated=truncated
    )
```

### 4.5 Images

- Files ≤ 2 MB: read bytes, base64-encode, return `<img src="data:image/png;base64,...">`.
- Files > 2 MB but ≤ 10 MB: generate a presigned GET URL via `storage.generate_presigned_url()` and return `<img src="...">`.
- SVG: base64-encode always (never inline raw SVG — XSS risk).

### 4.6 Plain text

```python
def _render_text(data: bytes) -> PreviewResult:
    text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    import html as html_mod
    escaped = html_mod.escape(text)
    truncated = len(data) > MAX_TEXT_BYTES
    return PreviewResult(
        html=f"<pre class=\"preview-text\">{escaped}</pre>",
        preview_type="text", truncated=truncated,
    )
```

### 4.7 PDF / Other

Return metadata-only preview:

```python
def _render_metadata(filename: str, size_bytes: int | None) -> PreviewResult:
    size_str = f"{size_bytes:,} bytes" if size_bytes else "unknown size"
    html = f'<div class="preview-meta"><strong>{html_escape(filename)}</strong> — {size_str}</div>'
    return PreviewResult(html=html, preview_type="metadata", truncated=False)
```

---

## 5. Size limits

- Files with `size_bytes > MAX_PREVIEW_BYTES` (10 MB): skip download entirely, return metadata-only preview.
- Plain text and code: truncate to first `MAX_TEXT_BYTES` (100 KB), set `truncated=True`.
- Tables (CSV/TSV/Parquet): cap at `MAX_TABLE_ROWS` (50 rows), set `truncated=True` if exceeded.
- Images > 2 MB: use presigned URL instead of base64 (avoid bloating HTML response).

---

## 6. HTML sanitization

All rendered HTML passes through `nh3.clean()` before being stored in `PreviewResult.html`.

```python
import nh3

ALLOWED_TAGS = {
    "table", "thead", "tbody", "tr", "th", "td",
    "pre", "code", "span", "div", "p", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "a", "ul", "ol", "li",
    "img", "blockquote",
}
ALLOWED_ATTRIBUTES = {
    "*": {"class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "style"},
    "td": {"class"},
    "th": {"class"},
}

def _sanitize(html: str) -> str:
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
```

This prevents:
- `<script>` injection from malicious markdown or CSV cell contents.
- `onerror`/`onload` event handlers on `<img>` tags.
- `javascript:` URIs in `<a href>`.
- Raw SVG with embedded scripts.

---

## 7. S3 access and error handling

`render_preview()` calls `storage.download_object(settings, storage_key)` to fetch file bytes.

### Dev mode without S3

When `DEV_AUTH_BYPASS=true` and S3 is not configured (or `S3_ENDPOINT_URL` is empty), `download_object()` may raise. The preview service catches all exceptions and returns a graceful fallback:

```python
async def render_preview(...) -> PreviewResult:
    # Size check first (no download needed)
    if size_bytes and size_bytes > MAX_PREVIEW_BYTES:
        return _render_metadata(filename, size_bytes)

    try:
        data = await download_object(settings, storage_key)
    except Exception:
        return PreviewResult(
            html='<div class="preview-unavailable">Preview not available</div>',
            preview_type="metadata",
            truncated=False,
            error="Could not fetch file from storage",
        )

    fmt = _detect_format(filename)
    # ... dispatch to renderer
```

No exception from the preview service should propagate to the caller. All failures produce a valid `PreviewResult` with the `error` field set.

---

## 8. Integration with UI router

### Modified: `src/trevor/routers/ui.py`

#### Request detail view (`GET /ui/requests/{id}`)

After loading the request and its objects, call `render_preview()` for each object and pass results to the template:

```python
from trevor.services.preview_service import render_preview

# In the request detail handler:
previews: dict[str, PreviewResult] = {}
for obj in request.output_objects:
    previews[str(obj.id)] = await render_preview(
        storage_key=obj.storage_key,
        filename=obj.filename,
        size_bytes=obj.size_bytes,
        output_type=obj.output_type,
        settings=settings,
    )
# Pass previews dict to template context
```

#### Review form view (`GET /ui/review/{id}`)

Same pattern — render previews for all objects in the request under review.

---

## 9. Changes to templates

### Modified: `src/trevor/templates/components/file_preview.html`

Update to use `PreviewResult` fields and handle the `truncated` flag and `error`:

```html
<div class="preview-panel" id="preview-{{ object_id }}">
  {% if preview.error %}
  <div class="preview-unavailable">{{ preview.error }}</div>
  {% elif preview.preview_type == "image" %}
  {{ preview.html|safe }}
  {% elif preview.preview_type in ("html_table", "markdown", "code", "text", "metadata") %}
  {{ preview.html|safe }}
  {% else %}
  <div class="preview-unavailable">No preview available</div>
  {% endif %}

  {% if preview.truncated %}
  <div class="preview-truncated">Preview truncated. Download the file to see full content.</div>
  {% endif %}
</div>
```

### Modified: `src/trevor/static/style.css`

Add Pygments CSS classes and preview styling:

```css
.preview-panel { margin: 0.5rem 0; }
.preview-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.preview-table th, .preview-table td { border: 1px solid var(--border); padding: 0.25rem 0.5rem; text-align: left; }
.preview-text { max-height: 400px; overflow: auto; font-size: 0.85rem; }
.preview-unavailable { color: var(--muted); font-style: italic; padding: 0.5rem; }
.preview-truncated { color: var(--muted); font-size: 0.8rem; margin-top: 0.25rem; }
.preview-meta { padding: 0.5rem; }
/* Pygments highlight classes — use HtmlFormatter(cssclass="highlight") */
.highlight pre { margin: 0; max-height: 400px; overflow: auto; font-size: 0.85rem; }
```

---

## 10. Test plan

### File: `tests/test_preview.py`

| Test | What it verifies |
|---|---|
| `test_render_csv` | CSV bytes → HTML table with correct row count |
| `test_render_tsv` | TSV bytes → HTML table, tab separator detected |
| `test_render_parquet` | Parquet bytes → HTML table |
| `test_render_csv_truncation` | CSV with 100 rows → table has 50 rows, `truncated=True` |
| `test_render_markdown` | Markdown → HTML with headings, links |
| `test_render_python` | Python source → highlighted `<pre>` |
| `test_render_r_code` | R source → highlighted `<pre>` |
| `test_render_sql` | SQL source → highlighted `<pre>` |
| `test_render_text` | Plain text → escaped `<pre>` |
| `test_render_image_small` | Small PNG → base64 `<img>` tag |
| `test_render_pdf` | PDF filename → metadata-only preview |
| `test_render_unknown` | `.xyz` extension → metadata-only |
| `test_size_limit_skip` | `size_bytes=20_000_000` → metadata-only, no download attempted |
| `test_code_truncation` | 200 KB Python file → truncated to 100 KB, `truncated=True` |
| `test_sanitize_markdown_xss` | Markdown with `<script>alert(1)</script>` → script tag stripped |
| `test_sanitize_csv_xss` | CSV with `<img onerror=alert(1)>` cell → attribute stripped |
| `test_s3_failure_graceful` | Mock `download_object` raising → returns "Preview not available" |
| `test_binary_garbage` | Random bytes with `.csv` extension → error result, no crash |
| `test_format_detection` | Verify `_detect_format()` for all supported extensions |

Tests call the rendering functions directly (unit tests), not through the HTTP layer. S3 download is mocked via `unittest.mock.AsyncMock`.

---

## 11. New and modified files

| File | Action |
|---|---|
| `src/trevor/services/preview_service.py` | **New** — core rendering logic |
| `src/trevor/routers/ui.py` | **Modified** — call `render_preview()` in detail + review views |
| `src/trevor/templates/components/file_preview.html` | **Modified** — use `PreviewResult` fields |
| `src/trevor/static/style.css` | **Modified** — add preview + Pygments styles |
| `tests/test_preview.py` | **New** — unit tests for all renderers |
| `pyproject.toml` | **Modified** — add `nh3>=0.2` dependency |

---

## 12. Implementation order

1. Add `nh3` dependency to `pyproject.toml`, run `uv sync`
2. Create `src/trevor/services/preview_service.py` — `PreviewResult`, `_detect_format()`, `_sanitize()`
3. Implement renderers: `_render_table`, `_render_markdown`, `_render_code`, `_render_text`, `_render_image`, `_render_metadata`
4. Implement `render_preview()` — size guard, S3 download, format dispatch, error handling
5. Write `tests/test_preview.py` — all unit tests, verify passing
6. Update `src/trevor/routers/ui.py` — integrate preview into request detail and review form
7. Update `src/trevor/templates/components/file_preview.html` — consume `PreviewResult`
8. Update `src/trevor/static/style.css` — add preview styles
9. Manual smoke test with local dev stack (CSV, markdown, Python file)
