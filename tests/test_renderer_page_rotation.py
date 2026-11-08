"""Bounded Chromium page lifetime without losing reading or export contracts."""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import fitz
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, Qt, QTimer, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from shiboken6 import getCppPointer, isValid

from app import renderer
from app.annotations import Annotation
from app.renderer import RendererView, _run_page_javascript


@pytest.mark.parametrize("target", ["Target", "資料/筆記#段落", "100% done", "literal%2F"])
def test_internal_wikilink_preserves_target_without_loading_network(qapp, target):
    events = []
    page = SimpleNamespace(_view=SimpleNamespace(
        wikilink_clicked=SimpleNamespace(emit=events.append)))
    url = QUrl("https://markdown-viewer.invalid/wikilink/" + quote(target, safe=""))
    accepted = renderer._DocumentPage.acceptNavigationRequest(
        page, url, QWebEnginePage.NavigationType.NavigationTypeLinkClicked, True)
    # The note opens after Chromium's navigation callback returns (F2).
    assert accepted is False and events == []
    QTest.qWait(20)
    assert events == [target]


def test_retired_page_javascript_reply_finishes_as_empty():
    pending, values = [], []
    old = SimpleNamespace(runJavaScript=lambda script, callback: pending.append(callback))
    current = [old]
    view = SimpleNamespace(page=lambda: current[0], _page_epoch=0)
    _run_page_javascript(view, "document.body.innerText", values.append)
    current[0] = object()
    view._page_epoch += 1
    pending.pop()("old document")
    assert values == [None]


def test_navigation_waits_for_print_and_keeps_only_latest_request():
    submitted = []
    view = SimpleNamespace(
        _navigation_serial=0, _render_generation=3, _pdf_printing=True,
        _deferred_navigation=None, _submit_page_navigation=submitted.append,
    )
    RendererView._navigate_page(view, "html", "older")
    RendererView._navigate_page(view, "html", "latest")
    assert submitted == []
    assert view._deferred_navigation == (2, 3, "html", "latest", None)


def test_superseded_navigation_and_same_generation_prefix_do_not_rotate(monkeypatch):
    monkeypatch.setattr(renderer, "_PAGE_NAVIGATION_LIMIT", 2)
    pages, rotations = [], []
    view = SimpleNamespace(
        _navigation_serial=4, _render_generation=8,
        _page_navigation_count=2, _page_last_generation=8,
        _rotate_document_page=lambda: rotations.append(True),
        page=lambda: SimpleNamespace(setHtml=pages.append),
    )
    RendererView._submit_page_navigation(view, (3, 8, "html", "old serial", None))
    RendererView._submit_page_navigation(view, (4, 7, "html", "old generation", None))
    RendererView._submit_page_navigation(view, (4, 8, "html", "full", None))
    assert pages == ["full"] and rotations == []
    view._render_generation = 9
    RendererView._submit_page_navigation(view, (4, 9, "html", "next", None))
    assert rotations == [True] and pages[-1] == "next"


@pytest.mark.parametrize("progress,expected", [(0.4, [0.4]), (-8, [0.0]), (9, [1.0]),
                                               (float("nan"), []), (None, [])])
def test_reading_progress_is_finite_clamped_and_rejects_old_document(progress, expected):
    values = []
    view = SimpleNamespace(
        _render_generation=4, _current_path=Path("current.md"),
        _scroll_y=0, _partial_preview_active=False, _current_anchor="",
        reading_progress_changed=SimpleNamespace(emit=values.append),
    )
    payload = ["", 10, None, False, None, progress]
    RendererView._on_spy_result(view, payload, 3, view._current_path)
    RendererView._on_spy_result(view, payload, 4, Path("other.md"))
    assert values == []
    RendererView._on_spy_result(view, payload, 4, view._current_path)
    assert values == expected


_real = pytest.mark.skipif(os.environ.get("RUN_WEBENGINE_TESTS") != "1",
                          reason="set RUN_WEBENGINE_TESTS=1 for Chromium page rotation")


