"""End-to-end inline preview editing in a real QWebEngineView.

Same opt-in gate as tests/test_annotation_bridge.py: headless Chromium can
hard-abort a whole pytest run, so these only execute with
``RUN_WEBENGINE_TESTS=1``. They are the only place the JavaScript runs in a
real browser rather than against the Node DOM stub.
"""

import json
import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app.inline_edit import extract_source_lines, replace_source_lines
from app.renderer import RendererView

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

    def done(value):
        box["v"] = value
        loop.quit()

    view.page().runJavaScript(js, done)
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    return box.get("v")


def _wire(view, path):
    """Back the bridge with the same pure functions the window uses."""
    committed = []

    def fetch(start, end):
        text = path.read_text(encoding="utf-8")
        source = extract_source_lines(text, start, end)
        if source is None:
            return {"ok": False, "error": "out-of-range"}
        return {"ok": True, "text": source}

    def commit(start, end, original, new):
        text = path.read_text(encoding="utf-8")
        out = replace_source_lines(text, start, end, original, new)
        if out is None:
            return {"ok": False, "error": "stale"}
        path.write_text(out, encoding="utf-8")
        committed.append((start, end, original, new))
        return {"ok": True}

    view.bridge.set_inline_edit_handlers(fetch=fetch, commit=commit)
    return committed


def _open_preview(tmp_path, body):
    md = tmp_path / "doc.md"
    md.write_text(body, encoding="utf-8")
    view = RendererView()
    view.resize(800, 600)
    committed = _wire(view, md)
    view.load_file(md)
    _wait(4000)
    return md, view, committed


_DOC = "# Title\n\nalpha line\nbeta line\n\n- a\n- b\n"


@_skip_webengine
def test_rendered_blocks_carry_their_source_ranges(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path, _DOC)

    ranges = _eval(
        view,
        "JSON.stringify(Array.prototype.map.call("
        "document.querySelectorAll('[data-src-start]'),"
        "function (el) { return [el.tagName, el.getAttribute('data-src-start'),"
        "el.getAttribute('data-src-end')]; }))",
    )

    assert json.loads(ranges) == [
        ["H1", "0", "0"],
        ["P", "2", "3"],
        ["UL", "5", "6"],
    ]


@_skip_webengine
def test_triple_click_opens_a_textarea_holding_the_raw_markdown(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path, _DOC)

    _eval(
        view,
        "document.querySelector('p[data-src-start=\"2\"]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(600)

    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 1
    assert _eval(view, "document.querySelector('.inline-edit textarea').value") == (
        "alpha line\nbeta line"
    )
    # The rendered paragraph is hidden, not destroyed.
    assert _eval(
        view, "document.querySelector('p[data-src-start=\"2\"]').style.display"
    ) == "none"


@_skip_webengine
def test_ctrl_enter_writes_the_edit_back_to_the_file(qapp, tmp_path):
    md, view, committed = _open_preview(tmp_path, _DOC)

    _eval(
        view,
        "document.querySelector('p[data-src-start=\"2\"]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(600)
    _eval(
        view,
        "(function () { var t = document.querySelector('.inline-edit textarea');"
        "t.value = 'rewritten'; t.dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Enter', ctrlKey: true, bubbles: true})); })()",
    )
    _wait(800)

    assert md.read_text(encoding="utf-8") == "# Title\n\nrewritten\n\n- a\n- b\n"
    assert committed == [(2, 3, "alpha line\nbeta line", "rewritten")]


@_skip_webengine
def test_escape_cancels_without_touching_the_file(qapp, tmp_path):
    md, view, committed = _open_preview(tmp_path, _DOC)

    _eval(
        view,
        "document.querySelector('p[data-src-start=\"2\"]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(600)
    _eval(
        view,
        "(function () { var t = document.querySelector('.inline-edit textarea');"
        "t.value = 'discarded'; t.dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Escape', bubbles: true})); })()",
    )
    _wait(400)

    assert md.read_text(encoding="utf-8") == _DOC
    assert committed == []
    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 0
    assert _eval(
        view, "document.querySelector('p[data-src-start=\"2\"]').style.display"
    ) == ""


@_skip_webengine
def test_stale_commit_is_refused_and_the_file_keeps_the_other_write(qapp, tmp_path):
    md, view, committed = _open_preview(tmp_path, _DOC)

    _eval(
        view,
        "document.querySelector('p[data-src-start=\"2\"]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(600)
    external = "# Title\n\nsomeone else\n\n- a\n- b\n"
    md.write_text(external, encoding="utf-8")
    _eval(
        view,
        "(function () { var t = document.querySelector('.inline-edit textarea');"
        "t.value = 'my edit'; t.dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Enter', ctrlKey: true, bubbles: true})); })()",
    )
    _wait(800)

    assert md.read_text(encoding="utf-8") == external
    assert committed == []


@_skip_webengine
def test_disabled_preview_ignores_triple_clicks(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path, _DOC)
    view.set_inline_edit_enabled(False)
    _wait(200)

    _eval(
        view,
        "document.querySelector('p[data-src-start=\"2\"]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(600)

    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 0


@_skip_webengine
def test_triple_click_on_a_task_checkbox_does_not_open_an_editor(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path, "# T\n\n- [ ] todo\n")

    _eval(
        view,
        "document.querySelector('input.task-list-item-checkbox')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(600)

    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 0
