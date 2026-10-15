"""Switch cancellation, loading hints, and browser-visible content contracts."""

import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app import md_converter
from app.renderer import RendererView


def _signal():
    values = []
    return SimpleNamespace(emit=lambda *args: values.append(args), values=values)


def _stub():
    calls = []
    timer = SimpleNamespace(
        start=lambda: calls.append("timer-start"), stop=lambda: calls.append("timer-stop")
    )
    page = SimpleNamespace(runJavaScript=lambda *args: calls.append(args), isLoading=lambda: False)
    stub = SimpleNamespace(
        _render_generation=5,
        _current_path=Path("a.md"),
        _loaded_markdown_generation=5,
        _render_cancel=threading.Event(),
        _pending_export=None,
        _pending_find=(5, "x"),
        _pending_scroll=120,
        _pending_scroll_generation=5,
        _pending_ratio=(5, 0.5),
        _partial_restore=None,
        _pending_text_base_url="file:///a",
        _scroll_y=55,
        _current_anchor="a",
        _source_line_reveal=(5, 1),
        _spy_timer=timer,
        _frame_request=None,
        _frame_timer=timer,
        _frame_polls=0,
        _loading_generation=5,
        _submitted_generation=5,
        _render_failed_generation=None,
        _finish_loading=lambda: calls.append("finish-loading"),
        _inject_annotations=lambda _ok: calls.append("annotations"),
        _schedule_content_frame=lambda *_args: calls.append("schedule-frame"),
        _partial_preview_active=False,
        _partial_scope_percent=0,
        _on_headings_ready=lambda hs: calls.append(hs),
        content_ready=_signal(),
        render_failed=_signal(),
        active_anchor_changed=_signal(),
        stop=lambda: calls.append("stop"),
        page=lambda: page,
    )
    stub._next_render_generation = lambda: RendererView._next_render_generation(stub)
    stub._stale_render = lambda gen, path: RendererView._stale_render(stub, gen, path)
    stub._remember_reading_position = lambda: None
    return stub, calls


def test_deactivate_cancels_worker_and_pending_navigation_and_export():
    view, calls = _stub()
    old_cancel = view._render_cancel
    exports = []
    view._pending_export = ("out.pdf", lambda *args: exports.append(args), None)
    RendererView.deactivate(view)
    assert old_cancel.is_set()
    assert view._render_generation == 6
    assert view._current_path is None
    assert view._pending_scroll is None and view._pending_find is None
    assert view._pending_ratio is None and view._source_line_reveal is None
    assert exports == [("out.pdf", False)]
    assert calls[-1] == "stop"
    RendererView._on_file_render_ready(view, 5, Path("a.md"), "stale", [])
    assert calls[-1] == "stop"


def test_stale_spy_and_reload_reply_do_not_touch_new_document():
    view, calls = _stub()
    view.load_file = lambda *args, **kwargs: calls.append("load-file")
    RendererView._on_spy_result(view, ["old-anchor", 900], 4, Path("a.md"))
    RendererView._reload_at_scroll(view, 900, 4, Path("a.md"))
    assert view._scroll_y == 55 and view._current_anchor == "a"
    assert calls == [] and view.active_anchor_changed.values == []


def test_partial_navigation_completion_cannot_be_mistaken_for_full_document():
    view, calls = _stub()
    RendererView._on_markdown_load_checked(
        view, 5, Path("a.md"), json.dumps({"generation": 5, "phase": "partial"})
    )
    assert calls == []
    assert view._pending_scroll == 120


def test_stale_frame_does_not_report_readable_content():
    view, _calls = _stub()
    stale_request = (4, Path("a.md"), False, 10)
    view._frame_request = (5, Path("a.md"), False, 11)
    RendererView._on_content_frame_checked(view, stale_request, True)
    assert view.content_ready.values == []
    RendererView._on_content_frame_checked(view, view._frame_request, True)
    assert view.content_ready.values == [(5, "a.md", False)]


def test_failed_preview_releases_loading_without_readable_signal():
    view, calls = _stub()
    view._render_failed_generation = 5
    view._pending_scroll = None
    view._pending_find = None
    view._source_line_reveal = None
    RendererView._on_markdown_load_checked(view, 5, Path("a.md"), 5)
    assert "finish-loading" in calls
    assert view.render_failed.values == [(5, "a.md")]
    assert "schedule-frame" not in calls


def test_old_stop_completion_does_not_release_loading_for_pending_parser():
    view, calls = _stub()
    view._submitted_generation = 4
    RendererView._on_failed_navigation(view, 5, Path("a.md"))
    assert calls == [] and view.render_failed.values == []


def test_reading_position_cache_keeps_only_recent_versions():
    view, _calls = _stub()
    view._reading_positions = OrderedDict()
    view._scroll_anchor = {"line": 40, "offset": -20}
    for index in range(40):
        view._current_path = Path(f"{index}.md")
        view._document_signature = (str(view._current_path), index)
        RendererView._remember_reading_position(view)
    assert len(view._reading_positions) == 32
    assert next(iter(view._reading_positions)) == "8.md"
    view._document_signature = ("39.md", 99)
    RendererView._remember_reading_position(view)
    assert len(view._reading_positions) == 32
    assert view._reading_positions["39.md"][0] == ("39.md", 99)


