"""Fast text reading: block-boundary prefixes for very large documents."""

from pathlib import Path

import pytest

from app import md_converter as mc


def test_split_cuts_at_a_blank_line_not_a_line_count():
    text = "".join(f"para {i} text\n\n" for i in range(400))

    prefix, prefix_bytes = mc.fast_preview_split(text, max_bytes=100)

    assert prefix is not text
    assert prefix.endswith("\n\n")
    assert prefix_bytes == len(prefix.encode("utf-8"))
    assert text.startswith(prefix)
    # The cut lands after a completed paragraph, never inside one.
    assert prefix.rstrip("\n").splitlines()[-1].startswith("para ")


@pytest.mark.parametrize("construct", [
    "```python\nfirst\n\nsecond\n```",
    "~~~\nfirst\n\nsecond\n~~~",
    "$$\na = 1\n\nb = 2\n$$",
    "<details>\n<summary>s</summary>\n\ninner\n\n</details>",
])
def test_split_never_cuts_inside_a_multi_line_construct(construct):
    text = f"intro\n\n{construct}\n\nafter\n\n"

    # The byte budget runs out inside the construct, so the first blank line
    # the scan meets is the one *inside* it -- that must not be a cut point.
    prefix, _bytes = mc.fast_preview_split(text, max_bytes=8)

    assert prefix is not text
    assert prefix == f"intro\n\n{construct}\n\n"


def test_split_never_cuts_inside_front_matter():
    text = "---\ntitle: x\n\ntags: [a]\n---\n\nafter\n\n"

    prefix, _bytes = mc.fast_preview_split(text, max_bytes=5)

    assert prefix == "---\ntitle: x\n\ntags: [a]\n---\n\n"


def test_split_gives_up_on_one_giant_block():
    text = "word " * 200_000  # a single paragraph, no blank line anywhere

    prefix, _bytes = mc.fast_preview_split(text, max_bytes=1000)

    assert prefix is text  # caller must not claim a partial render


def test_partial_body_is_labelled_and_is_a_real_prefix():
    text = "".join(f"## Heading {i}\n\nbody {i}\n\n" for i in range(2000))
    total = len(text.encode("utf-8"))

    result = mc.render_partial_body(text, total, max_bytes=2000)

    assert result is not None
    rendered, prefix_bytes = result
    assert 0 < prefix_bytes < total
    assert "快速文字閱讀" in rendered.body
    assert "partial-preview-notice" in rendered.body
    assert "Heading 0" in rendered.body
    assert "Heading 1999" not in rendered.body
    assert rendered.headings[0] == (2, "Heading 0", "heading-0")


def test_partial_body_refuses_when_no_safe_boundary_exists():
    assert mc.render_partial_body("word " * 100_000, max_bytes=500) is None


def test_partial_notice_states_the_covered_range():
    html = mc.partial_preview_notice_html(500 * 1024, 5 * 1024 * 1024)

    assert "10%" in html
    assert "5.0 MB" in html
    assert "目錄、搜尋與行號定位只涵蓋已載入的區塊" in html


def test_partial_prefix_output_matches_full_render_of_the_same_prefix():
    text = "".join(f"### S{i}\n\n- a\n- b\n\n| x | y |\n| --- | --- |\n| 1 | 2 |\n\n"
                   for i in range(500))
    result = mc.render_partial_body(text, max_bytes=3000)

    assert result is not None
    rendered, prefix_bytes = result
    prefix, _ = mc.fast_preview_split(text, max_bytes=3000)
    expected = mc.render_body(prefix)
    notice = mc.partial_preview_notice_html(prefix_bytes,
                                            len(text.encode("utf-8")))
    assert rendered.body == notice + expected.body
    assert rendered.headings == expected.headings


def test_worker_emits_partial_then_full_for_a_large_file(qapp, tmp_path, monkeypatch):
    from app import renderer

    path = tmp_path / "big.md"
    path.write_text("".join(f"## H{i}\n\ntext {i}\n\n" for i in range(600)),
                    encoding="utf-8")
    monkeypatch.setattr(mc, "FAST_PREVIEW_MIN_BYTES", 1000)
    monkeypatch.setattr(mc, "FAST_PREVIEW_PREFIX_BYTES", 500)
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1 << 40)  # stay in-process
    partials, finals = [], []
    worker = renderer._MarkdownRenderWorker(3, path=path, theme="light")
    worker.signals.partial_ready.connect(lambda *a: partials.append(a))
    worker.signals.ready.connect(lambda *a: finals.append(a))

    worker.run()

    assert len(partials) == 1 and len(finals) == 1
    assert '<aside class="partial-preview-notice"' in partials[0][2]
    assert "H599" not in partials[0][2]
    assert '<aside class="partial-preview-notice"' not in finals[0][2]
    assert "H599" in finals[0][2]


