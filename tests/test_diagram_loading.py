"""The Markdown and fragment pipelines choose explicit graph-loading policies."""

from app import md_converter
from app.fragment_render import FragmentRenderer
from app.md_converter import _mermaid_script, convert_text


def test_reading_graph_loader_preserves_full_source_and_uses_lazy_policy():
    source = "flowchart TD\n" + "\n".join(f"A{i} --> A{i + 1}" for i in range(220))
    html, _ = convert_text(f"```mermaid\n{source}\n```", theme="dark")
    assert '"theme": "dark", "eager": false' in html
    assert "IntersectionObserver" in html
    assert "A219 --&gt; A220" in html
    assert "mdDiagrams" in html


def test_fragment_rendering_is_eager_even_for_oversized_source():
    fragment = object.__new__(FragmentRenderer)
    html, selector = fragment._build_html("mermaid", "graph TD\nA-->B")
    assert selector == ".mermaid svg"
    assert '"eager": true' in html


def test_plain_documents_do_not_ship_diagram_controller():
    html, _ = convert_text("# Plain document")
    assert "window.mdDiagrams" not in html
    assert "window.mermaid.initialize" in _mermaid_script("light")


def test_missing_engine_still_reports_a_terminal_readable_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(md_converter, "_MERMAID_JS", tmp_path / "missing.js")
    script = _mermaid_script("light")
    assert "window.mdDiagrams" in script
    assert '<script src="' not in script
