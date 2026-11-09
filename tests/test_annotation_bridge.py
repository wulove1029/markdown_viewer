import json

from app.annotation_bridge import AnnotationBridge


def test_add_emits_added(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.added.connect(got.append)
    bridge.add('{"id":"abc","exact":"x"}')
    assert got == ['{"id":"abc","exact":"x"}']


def test_remove_emits_removed(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.removed.connect(got.append)
    bridge.remove("abc")
    assert got == ["abc"]


def test_unhandled_escape_slot_emits_signal(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.unhandledEscape.connect(got.append)

    bridge.reportUnhandledEscape(7)

    assert got == [7]


def test_report_orphans_parses_json(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.orphansReported.connect(got.append)
    bridge.reportOrphans(json.dumps(["a", "b"]))
    assert got == [["a", "b"]]


def test_inline_edit_slots_report_unavailable_without_handlers(qapp):
    bridge = AnnotationBridge()

    assert json.loads(bridge.inlineEditFetch(0, 1)) == {
        "ok": False, "error": "unavailable"
    }
    assert json.loads(bridge.inlineEditCommit(0, 1, "a", "b", "sig"))["ok"] is False
    assert json.loads(bridge.inlineEditCommitTable(0, 1, "a", "{}", "sig")) == {
        "ok": False, "error": "unavailable"
    }
    assert json.loads(bridge.inlineEditSerializeTable("{}")) == {
        "ok": False, "error": "unavailable"
    }
    assert json.loads(bridge.inlineEditReload()) == {
        "ok": False, "error": "unavailable"
    }
    assert json.loads(bridge.inlineEditPasteImage())["ok"] is False


def test_inline_edit_slots_forward_arguments_and_serialize_the_reply(qapp):
    calls = []
    bridge = AnnotationBridge()
    bridge.set_inline_edit_handlers(
        fetch=lambda start, end: calls.append(("fetch", start, end))
        or {"ok": True, "text": "line"},
        commit=lambda start, end, original, new, sig: calls.append(
            ("commit", start, end, original, new, sig)
        )
        or {"ok": True},
        paste_image=lambda: {"ok": True, "link": "![](assets/a.png)"},
        commit_table=lambda start, end, original, model_json, sig: calls.append(
            ("commit_table", start, end, original, model_json, sig)
        )
        or {"ok": True},
        serialize_table=lambda model_json: calls.append(("serialize", model_json))
        or {"ok": True, "text": "| a |"},
        reload=lambda: calls.append(("reload",)) or {"ok": True},
    )

    assert json.loads(bridge.inlineEditFetch(2, 5)) == {"ok": True, "text": "line"}
    assert json.loads(bridge.inlineEditCommit(2, 5, "old", "new", "s1")) == {
        "ok": True
    }
    assert json.loads(bridge.inlineEditPasteImage())["link"] == "![](assets/a.png)"
    assert json.loads(bridge.inlineEditCommitTable(2, 5, "old", "{}", "s1")) == {
        "ok": True
    }
    assert json.loads(bridge.inlineEditSerializeTable("{}"))["text"] == "| a |"
    assert json.loads(bridge.inlineEditReload()) == {"ok": True}
    assert calls == [
        ("fetch", 2, 5),
        ("commit", 2, 5, "old", "new", "s1"),
        ("commit_table", 2, 5, "old", "{}", "s1"),
        ("serialize", "{}"),
        ("reload",),
    ]


def test_inline_edit_reply_keeps_non_ascii_text_readable(qapp):
    bridge = AnnotationBridge()
    bridge.set_inline_edit_handlers(fetch=lambda s, e: {"ok": True, "text": "中文"})

    assert json.loads(bridge.inlineEditFetch(0, 0))["text"] == "中文"


def test_inline_edit_handler_exception_answers_instead_of_hanging_the_page(qapp):
    def boom(start, end):
        raise RuntimeError("disk on fire")

    bridge = AnnotationBridge()
    bridge.set_inline_edit_handlers(fetch=boom)

    reply = json.loads(bridge.inlineEditFetch(0, 0))
    assert reply["ok"] is False
    assert "disk on fire" in reply["error"]


import os
import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app import renderer
from app.annotations import Annotation
from app.renderer import RendererView

# These end-to-end tests construct a real QWebEngineView. Headless Chromium is
# flaky in CI/sandboxes (a render crash hard-aborts the whole pytest run), so
# they are opt-in. Run them on a machine with a working display via:
#   RUN_WEBENGINE_TESTS=1 python -m pytest tests/test_annotation_bridge.py
_skip_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _eval(view, js):
    box = {}
    loop = QEventLoop()
    def cb(v):
        box["v"] = v
        loop.quit()
    view.page().runJavaScript(js, cb)
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    return box.get("v")


def _until(predicate, timeout_ms=10000, step_ms=20):
    """Poll *predicate* while the event loop runs; never a fixed sleep."""
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(step_ms)
    return predicate()


# Set by the renderer's document-creation script once its Escape watcher is
# listening; from then on an unhandled Escape is kept until the bridge is ready.
_ESCAPE_WATCH_READY = "window.__mdUnhandledEscapeReady === true"
# Loading a document (the first one also starts Chromium) can take far longer
# under CPU load than delivering an Escape; those waits get their own cap.
_LOAD_TIMEOUT_MS = 30000


def _escape_watch_ready(view, document_check):
    return _eval(view, f"!!({document_check}) && {_ESCAPE_WATCH_READY}") is True


def _press_escape(view):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    focus_target = view.focusProxy() or view
    focus_target.setFocus()
    QTest.keyClick(focus_target, Qt.Key.Key_Escape)


@_skip_webengine
def test_stored_annotation_renders_mark(qapp, tmp_path):
    md = tmp_path / "d.md"
    md.write_text("The quick brown fox jumps over the lazy dog.", encoding="utf-8")
    ann = Annotation.new(exact="brown fox", prefix="quick ", suffix=" jumps",
                         textPosition=10)
    view = RendererView()
    view.resize(700, 500)
    view.set_annotations([ann.to_dict()])
    view.load_file(md)
    _wait(4000)
    count = _eval(view, "document.querySelectorAll('mark.annot').length")
    assert count == 1
    located = []
    view.scroll_to_annotation(ann.id, located.append)
    _wait(100)
    assert located == [True]
    _eval(view, "window.matchMedia=()=>({matches:true});"
          "Element.prototype.scrollIntoView=function(options){window.__scrollBehavior=options.behavior;}")
    view.scroll_to_annotation(ann.id)
    _wait(100)
    assert _eval(view, "window.__scrollBehavior") == "auto"


@_skip_webengine
def test_orphan_annotation_not_rendered(qapp, tmp_path):
    md = tmp_path / "d.md"
    md.write_text("nothing matches here", encoding="utf-8")
    ann = Annotation.new(exact="absent phrase", textPosition=0)
    view = RendererView()
    view.resize(700, 500)
    view.set_annotations([ann.to_dict()])
    view.load_file(md)
    _wait(4000)
    count = _eval(view, "document.querySelectorAll('mark.annot').length")
    assert count == 0
    reported, located = [], []
    view.annotation_orphans(reported.append)
    view.scroll_to_annotation(ann.id, located.append)
    _wait(100)
    assert reported == [[ann.id]]
    assert located == [False]


@_skip_webengine
def test_rendered_page_reports_only_unhandled_escape(qapp, tmp_path):
    md = tmp_path / "escape.md"
    md.write_text("# Escape bridge", encoding="utf-8")
    view = RendererView()
    view.resize(700, 500)
    reported = []
    view.bridge.unhandledEscape.connect(reported.append)
    view.set_search_escape_generation(7)
    try:
        view.show()
        view.load_file(md)
        assert _until(lambda: view._loaded_markdown_generation == view._render_generation,
                      _LOAD_TIMEOUT_MS)
        assert _until(lambda: _escape_watch_ready(view, "document.querySelector('h1')"),
                      _LOAD_TIMEOUT_MS)
        _press_escape(view)
        assert _until(lambda: reported == [7]), reported
        # A fresh token for the next Escape: a duplicate report of the first
        # one would reach Python before it, so the order proves there is none.
        # The token reaches the page as script, which a key event may overtake.
        view.set_search_escape_generation(8)
        assert _eval(view, "window.__mdSearchEscapeGeneration") == 8
        _press_escape(view)
        assert _until(lambda: len(reported) >= 2), reported
        assert reported == [7, 8]

        reported.clear()
        view.set_search_escape_generation(7)
        view.render_html("<html><body tabindex='0'>live preview</body></html>")
        assert _until(lambda: _escape_watch_ready(
            view, "document.body && document.body.textContent === 'live preview'"),
            _LOAD_TIMEOUT_MS)
        _press_escape(view)
        assert _until(lambda: reported == [7]), reported

        _eval(
            view,
            "window.__escapeSeen = 0;"
            "document.addEventListener('keydown', function(e) {"
            "if (e.key !== 'Escape') return;"
            "window.__escapeSeen += 1;"
            "if (!window.__letEscapeThrough) e.preventDefault();"
            "}, true); true;",
        )
        _press_escape(view)
        assert _until(lambda: _eval(view, "window.__escapeSeen") == 1)
        # The page handled that Escape. Only the next, unhandled one (with a
        # fresh token) may arrive, and a report of the handled one would
        # have reached Python first.
        view.set_search_escape_generation(8)
        _eval(view, "window.__letEscapeThrough = true; true;")
        _press_escape(view)
        assert _until(lambda: len(reported) >= 2), reported
        assert reported == [7, 8]
    finally:
        view.close()


@_skip_webengine
@pytest.mark.parametrize("rotate", [False, True], ids=["same-page", "rotated-page"])
def test_escape_pressed_while_a_redrawn_preview_boots_is_still_reported(
    qapp, tmp_path, monkeypatch, rotate
):
    """Escape in the new document before its QWebChannel is ready is not lost.

    The key is pressed from the redrawn page's own loadFinished, i.e. before
    the bridge boot script has even run there, let alone finished its channel
    handshake. Under CPU load that window lasts seconds. Like the live
    preview, the page has a file base URL.
    """
    from PySide6.QtCore import QUrl

    if rotate:
        monkeypatch.setattr(renderer, "_PAGE_NAVIGATION_LIMIT", 1)
    view = RendererView()
    view.resize(700, 500)
    reported, loads, pressed = [], [], []
    view.bridge.unhandledEscape.connect(reported.append)
    view.loadFinished.connect(loads.append)
    view.set_search_escape_generation(7)

    def press_on_load(ok):
        if ok and not pressed:
            pressed.append(view.page())
            _press_escape(view)

    try:
        view.show()
        assert _until(lambda: loads == [True], _LOAD_TIMEOUT_MS), loads  # empty-state page
        first_page = view.page()
        view.loadFinished.connect(press_on_load)
        view.render_html("<html><body tabindex='0'>redrawn preview</body></html>",
                         QUrl.fromLocalFile(str(tmp_path) + "/"))
        assert (view.page() is not first_page) == rotate
        assert _until(lambda: bool(pressed), _LOAD_TIMEOUT_MS)
        assert pressed == [view.page()]
        assert _until(lambda: reported == [7]), reported
    finally:
        view.close()


@_skip_webengine
def test_escape_watch_script_follows_generation_and_page_rotation(qapp):
    view = RendererView()

    def watcher_sources():
        return [script.sourceCode() for script in
                view.page().scripts().find(renderer._UNHANDLED_ESCAPE_WATCH_SCRIPT)]

    try:
        sources = watcher_sources()
        assert len(sources) == 1 and "window.__mdSearchEscapeGeneration = 0;" in sources[0]
        view.set_search_escape_generation(7)
        sources = watcher_sources()
        assert len(sources) == 1 and "window.__mdSearchEscapeGeneration = 7;" in sources[0]
        first_page = view.page()
        view._rotate_document_page()
        assert view.page() is not first_page
        sources = watcher_sources()
        assert len(sources) == 1 and "window.__mdSearchEscapeGeneration = 7;" in sources[0]
    finally:
        view.close()


@_skip_webengine
def test_escape_reports_survive_pages_without_document_creation_scripts(qapp):
    """Qt 6.11 runs no QWebEngineScript in a data: page that replaced another.

    Such a page (setHtml without a base URL, twice) must still report an
    unhandled Escape once its bridge is up instead of never.
    """
    view = RendererView()
    view.resize(700, 500)
    reported, loads = [], []
    view.bridge.unhandledEscape.connect(reported.append)
    view.loadFinished.connect(loads.append)
    view.set_search_escape_generation(7)
    try:
        view.show()
        assert _until(lambda: loads == [True], _LOAD_TIMEOUT_MS), loads  # data: empty page
        for text in ("first data page", "second data page"):
            count = len(loads)
            view.render_html(f"<html><body tabindex='0'>{text}</body></html>")
            assert _until(lambda count=count: len(loads) > count, _LOAD_TIMEOUT_MS)
        assert _until(lambda: _escape_watch_ready(
            view, "document.body && document.body.textContent === 'second data page'"),
            _LOAD_TIMEOUT_MS)
        _press_escape(view)
        assert _until(lambda: reported == [7]), reported
    finally:
        view.close()