def test_partial_user_navigation_wins_over_original_restore(tmp_path):
    from tests.test_fast_preview import _StubRenderer

    path = tmp_path / "partial.md"
    path.write_text("# Partial", encoding="utf-8")
    view = _StubRenderer(path)
    view._pending_scroll = 9000
    view._pending_scroll_generation = 7
    view._on_file_partial_ready(7, path, "<html><head></head><body>prefix</body></html>", [], 10)
    view._partial_user_navigated = True
    view._scroll_y = 500
    view._scroll_anchor = {"line": 40, "offset": -20}
    view._on_file_render_ready(7, path, "<html><head></head><body>full</body></html>", [])
    assert "window.scrollTo(0, 500)" in view._page.pages[-1]
    assert '"line": 40' in view._page.pages[-1]


def _wait_until(qapp, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert condition()


def _evaluate(view, script):
    result = {}
    loop = QEventLoop()
    view.page().runJavaScript(script, lambda value: (result.update(value=value), loop.quit()))
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    assert "value" in result
    return result["value"]


_real = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1", reason="set RUN_WEBENGINE_TESTS=1"
)


@_real
def test_real_warm_switch_never_loads_placeholder_or_reparses_and_restores_scroll(
    qapp, tmp_path, monkeypatch
):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("# Alpha\n\n" + "Paragraph to read.\n\n" * 200, encoding="utf-8")
    b.write_text("# Beta\n\nOther content.", encoding="utf-8")
    view = RendererView()
    view.resize(800, 500)
    view.show()
    ready = []
    view.content_ready.connect(lambda *args: ready.append(args))
    loads = []
    view.page().loadStarted.connect(lambda: loads.append(view._render_generation))
    for path in (a, b):
        view.load_file(path)
        generation = view._render_generation
        _wait_until(qapp, lambda: any(row[0] == generation for row in ready))
    assert len(loads) <= 3  # constructor's home page plus one page per document
    loads.clear()
    monkeypatch.setattr(
        md_converter, "render_body", lambda *_args, **_kwargs: pytest.fail("warm tab reparsed")
    )
    view.load_file(a, scroll_y=900)
    generation = view._render_generation
    _wait_until(qapp, lambda: any(row[0] == generation for row in ready))
    assert loads == [generation]
    assert _evaluate(view, "document.querySelector('h1').textContent") == "Alpha"
    assert abs(_evaluate(view, "window.scrollY") - 900) <= 2
    assert not view._loading_shield.isVisible()


@_real
def test_real_loading_shield_delays_hint_and_cancels_without_old_callback(
    qapp, tmp_path, monkeypatch
):
    path = tmp_path / "slow.md"
    path.write_text("# Slow", encoding="utf-8")
    view = RendererView()
    view.resize(700, 400)
    view.show()
    monkeypatch.setattr(view, "_finish_load_file", lambda *_args: None)
    view.load_file(path)
    assert view._loading_shield.isVisible()
    assert not view._loading_shield._label.isVisible()
    assert view._loading_timer.interval() == 140
    _wait_until(qapp, lambda: view._loading_shield._label.isVisible())
    old_generation = view._render_generation
    view.deactivate()
    assert not view._loading_shield.isVisible()
    view._on_file_render_ready(old_generation, path, "<h1>Stale</h1>", [])
    assert view._current_path is None


@_real
def test_real_missing_file_reports_failure_and_clears_overlay(qapp, tmp_path):
    view = RendererView()
    view.resize(700, 400)
    view.show()
    ready, failed = [], []
    view.content_ready.connect(lambda *args: ready.append(args))
    view.render_failed.connect(lambda *args: failed.append(args))
    view.load_file(tmp_path / "missing.md")
    _wait_until(qapp, lambda: bool(failed))
    assert ready == []
    assert not view._loading_shield.isVisible()


