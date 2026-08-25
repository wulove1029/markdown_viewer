"""End-to-end WYSIWYG editing in a real QWebEngineView + real Vditor.

Same opt-in gate as tests/test_inline_edit_webengine.py and
tests/test_annotation_bridge.py: headless Chromium can hard-abort a whole
pytest run, so these only execute with ``RUN_WEBENGINE_TESTS=1``. This is the
only place assets/vditor_glue.js and the bundled Vditor library run together
in a real browser rather than against the Node/Vditor stub in
tests/js/vditor_glue_harness.js.
"""

import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QTextDocument

from app.wysiwyg_view import WysiwygView

_skip_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(predicate, timeout_ms=8000, step_ms=50):
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        _wait(step_ms)
        waited += step_ms
    return predicate()


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


def _current_value(view) -> str:
    # Vditor/lute normalizes a trailing newline onto getValue(); trimming it
    # here is the documented "lute may normalize layout" trade-off, not a
    # bug in the glue code.
    value = _eval(view, "window.__wysiwygGlue.getValue()")
    return value.rstrip("\n") if isinstance(value, str) else value


@_skip_webengine
def test_wysiwyg_view_becomes_ready_and_round_trips_load_markdown(qapp):
    view = WysiwygView()
    try:
        ready = _wait_until(lambda: view._ready)
        assert ready, "WYSIWYG view never reported ready()"

        view.load_markdown("# hello\n\nworld")
        _wait_until(lambda: _current_value(view) == "# hello\n\nworld")
        assert _current_value(view) == "# hello\n\nworld"
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_view_pushes_typed_content_after_debounce(qapp):
    view = WysiwygView()
    pushed = []
    view.content_changed.connect(pushed.append)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("start")
        assert _wait_until(lambda: _current_value(view) == "start")

        # Vditor's own insertValue() mutates the WYSIWYG DOM and fires the
        # `input` callback glue code wired -- the same path real typing
        # takes, without simulating individual Chromium key events.
        _eval(
            view,
            "window.__wysiwygGlue._state.vditor.insertValue(' plus typed');",
        )
        assert _wait_until(lambda: len(pushed) >= 1, timeout_ms=3000)
        assert "plus typed" in pushed[-1]
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_view_ctrl_s_emits_save_requested(qapp):
    """The JS keydown interception reaches Python end to end.

    ``flushPending()`` firing *before* ``saveRequested`` -- so Ctrl+S can
    never save text one keystroke stale behind the 250ms debounce -- is
    exercised deterministically in tests/js/vditor_glue_harness.js with fake
    timers; the real browser's own internal typing pipeline (Vditor
    debounces its DOM commit ahead of calling our `input` option) makes that
    race timing-dependent to reproduce headlessly, so this only checks the
    ordering guarantee still holds once Vditor *has* reported the edit.
    """
    view = WysiwygView()
    saves = []
    pushed = []
    view.save_requested.connect(lambda: saves.append(True))
    view.content_changed.connect(pushed.append)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("before save")
        assert _wait_until(lambda: _current_value(view) == "before save")
        _eval(
            view,
            "window.__wysiwygGlue._state.vditor.insertValue(' -- edited');",
        )
        # Let Vditor's own pipeline call our `input` option naturally first.
        assert _wait_until(lambda: pushed, timeout_ms=3000)
        assert "edited" in pushed[-1]

        _eval(
            view,
            "document.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: 's', ctrlKey: true, bubbles: true, cancelable: true}));",
        )
        assert _wait_until(lambda: saves, timeout_ms=3000)
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_escape_reaches_the_bridge(qapp):
    """v2: a clean Esc (no hint panel open) fires esc_requested end to end."""
    view = WysiwygView()
    escapes = []
    view.esc_requested.connect(lambda: escapes.append(True))
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("some text")
        assert _wait_until(lambda: _current_value(view) == "some text")

        _eval(
            view,
            "document.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: 'Escape', bubbles: true, cancelable: true}));",
        )
        assert _wait_until(lambda: escapes, timeout_ms=3000)
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_push_writes_into_the_tab_document_and_marks_it_modified(qapp):
    """Mirrors the shadow-document push model MainWindow relies on.

    Not the full MainWindow (that integration lives in
    tests/test_editor_data_safety.py, which fakes the WebEngine widgets out),
    but exercises the same "write markdown into a QTextDocument and mark it
    modified" step against a real WYSIWYG push, end to end.
    """
    view = WysiwygView()
    document = QTextDocument("start")
    document.setModified(False)

    def apply_push(markdown):
        if document.toPlainText() != markdown:
            document.setPlainText(markdown)
            document.setModified(True)

    view.content_changed.connect(apply_push)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("start")
        assert _wait_until(lambda: _current_value(view) == "start")
        _eval(
            view,
            "window.__wysiwygGlue._state.vditor.insertValue(' edited');",
        )
        assert _wait_until(lambda: document.isModified(), timeout_ms=3000)
        assert "edited" in document.toPlainText()
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_context_menu_reaches_the_bridge(qapp):
    """v4: a real right-click in the Vditor surface fires context_menu_requested."""
    view = WysiwygView()
    requests = []
    view.context_menu_requested.connect(lambda x, y: requests.append((x, y)))
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("some text")
        assert _wait_until(lambda: _current_value(view) == "some text")

        _eval(
            view,
            "document.dispatchEvent(new MouseEvent('contextmenu', "
            "{clientX: 12, clientY: 34, bubbles: true, cancelable: true}));",
        )
        assert _wait_until(lambda: requests, timeout_ms=3000)
        x, y = requests[-1]
        assert (x, y) == (12, 34)
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_custom_toolbar_button_reaches_the_bridge(qapp):
    """v4: clicking a custom toolbar entry (e.g. save) fires toolbar_action."""
    view = WysiwygView()
    actions = []
    view.toolbar_action.connect(actions.append)
    try:
        assert _wait_until(lambda: view._ready)
        # Click the real DOM button Vditor rendered for the custom "save"
        # toolbar item (see CUSTOM_TOOLBAR_ITEMS in assets/vditor_glue.js) --
        # exercises the actual click handler a user click would trigger.
        _eval(
            view,
            "(function(){"
            "var btn = document.querySelector('[data-type=\"save\"]');"
            "if (btn) { btn.click(); }"
            "})();",
        )
        assert _wait_until(lambda: actions, timeout_ms=3000)
        assert actions[-1] == "save"
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_insert_value_and_get_html(qapp):
    """v4: insert_value() lands in the buffer; get_html() returns rendered HTML."""
    view = WysiwygView()
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("# Title")
        assert _wait_until(lambda: _current_value(view) == "# Title")

        view.insert_value("\n\n![alt](img.png)")
        assert _wait_until(
            lambda: "img.png" in (_current_value(view) or ""), timeout_ms=3000
        )

        box = {}

        def capture(html):
            box["html"] = html

        view.get_html(capture)
        assert _wait_until(lambda: "html" in box, timeout_ms=3000)
        assert isinstance(box["html"], str)
        assert "Title" in box["html"]
    finally:
        view.deleteLater()