def test_worker_skips_the_partial_pass_for_a_normal_file(qapp, tmp_path):
    from app import renderer

    path = tmp_path / "small.md"
    path.write_text("# Small\n\nbody\n", encoding="utf-8")
    partials, finals = [], []
    worker = renderer._MarkdownRenderWorker(1, path=path, theme="light")
    worker.signals.partial_ready.connect(lambda *a: partials.append(a))
    worker.signals.ready.connect(lambda *a: finals.append(a))

    worker.run()

    assert partials == []
    assert len(finals) == 1


def test_worker_skips_the_partial_pass_when_the_full_body_is_cached(
    qapp, tmp_path, monkeypatch
):
    from app import renderer

    path = tmp_path / "big.md"
    path.write_text("".join(f"## H{i}\n\ntext {i}\n\n" for i in range(600)),
                    encoding="utf-8")
    monkeypatch.setattr(mc, "FAST_PREVIEW_MIN_BYTES", 1000)
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1 << 40)
    mc.convert_body(path)  # first open populates the cache
    partials = []
    worker = renderer._MarkdownRenderWorker(2, path=path, theme="light")
    worker.signals.partial_ready.connect(lambda *a: partials.append(a))

    worker.run()

    assert partials == []


def test_cancelled_worker_emits_no_partial_view(qapp, tmp_path, monkeypatch):
    import threading

    from app import renderer

    path = tmp_path / "big.md"
    path.write_text("".join(f"## H{i}\n\ntext {i}\n\n" for i in range(600)),
                    encoding="utf-8")
    monkeypatch.setattr(mc, "FAST_PREVIEW_MIN_BYTES", 1000)
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1 << 40)
    cancel = threading.Event()
    cancel.set()
    delivered = []
    worker = renderer._MarkdownRenderWorker(4, path=path, theme="light",
                                            cancel=cancel)
    worker.signals.partial_ready.connect(lambda *a: delivered.append(a))
    worker.signals.ready.connect(lambda *a: delivered.append(a))

    worker.run()

    assert delivered == []


class _StubPage:
    def __init__(self):
        self.pages = []
        self.pdfs = []

    def setHtml(self, html, base_url=None):
        self.pages.append(html)

    def printToPdf(self, path, layout):
        self.pdfs.append(path)


class _StubRenderer:
    """Only the state the two ready-handlers touch, so they can be unit tested
    without starting a real WebEngine view."""

    from app.renderer import RendererView as _R
    _stale_render = _R._stale_render
    _on_file_partial_ready = _R._on_file_partial_ready
    _on_file_render_ready = _R._on_file_render_ready
    is_partial_preview = _R.is_partial_preview
    export_pdf = _R.export_pdf
    del _R

    def __init__(self, path, generation=7):
        self._render_generation = generation
        self._current_path = path
        self._partial_preview_active = False
        self._partial_restore = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_find = None
        self._scroll_y = 0
        self._on_headings_ready = None
        self._pending_export = None
        self._pdf_callback = None
        self._page = _StubPage()

    def page(self):
        return self._page


def test_partial_page_does_not_swallow_a_pending_search_or_scroll(tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# x\n", encoding="utf-8")
    view = _StubRenderer(path)
    view._pending_scroll = 4200
    view._pending_scroll_generation = 7
    view._pending_find = (7, "needle")

    view._on_file_partial_ready(7, path, "<html><head></head><body>p</body></html>", [])
    assert view.is_partial_preview()
    # The partial page's own load consumes them (it looks like a normal load).
    view._pending_scroll = None
    view._pending_scroll_generation = None
    view._pending_find = None

    view._on_file_render_ready(7, path, "<html><head></head><body>full</body></html>", [])

    assert not view.is_partial_preview()
    assert view._pending_find == (7, "needle")
    assert view._pending_scroll == 4200
    assert view._pending_scroll_generation == 7
    assert "window.scrollTo(0, 4200)" in view._page.pages[-1]


def test_full_page_keeps_the_reader_where_they_scrolled_in_the_prefix(tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# x\n", encoding="utf-8")
    view = _StubRenderer(path)

    view._on_file_partial_ready(7, path, "<html><head></head><body>p</body></html>", [])
    view._scroll_y = 900  # the reader scrolled inside the prefix

    view._on_file_render_ready(7, path, "<html><head></head><body>full</body></html>", [])

    assert "window.scrollTo(0, 900)" in view._page.pages[-1]


def test_stale_partial_view_never_touches_the_page(tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# x\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("# y\n", encoding="utf-8")
    view = _StubRenderer(path)

    view._on_file_partial_ready(6, path, "<html></html>", [])       # old generation
    view._on_file_partial_ready(7, other, "<html></html>", [])      # other document

    assert view._page.pages == []
    assert not view.is_partial_preview()


def test_pdf_export_waits_for_the_full_document(tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# x\n", encoding="utf-8")
    view = _StubRenderer(path)
    view._on_file_partial_ready(7, path, "<html><head></head><body>p</body></html>", [])

    view.export_pdf(tmp_path / "out.pdf", None, object())

    assert view._page.pdfs == []          # never export the truncated prefix
    assert view._pending_export is not None