@_real
def test_real_same_version_anchor_survives_width_change_and_invalidates_on_edit(qapp, tmp_path):
    a, b = tmp_path / "anchor.md", tmp_path / "other.md"
    a.write_text(
        "# Anchor\n\n" + ("A sentence long enough to wrap to another line. " * 8 + "\n\n") * 80,
        encoding="utf-8",
    )
    b.write_text("# Other", encoding="utf-8")
    view = RendererView()
    view.resize(850, 500)
    view.show()
    ready = []
    view.content_ready.connect(lambda *args: ready.append(args))

    def load(path, scroll_y=None):
        view.load_file(path, scroll_y=scroll_y)
        generation = view._render_generation
        _wait_until(qapp, lambda: any(row[0] == generation for row in ready))

    load(a, 1800)
    view._poll_active_heading()
    _wait_until(qapp, lambda: view._scroll_anchor is not None)
    position = dict(view._scroll_anchor)
    pixel = view._scroll_y
    load(b)
    view.resize(500, 500)
    load(a, pixel)
    top = _evaluate(
        view,
        f"document.querySelector('[data-src-start=\"{position['line']}\"]').getBoundingClientRect().top",
    )
    assert abs(top - position["offset"]) <= 3
    assert view._pending_reading_anchor == position
    load(b)
    a.write_text(a.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    load(a, pixel)
    assert view._pending_reading_anchor is None


@_real
def test_real_rapid_switch_cancels_obsolete_conversion(qapp, tmp_path, monkeypatch):
    from app import renderer

    a, b = tmp_path / "slow.md", tmp_path / "last.md"
    a.write_text("# Obsolete", encoding="utf-8")
    b.write_text("# Last", encoding="utf-8")
    started, cancelled = threading.Event(), threading.Event()
    original = renderer.convert

    def convert(path, theme, *, cancel=None):
        if path == a:
            started.set()
            assert cancel.wait(5), "obsolete work was never cancelled"
            cancelled.set()
            raise renderer.RenderCancelled()
        return original(path, theme, cancel=cancel)

    monkeypatch.setattr(renderer, "convert", convert)
    headings, ready = [], []
    view = RendererView(on_headings_ready=headings.append)
    view.resize(700, 500)
    view.show()
    view.content_ready.connect(lambda *args: ready.append(args))
    view.load_file(a)
    _wait_until(qapp, started.is_set)
    view.load_file(b)
    generation = view._render_generation
    _wait_until(qapp, lambda: any(row[0] == generation for row in ready))
    assert cancelled.is_set()
    assert all(row[1] == str(b) for row in ready)
    assert not any(hs and hs[0][1] == "Obsolete" for hs in headings)
    assert _evaluate(view, "document.querySelector('h1').textContent") == "Last"


@_real
def test_real_large_document_uses_bounded_file_navigation_and_keeps_relative_assets(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    image = QImage(3, 3, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(tmp_path / "pixel.png"))
    path = tmp_path / "large.md"
    path.write_text(
        "# Large\n\n![pixel](pixel.png)\n\n"
        + ("ordinary text " * 900 + "\n\n") * 170
        + "\n## Final sentinel\n",
        encoding="utf-8",
    )
    view = RendererView()
    view.resize(800, 500)
    view.show()
    ready, failed = [], []
    view.content_ready.connect(lambda *args: ready.append(args))
    view.render_failed.connect(lambda *args: failed.append(args))
    first_temporary = None
    for _ in range(3):
        view.load_file(path)
        generation = view._render_generation
        _wait_until(
            qapp, lambda: any(row[0] == generation and not row[2] for row in ready), timeout=30
        )
        if first_temporary is None:
            first_temporary = Path(view._html_files[-1].fileName())
    assert failed == []
    assert len(view._html_files) == 2
    assert not first_temporary.exists()
    assert view.page().url().toLocalFile().endswith(".html")
    assert _evaluate(view, "document.querySelector('h2').textContent") == "Final sentinel"
    assert _evaluate(view, "document.querySelector('img').naturalWidth") == 3
    assert _evaluate(view, "document.baseURI").startswith(tmp_path.as_uri())
    view.reload_current()
    _wait_until(qapp, lambda: view._render_generation > generation)
    latest = view._render_generation
    _wait_until(qapp, lambda: any(row[0] == latest and not row[2] for row in ready), timeout=30)
    assert _evaluate(view, "document.querySelector('h2').textContent") == "Final sentinel"
    assert not view._loading_shield.isVisible()


@_real
def test_real_partial_export_waits_for_file_backed_full_page_and_temp_cleanup(
    qapp, tmp_path, monkeypatch
):
    import fitz
    from PySide6.QtCore import QCoreApplication, QEvent

    path = tmp_path / "controlled.md"
    path.write_text("# Complete", encoding="utf-8")
    view = RendererView()
    view.resize(800, 500)
    view.show()
    monkeypatch.setattr(view, "_finish_load_file", lambda *_args: None)
    ready, exported = [], []
    view.content_ready.connect(lambda *args: ready.append(args))
    view.load_file(path)
    generation = view._render_generation
    view._on_file_partial_ready(
        generation, path, "<html><head></head><body>Partial only</body></html>", [], 10
    )
    _wait_until(qapp, lambda: any(row[2] for row in ready))
    output = tmp_path / "complete.pdf"
    view.export_pdf(output, lambda *args: exported.append(args))
    assert not output.exists()
    full = (
        "<html><head></head><body><h1>Complete sentinel</h1><!--"
        + "x" * 800_000
        + "--></body></html>"
    )
    view._on_file_render_ready(generation, path, full, [])
    _wait_until(qapp, lambda: bool(exported), timeout=15)
    assert exported == [(str(output), True)]
    with fitz.open(output) as pdf:
        text = "".join(page.get_text() for page in pdf)
        assert "Complete sentinel" in text
        assert "Partial only" not in text
    temporary_paths = [Path(item.fileName()) for item in view._html_files]
    assert temporary_paths and all(item.exists() for item in temporary_paths)
    view.deactivate()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not item.exists() for item in temporary_paths)
