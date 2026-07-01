"""Tests for Mermaid render HTML and template data."""

from app.mermaid_render import build_preview_html, mermaid_asset_exists
from app.mermaid_templates import (
    SNIPPETS,
    TEMPLATES,
    default_template,
    snippet_by_id,
    template_by_id,
)
from app.mermaid_format import format_mermaid_source


def test_templates_have_unique_ids_and_source():
    ids = [template.id for template in TEMPLATES]
    assert len(ids) == len(set(ids))
    assert default_template() in TEMPLATES
    for template in TEMPLATES:
        assert template.name
        assert template.source.strip()
        assert template_by_id(template.id) is template


def test_snippets_have_unique_ids_and_source():
    ids = [snippet.id for snippet in SNIPPETS]
    assert len(ids) == len(set(ids))
    for snippet in SNIPPETS:
        assert snippet.name
        assert snippet.source.strip()
        assert snippet_by_id(snippet.id) is snippet


def test_unknown_template_returns_none():
    assert template_by_id("no-such-template") is None
    assert snippet_by_id("no-such-snippet") is None


def test_preview_html_includes_mermaid_asset_when_available():
    html = build_preview_html("graph TD\nA-->B")
    if mermaid_asset_exists():
        assert "mermaid.min.js" in html
    assert "window.__mermaidStatus" in html
    assert "graph TD" in html


def test_preview_html_escapes_script_end_inside_source():
    html = build_preview_html('graph TD\nA["</script><script>alert(1)</script>"]')
    assert '<\\/script><script>alert(1)<\\/script>' in html
    assert 'A["</script><script>alert(1)</script>"]' not in html


def test_format_mermaid_source_cleans_safe_whitespace_only():
    src = "\n\nflowchart TD   \nA --> B\n\n\nB --> C   \n\n"
    assert format_mermaid_source(src) == "flowchart TD\nA --> B\n\nB --> C"
