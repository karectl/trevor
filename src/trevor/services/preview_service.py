"""Preview service — render file content to HTML for the review UI.

Supported formats:
- CSV/TSV: first 50 rows as HTML table (via polars)
- Parquet: first 50 rows as HTML table (via polars)
- Markdown: rendered to HTML (via mistune)
- Code / plain text: syntax-highlighted (via pygments)
- Images (PNG/JPEG/GIF/WebP): base64 data URI
- Fallback: raw text in <pre>

All HTML output is sanitised via nh3.
Files larger than MAX_PREVIEW_BYTES are skipped (returns None).
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path

import nh3

logger = logging.getLogger(__name__)

MAX_PREVIEW_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ROWS = 50

_CSV_EXTENSIONS = {".csv", ".tsv", ".tab"}
_PARQUET_EXTENSIONS = {".parquet", ".pq"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}

# mime types for images
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _safe_html(html: str) -> str:
    """Sanitise HTML, allowing a broad set of safe tags."""
    allowed_tags = {
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "strong",
        "em",
        "a",
        "br",
        "hr",
        "span",
        "div",
        "img",
    }
    allowed_attrs = {
        "a": {"href", "title", "target"},
        "img": {"src", "alt", "width", "height"},
        "code": {"class"},
        "span": {"class"},
        "div": {"class"},
        "td": {"class", "style"},
        "th": {"class", "style"},
        "table": {"class"},
    }
    return nh3.clean(html, tags=allowed_tags, attributes=allowed_attrs)


def _render_csv(content: bytes, suffix: str) -> str:
    """Render CSV/TSV to an HTML table (first MAX_ROWS rows)."""
    import polars as pl

    separator = "\t" if suffix in {".tsv", ".tab"} else ","
    try:
        df = pl.read_csv(BytesIO(content), separator=separator, n_rows=MAX_ROWS, ignore_errors=True)
    except Exception as exc:
        logger.debug("preview_service: CSV parse failed: %s", exc)
        raise
    return _dataframe_to_html(df)


def _render_parquet(content: bytes) -> str:
    """Render Parquet to an HTML table (first MAX_ROWS rows)."""
    import polars as pl

    df = pl.read_parquet(BytesIO(content)).head(MAX_ROWS)
    return _dataframe_to_html(df)


def _dataframe_to_html(df) -> str:  # noqa: ANN001
    """Convert a polars DataFrame to a safe HTML table string."""
    rows_html = []
    headers = df.columns
    header_row = "".join(f"<th>{h}</th>" for h in headers)
    rows_html.append(f"<thead><tr>{header_row}</tr></thead>")
    body_rows = []
    for row in df.iter_rows():
        cells = "".join(f"<td>{v}</td>" for v in row)
        body_rows.append(f"<tr>{cells}</tr>")
    rows_html.append(f"<tbody>{''.join(body_rows)}</tbody>")
    html = f'<table class="preview-table">{"".join(rows_html)}</table>'
    return _safe_html(html)


def _render_markdown(content: bytes) -> str:
    """Render Markdown to sanitised HTML."""
    import mistune

    md_text = content.decode("utf-8", errors="replace")
    html = mistune.html(md_text)
    return _safe_html(html)


def _render_code(content: bytes, suffix: str) -> str:
    """Syntax-highlight code using pygments."""
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import TextLexer, get_lexer_for_filename
    from pygments.util import ClassNotFound

    text = content.decode("utf-8", errors="replace")
    filename = f"file{suffix}" if suffix else "file.txt"
    try:
        lexer = get_lexer_for_filename(filename)
    except ClassNotFound:
        lexer = TextLexer()
    formatter = HtmlFormatter(nowrap=False, cssclass="highlight")
    highlighted = highlight(text, lexer, formatter)
    return _safe_html(highlighted)


def _render_image(content: bytes, suffix: str) -> str:
    """Render image as a base64 data URI <img> tag."""
    mime = _IMAGE_MIME.get(suffix, "image/png")
    b64 = base64.b64encode(content).decode()
    style = "max-width:100%;max-height:400px;"
    return f'<img src="data:{mime};base64,{b64}" alt="preview" style="{style}">'


def render_preview(filename: str, content: bytes) -> dict | None:
    """Render file content to a preview dict.

    Returns:
        dict with keys ``preview_type`` and ``preview_content``, or
        ``None`` if the file is too large or an error occurs.

    ``preview_type`` values: ``html_table``, ``markdown``, ``code``, ``image``, ``text``.
    """
    if len(content) > MAX_PREVIEW_BYTES:
        logger.debug("preview_service: %s too large (%d bytes), skipping", filename, len(content))
        return None

    suffix = Path(filename).suffix.lower()

    try:
        if suffix in _CSV_EXTENSIONS:
            html = _render_csv(content, suffix)
            return {"preview_type": "html_table", "preview_content": html}

        if suffix in _PARQUET_EXTENSIONS:
            html = _render_parquet(content)
            return {"preview_type": "html_table", "preview_content": html}

        if suffix in _MARKDOWN_EXTENSIONS:
            html = _render_markdown(content)
            return {"preview_type": "markdown", "preview_content": html}

        if suffix in _IMAGE_EXTENSIONS:
            html = _render_image(content, suffix)
            return {"preview_type": "image", "preview_content": html}

        # Everything else: try code highlighting
        html = _render_code(content, suffix)
        return {"preview_type": "code", "preview_content": html}

    except Exception as exc:
        logger.warning("preview_service: render failed for %s: %s", filename, exc)
        # Graceful degradation: plain text fallback
        text = content.decode("utf-8", errors="replace")
        return {"preview_type": "text", "preview_content": text[:4096]}