import json

# v4 second wave: Notion-style block handles + pointer-events drag-to-reorder
# (see assets/vditor_glue.js's installBlockHandles()/onDragPointerDown()).
# This JS snippet drives the exact same real-DOM sequence a user's mouse
# would: hover the second block to reveal its "::" drag handle, pointerdown
# on that handle, pointermove over the first block, pointerup there. It
# returns a JSON-stringified diagnostic object rather than a plain JS object
# -- this build's QWebEngineView.runJavaScript() callback silently comes
# back as "" for a returned plain object/boolean-bearing object (verified:
# `(function(){return {a:1};})()` -> ""), so every value crossing back to
# Python here goes through JSON.stringify()/json.loads() instead.
_DRAG_SECOND_BLOCK_ABOVE_FIRST_JS = """
JSON.stringify((function(){
  function fire(el, type, x, y, extra) {
    var opts = Object.assign({bubbles: true, cancelable: true, clientX: x,
      clientY: y, button: 0, pointerId: 1}, extra || {});
    el.dispatchEvent(new PointerEvent(type, opts));
  }
  var editable = document.querySelector('.vditor-wysiwyg > pre.vditor-reset');
  if (!editable) return {error: 'no editable root'};
  var blocks = editable.children;
  if (blocks.length < 2) return {error: 'fewer than 2 top-level blocks', count: blocks.length};
  var first = blocks[0], second = blocks[1];

  var r2 = second.getBoundingClientRect();
  second.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, clientX: r2.left + 5, clientY: r2.top + 5}));

  var group = document.querySelector('.vditor-block-handle-group');
  if (!group || group.style.display !== 'flex') return {error: 'handle group not shown on hover'};
  var drag = group.querySelector('.vditor-block-handle--drag');
  if (!drag) return {error: 'no drag handle'};
  var dr = drag.getBoundingClientRect();
  fire(drag, 'pointerdown', dr.left + dr.width / 2, dr.top + dr.height / 2);

  var r1 = first.getBoundingClientRect();
  fire(document, 'pointermove', r1.left + 5, r1.top + 2);
  fire(document, 'pointerup', r1.left + 5, r1.top + 2);
  return {ok: true};
})());
"""


