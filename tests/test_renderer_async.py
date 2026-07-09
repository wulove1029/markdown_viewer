from pathlib import Path

from app.renderer import (
    _MarkdownRenderWorker,
    _html_with_render_generation,
    _pending_scroll_target,
)


def test_markdown_file_worker_returns_generation_and_headings(qapp, tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Async\n\nbody", encoding="utf-8")
    results = []

    worker = _MarkdownRenderWorker(42, path=path, theme="light")
    worker.signals.ready.connect(lambda *args: results.append(args))
    worker.run()

    generation, source, html, headings = results[0]
    assert generation == 42
    assert Path(source) == path
    assert "<h1 id=\"async\">Async</h1>" in html
    assert headings == [(1, "Async", "async")]


def test_markdown_text_worker_returns_preview_html(qapp):
    results = []

    worker = _MarkdownRenderWorker(
        7,
        text="## Draft",
        theme="dark",
        title="live-preview",
    )
    worker.signals.ready.connect(lambda *args: results.append(args))
    worker.run()

    generation, source, html, headings = results[0]
    assert generation == 7
    assert source is None
    assert "live-preview" in html
    assert "theme-dark" in html
    assert headings == [(2, "Draft", "draft")]


def test_pending_scroll_survives_loading_page_without_generation():
    target, pending_scroll, pending_generation = _pending_scroll_target(
        pending_scroll=1500,
        pending_generation=5,
        loaded_generation=None,
    )

    assert target is None
    assert pending_scroll == 1500
    assert pending_generation == 5


def test_pending_scroll_consumed_only_by_matching_final_generation():
    target, pending_scroll, pending_generation = _pending_scroll_target(
        pending_scroll=1500,
        pending_generation=5,
        loaded_generation=4,
    )

    assert target is None
    assert pending_scroll == 1500
    assert pending_generation == 5

    target, pending_scroll, pending_generation = _pending_scroll_target(
        pending_scroll=pending_scroll,
        pending_generation=pending_generation,
        loaded_generation="5",
    )

    assert target == 1500
    assert pending_scroll is None
    assert pending_generation is None


def test_final_markdown_html_gets_generation_marker():
    html = "<html><head><title>x</title></head><body>doc</body></html>"

    marked = _html_with_render_generation(html, 12)

    assert '<meta name="markdown-viewer-render-generation" content="12">' in marked
    assert marked.index("markdown-viewer-render-generation") < marked.index("</head>")
