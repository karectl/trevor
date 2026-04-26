"""Tests for preview_service.py (iteration 18)."""

from __future__ import annotations

import csv
import io

from trevor.services.preview_service import MAX_PREVIEW_BYTES, render_preview

# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _make_csv(rows: list[list[str]], sep: str = ",") -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=sep)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode()


def test_csv_renders_table():
    content = _make_csv([["name", "value"], ["alpha", "1"], ["beta", "2"]])
    result = render_preview("data.csv", content)
    assert result is not None
    assert result["preview_type"] == "html_table"
    assert "<table" in result["preview_content"]
    assert "alpha" in result["preview_content"]


def test_tsv_renders_table():
    content = _make_csv([["a", "b"], ["1", "2"]], sep="\t")
    result = render_preview("data.tsv", content)
    assert result is not None
    assert result["preview_type"] == "html_table"


def test_csv_caps_at_max_rows():
    """Only MAX_ROWS rows are included in the preview."""
    from trevor.services.preview_service import MAX_ROWS

    header = ["col"]
    rows = [header] + [[str(i)] for i in range(MAX_ROWS + 10)]
    content = _make_csv(rows)
    result = render_preview("big.csv", content)
    assert result is not None
    # count <tr> tags — should be MAX_ROWS body rows + 1 header
    tr_count = result["preview_content"].count("<tr>")
    assert tr_count <= MAX_ROWS + 1


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def test_markdown_renders_html():
    content = b"# Hello\n\nSome **bold** text."
    result = render_preview("notes.md", content)
    assert result is not None
    assert result["preview_type"] == "markdown"
    assert "<h1>" in result["preview_content"]
    assert "<strong>" in result["preview_content"]


def test_markdown_sanitised():
    content = b"<script>alert('xss')</script> safe text"
    result = render_preview("notes.md", content)
    assert result is not None
    assert "<script>" not in result["preview_content"]


# ---------------------------------------------------------------------------
# Code / plain text
# ---------------------------------------------------------------------------


def test_python_code_highlighted():
    content = b"def hello():\n    return 'world'\n"
    result = render_preview("script.py", content)
    assert result is not None
    assert result["preview_type"] == "code"
    assert "hello" in result["preview_content"]


def test_json_renders_code():
    content = b'{"key": "value"}'
    result = render_preview("data.json", content)
    assert result is not None
    assert result["preview_type"] == "code"


def test_plain_text_renders_code():
    content = b"Just some plain text."
    result = render_preview("notes.txt", content)
    assert result is not None
    # txt might map to TextLexer → still returns "code" type
    assert result["preview_type"] == "code"


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


def test_image_returns_data_uri():
    # Minimal valid 1x1 PNG
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
        b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
        b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    result = render_preview("photo.png", png_bytes)
    assert result is not None
    assert result["preview_type"] == "image"
    assert "data:image/png;base64," in result["preview_content"]


# ---------------------------------------------------------------------------
# Size limit
# ---------------------------------------------------------------------------


def test_too_large_returns_none():
    big = b"x" * (MAX_PREVIEW_BYTES + 1)
    result = render_preview("huge.csv", big)
    assert result is None


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_corrupt_csv_falls_back_to_text():
    # Content that is not valid CSV at all — binary garbage
    content = bytes(range(256)) * 10
    result = render_preview("bad.csv", content)
    # Should either parse something or fall back gracefully (not raise)
    # result may be None or a dict; must not raise
    # If it falls back, preview_type is "text"
    if result is not None:
        assert result["preview_type"] in {"html_table", "text"}


def test_unknown_extension_renders_code():
    content = b"unknown format data here"
    result = render_preview("file.xyz", content)
    assert result is not None
    assert result["preview_type"] in {"code", "text"}