def _run_drag_second_block_above_first(view):
    raw = _eval(view, _DRAG_SECOND_BLOCK_ABOVE_FIRST_JS)
    assert isinstance(raw, str) and raw, "drag JS returned no JSON: %r" % (raw,)
    return json.loads(raw)


@_skip_webengine
def test_wysiwyg_drag_handle_reorders_top_level_blocks(qapp):
    """v4 second wave: dragging a block's "::" handle above another one
    reorders the DOM, and the reordered document reaches Python through the
    exact same debounced bridge.contentChanged push a real keystroke uses.
    """
    view = WysiwygView()
    pushed = []
    view.content_changed.connect(pushed.append)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("Block One\n\nBlock Two\n\nBlock Three")
        assert _wait_until(
            lambda: "Block Three" in (_current_value(view) or ""), timeout_ms=5000
        )

        result = _run_drag_second_block_above_first(view)
        assert isinstance(result, dict) and result.get("ok"), result

        def reordered():
            value = _current_value(view) or ""
            return (
                "Block Two" in value
                and "Block One" in value
                and value.index("Block Two") < value.index("Block One")
            )

        assert _wait_until(reordered, timeout_ms=5000)

        def pushed_reordered():
            if not pushed:
                return False
            last = pushed[-1]
            return (
                "Block Two" in last
                and "Block One" in last
                and last.index("Block Two") < last.index("Block One")
            )

        assert _wait_until(pushed_reordered, timeout_ms=5000), pushed
    finally:
        view.deleteLater()


@_skip_webengine
def test_wysiwyg_drag_reorder_undo_behavior(qapp):
    """v4 second wave, spec requirement 3: empirically record whether Ctrl+Z
    undoes a drag reorder.

    This is not a pass/fail assertion on undo working -- Vditor's undo stack
    may or may not capture a plain DOM node move that didn't go through its
    own insertValue()/setValue() API. The test always passes; it asserts
    only that the drag itself succeeded, and prints the empirically observed
    undo outcome so it's visible in the pytest output rather than asserted
    blind. See the WYSIWYG spec / final report for the recorded conclusion.
    """
    view = WysiwygView()
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("Block One\n\nBlock Two\n\nBlock Three")
        assert _wait_until(
            lambda: "Block Three" in (_current_value(view) or ""), timeout_ms=5000
        )
        before = _current_value(view)

        result = _run_drag_second_block_above_first(view)
        assert isinstance(result, dict) and result.get("ok"), result

        def reordered():
            value = _current_value(view) or ""
            return (
                "Block Two" in value
                and "Block One" in value
                and value.index("Block Two") < value.index("Block One")
            )

        assert _wait_until(reordered, timeout_ms=5000)
        after_drag = _current_value(view)

        _eval(
            view,
            "document.querySelector('.vditor-wysiwyg pre.vditor-reset').focus();"
            "document.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: 'z', ctrlKey: true, bubbles: true, cancelable: true}));",
        )
        _wait(500)
        after_undo = _current_value(view)

        undo_reverted = after_undo == before
        print(
            "\n[v4 drag undo] before=%r after_drag=%r after_ctrl_z=%r "
            "reverted=%s" % (before, after_drag, after_undo, undo_reverted)
        )
    finally:
        view.deleteLater()