def _evaluate(view, script):
    values = []
    loop = QEventLoop()
    view.page().runJavaScript(script, lambda value: (values.append(value), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert values, "JavaScript did not answer"
    return values[0]


def _until(condition, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        QTest.qWait(20)
    pytest.fail("Renderer did not reach the requested state")


def _load(view, path):
    view.load_file(path)
    _until(lambda: view._loaded_markdown_generation == view._render_generation)


def _click_wikilink(view):
    point = json.loads(_evaluate(view, "JSON.stringify((function(){"
        "var r=document.querySelector('a.wikilink').getBoundingClientRect();"
        "return [r.x+r.width/2,r.y+r.height/2];})())"))
    QTest.mouseClick(view.focusProxy(), Qt.MouseButton.LeftButton,
                     pos=QPoint(*(round(value * view.zoomFactor()) for value in point)))


@_real
def test_rotation_preserves_configuration_and_features(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "_PAGE_NAVIGATION_LIMIT", 2)
    note = tmp_path / "reading.md"
    note.write_text("The quick brown fox jumps over the lazy dog.\n\n[[Target]]\n\n"
                    "- [ ] rotation task\n\n" +
                    "\n\n".join(f"## Chapter {i}\n\nparagraph {i}" for i in range(50)), "utf-8")
    view = RendererView()
    view.resize(800, 600)
    view.show()
    first = view.page()
    profile_pointer = getCppPointer(first.profile())
    bridge, channel = view.bridge, view._channel
    first.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
    first.settings().setFontFamily(QWebEngineSettings.FontFamily.FixedFont, "Consolas")
    script = QWebEngineScript()
    script.setName("retained-test-script")
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setSourceCode("window.retainedSetting = 123;")
    first.scripts().insert(script)
    view.set_zoom(1.25)
    annotation = Annotation.new(exact="brown fox", prefix="quick ", suffix=" jumps",
                                textPosition=10)
    view.set_annotations([annotation.to_dict()])
    events, links, progress = [], [], []
    bridge.taskToggled.connect(lambda line, checked: events.append((line, checked)))
    view.wikilink_clicked.connect(links.append)
    view.reading_progress_changed.connect(progress.append)
    _load(view, note)
    _click_wikilink(view)
    _until(lambda: links == ["Target"])
    assert view._page_rotation_count == 0
    retired = [first]
    for _ in range(4):
        previous = view.page()
        _load(view, note)
        if previous is not view.page() and previous not in retired:
            retired.append(previous)
    assert view._page_rotation_count == 2
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not isValid(page) for page in retired)
    assert getCppPointer(view.page().profile()) == profile_pointer
    assert view.bridge is bridge and view.page().webChannel() is channel
    assert view.zoomFactor() == pytest.approx(1.25)
    assert not view.settings().testAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows)
    assert view.settings().fontFamily(QWebEngineSettings.FontFamily.FixedFont) == "Consolas"
    assert _evaluate(view, "window.retainedSetting") == 123
    _until(lambda: _evaluate(view, "document.querySelectorAll('mark.annot').length") == 1)
    _evaluate(view, "document.querySelector('input[type=checkbox]').click()")
    _until(lambda: events == [(4, True)])
    _click_wikilink(view)
    _until(lambda: links == ["Target", "Target"])
    view.set_theme("dark")
    assert _evaluate(view, "document.body.classList.contains('theme-dark')")
    assert _evaluate(view, "!!window.readingMedia && !!window.__inlineEdit")
    view.reveal_source_line_after_load(105)
    _until(lambda: _evaluate(view, "window.scrollY") > 1000)
    view._poll_active_heading()
    _until(lambda: bool(progress) and progress[-1] > 0)
    assert progress[-1] <= 1


@_real
def test_print_finishes_before_latest_navigation_rotates(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "_PAGE_NAVIGATION_LIMIT", 1)
    before = tmp_path / "before.md"
    before.write_text("# PRINT-ORIGINAL\n\n" +
                      "\n\n".join("old content " * 20 for _ in range(80)), "utf-8")
    view = RendererView()
    _load(view, before)
    original = view.page()
    output = tmp_path / "original.pdf"
    results, rejected = [], []
    # Start printing synchronously once the already plain document is ready.
    monkeypatch.setattr(RendererView, "_prepare_diagrams", lambda self, callback: callback(True))
    view.export_pdf(output, lambda path, ok: results.append((path, ok)))
    assert view._pdf_printing
    view.export_pdf(tmp_path / "duplicate.pdf", lambda path, ok: rejected.append(ok))
    assert rejected == [False] and view._pdf_printing
    view._next_render_generation()
    view._navigate_page("html", "<html><body>SUPERSEDED</body></html>")
    view._next_render_generation()
    view._navigate_page("html", "<html><body>LATEST-DOCUMENT</body></html>")
    assert view.page() is original
    _until(lambda: bool(results))
    assert results == [(str(output), True)]
    _until(lambda: _evaluate(view, "document.body.innerText") == "LATEST-DOCUMENT")
    assert view.page() is not original
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(original)
    with fitz.open(output) as pdf:
        text = "".join(page.get_text() for page in pdf)
    assert "PRINT-ORIGINAL" in text and "LATEST-DOCUMENT" not in text
    assert not (tmp_path / "duplicate.pdf").exists()


@_real
def test_pdf_export_after_rotation_includes_the_current_document(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "_PAGE_NAVIGATION_LIMIT", 1)
    note = tmp_path / "current.md"
    note.write_text("# CURRENT-PDF\n\nComplete exported body.", "utf-8")
    view = RendererView()
    _load(view, note)
    assert view._page_rotation_count == 1
    results = []
    output = tmp_path / "current.pdf"
    view.export_pdf(output, lambda path, ok: results.append(ok))
    _until(lambda: bool(results))
    assert results == [True]
    with fitz.open(output) as pdf:
        assert "CURRENT-PDF" in "".join(page.get_text() for page in pdf)


@_real
def test_immediate_navigation_after_rotation_remains_usable(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, "_PAGE_NAVIGATION_LIMIT", 1)
    note = tmp_path / "rapid.md"
    note.write_text("# RAPID", "utf-8")
    view = RendererView()
    view.show()
    _load(view, note)
    for index in range(10):
        old = view.page()
        marker = f"RAPID-NEXT-{index}"
        view._next_render_generation()
        view._navigate_page("html", f"<html><body>{marker}</body></html>")
        assert view.page() is not old
        _until(lambda: _evaluate(view, "document.body.innerText") == marker)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(old)
    assert view._page_rotation_count == 11
