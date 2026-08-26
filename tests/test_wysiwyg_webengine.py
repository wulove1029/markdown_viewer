"""End-to-end WYSIWYG editing in a real QWebEngineView + real Vditor.

Same opt-in gate as tests/test_inline_edit_webengine.py and
tests/test_annotation_bridge.py: headless Chromium can hard-abort a whole
pytest run, so these only execute with ``RUN_WEBENGINE_TESTS=1``. This is the
only place assets/vditor_glue.js and the bundled Vditor library run together
in a real browser rather than against the Node/Vditor stub in
tests/js/vditor_glue_harness.js.
"""

import json
import os

import pytest
from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt, QUrl
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest

from app.wysiwyg_view import WysiwygView

_skip_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _dispose(view):
    """Let Chromium tear its page down before the session QApplication exits."""
    view.close()
    view.deleteLater()
    _wait(150)


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
        _dispose(view)


@_skip_webengine
def test_initial_constructor_uses_final_value_base_and_exact_themes(
    qapp, tmp_path
):
    """Queued startup state reaches the first Vditor constructor exactly once."""
    note_dir = tmp_path / "notes"
    note_dir.mkdir()
    document_path = note_dir / "note.md"
    expected_base = QUrl.fromLocalFile(str(note_dir.resolve()) + "/").toString()
    expected_image_src = QUrl(expected_base).resolved(
        QUrl("images/pic.png")
    ).toString()
    markdown = (
        "# Initial\n\n![alt](images/pic.png)\n\n```python\nprint(1)\n```"
    )

    view = WysiwygView()
    push_calls = []
    theme_calls = []
    original_push = view._push_markdown
    original_apply_theme = view._apply_theme_name

    def record_push(*args):
        push_calls.append(args)
        return original_push(*args)

    def record_theme(*args):
        theme_calls.append(args)
        return original_apply_theme(*args)

    view._push_markdown = record_push
    view._apply_theme_name = record_theme
    try:
        # No event-loop turn has happened yet, so these become constructor
        # state rather than a blank boot followed by setValue()/setTheme().
        view.apply_theme("dark")
        view.set_document_path(document_path)
        view.load_markdown(markdown)
        assert _wait_until(lambda: view._ready)
        assert _wait_until(lambda: "print(1)" in (_current_value(view) or ""))

        state = json.loads(
            _eval(
                view,
                "JSON.stringify((function(){"
                "var glue=window.__wysiwygGlue._state;"
                "var inner=glue.vditor.vditor;"
                "var root=document.querySelector('.vditor');"
                "return {boot:window.__wysiwygInitialBoot,"
                "value:glue.vditor.getValue(),"
                "generation:glue.generation,"
                "focusHost:inner.options.cache.focusHost,"
                "linkBase:inner.options.preview.markdown.linkBase,"
                "editorTheme:inner.options.editorTheme,"
                "theme:inner.options.theme,"
                "codeMirrorTheme:inner.options.codeMirrorTheme,"
                "mermaidTheme:inner.options.mermaidTheme,"
                "imageAttr:(document.querySelector('.vditor-wysiwyg img')||{})"
                ".src||null,"
                "imageMarkup:(document.querySelector('.vditor-wysiwyg img')||{})"
                ".getAttribute&&document.querySelector('.vditor-wysiwyg img')"
                ".getAttribute('src'),"
                "htmlCm:document.documentElement.getAttribute('data-cm-theme'),"
                "rootCm:root&&root.getAttribute('data-cm-theme'),"
                "rootEditor:root&&root.getAttribute('data-editor-theme')};"
                "})())",
            )
        )
        assert state["boot"] == {
            "constructorValue": True,
            "generation": 1,
            "editorTheme": "One Dark",
            "theme": "dark",
            "codeMirrorTheme": "One Dark",
            "mermaidTheme": "Auto",
        }
        assert state["value"].rstrip("\n") == markdown
        assert state["generation"] == 1
        assert state["focusHost"] == "browser"
        assert state["linkBase"] == expected_base
        assert state["editorTheme"] == "One Dark"
        assert state["theme"] == "dark"
        assert state["codeMirrorTheme"] == "One Dark"
        assert state["mermaidTheme"] == "Auto"
        assert state["imageAttr"] == expected_image_src
        assert state["imageMarkup"] == expected_image_src
        assert "![alt](images/pic.png)" in state["value"]
        assert expected_image_src not in state["value"]
        assert state["htmlCm"] == "One Dark"
        assert state["rootCm"] == "One Dark"
        assert state["rootEditor"] == "One Dark"
        assert push_calls == []
        assert theme_calls == []
        assert view._pending_markdown is None

        # Runtime light-theme sync must use the catalog's case-sensitive ID,
        # not the old lower-case value that silently fell back to Auto.
        view.apply_theme("light")
        assert _wait_until(
            lambda: _eval(
                view,
                "document.documentElement.getAttribute('data-cm-theme')",
            )
            == "Github"
        )
        assert theme_calls == [("light",)]
        assert (
            _eval(
                view,
                "window.__wysiwygGlue._state.vditor.vditor.options"
                ".codeMirrorTheme",
            )
            == "Github"
        )
    finally:
        _dispose(view)


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
        _dispose(view)


@_skip_webengine
def test_wysiwyg_typing_crosses_qwebchannel_as_delta_only(qapp):
    """Normal typing must not serialize the full document through QWebChannel."""
    view = WysiwygView()
    deltas = []
    full_pushes = []
    rebuilt = []
    view._bridge.contentDeltaPushed.connect(lambda *args: deltas.append(args))
    view._bridge.contentPushed.connect(lambda *args: full_pushes.append(args))
    view.content_changed.connect(rebuilt.append)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("start 😀")
        assert _wait_until(lambda: _current_value(view) == "start 😀")

        _eval(
            view,
            "window.__wysiwygGlue._state.vditor.insertValue(' plus typed');",
        )
        assert _wait_until(lambda: bool(deltas), timeout_ms=3000)
        assert full_pushes == []
        assert rebuilt and "plus typed" in rebuilt[-1]
        generation, start, delete_count, inserted, revision, final_length = deltas[-1]
        assert generation == view._generation
        assert start >= 0
        assert delete_count >= 0
        assert inserted
        assert revision == 0
        assert final_length == len(rebuilt[-1].encode("utf-16-le")) // 2
    finally:
        _dispose(view)


@_skip_webengine
def test_wysiwyg_ctrl_s_saves_the_live_value_before_the_debounce(qapp):
    """Ctrl+S uses the Office Viewer ``saveWithContent`` contract.

    Insert and save happen in one JavaScript turn, before the glue's 450 ms
    shadow-document debounce can expire.  The save payload itself therefore
    has to carry the newest exact-core value; the legacy parameterless
    ``saveRequested`` signal must not be used.
    """
    view = WysiwygView()
    saves = []
    legacy_saves = []
    view.save_with_content_requested.connect(saves.append)
    view.save_requested.connect(lambda: legacy_saves.append(True))
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("before save")
        assert _wait_until(lambda: _current_value(view) == "before save")

        live_value = _eval(
            view,
            "(function(){"
            "var v=window.__wysiwygGlue._state.vditor;"
            "v.insertValue(' -- newest');"
            "var live=v.getValue();"
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'s',ctrlKey:true,bubbles:true,cancelable:true}));"
            "return live;"
            "})();",
        )
        assert _wait_until(lambda: saves, timeout_ms=3000)
        assert "newest" in live_value
        assert saves[-1] == live_value
        assert legacy_saves == []
    finally:
        _dispose(view)


@_skip_webengine
def test_wysiwyg_clean_escape_stays_in_the_editor(qapp):
    """A clean Esc is never a host transition out of WYSIWYG."""
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
        _wait(150)
        assert escapes == []
    finally:
        _dispose(view)


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
        _dispose(view)


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
        _dispose(view)


@_skip_webengine
def test_wysiwyg_custom_toolbar_button_reaches_the_bridge(qapp):
    """A host action remains a toolbar action; save is tested separately."""
    view = WysiwygView()
    actions = []
    view.toolbar_action.connect(actions.append)
    try:
        assert _wait_until(lambda: view._ready)
        # ``markmap`` is a host-owned bridge adapter.  In contrast, ``save``
        # has the exact-core dirty-state UI and saveWithContent contract.
        _eval(
            view,
            "(function(){"
            "var btn = document.querySelector('[data-type=\"markmap\"]');"
            "if (btn) { btn.click(); }"
            "})();",
        )
        assert _wait_until(lambda: actions, timeout_ms=3000)
        assert actions[-1] == "open_graph"
    finally:
        _dispose(view)


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
        _dispose(view)


@_skip_webengine
def test_exact_office_viewer_toolbar_order_and_native_titles(qapp):
    """The rendered toolbar follows the Office Viewer 4.2 order exactly."""
    expected = [
        "outline",
        "markmap",
        "edit-in-source",
        "save",
        "headings",
        "bold",
        "italic",
        "strike",
        "link",
        "font-color",
        "background-color",
        "export",
        "insert-image",
        "editor-theme",
        "editor-theme-toggle",
        "list",
        "ordered-list",
        "check",
        "table",
        "quote",
        "code",
        "insert-attachment",
        "undo",
        "redo",
        "find",
        "ai-settings",
        "settings",
    ]
    view = WysiwygView()
    try:
        assert _wait_until(lambda: view._ready)
        raw = _eval(
            view,
            "JSON.stringify(Array.from(document.querySelectorAll("
            "'.vditor-toolbar button[data-type]')).map(function(button){"
            "return {type:button.dataset.type,title:button.title,"
            "aria:button.getAttribute('aria-label')};}))",
        )
        buttons = json.loads(raw)
        assert [button["type"] for button in buttons] == expected
        assert all(button["title"] == button["aria"] for button in buttons)
        assert all(button["title"] for button in buttons)
    finally:
        _dispose(view)


@_skip_webengine
def test_exact_outline_is_left_nested_and_targets_real_headings(qapp):
    """The exact core owns outline rendering, hierarchy, and target ids."""
    view = WysiwygView()
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("# Alpha\n\n## Beta\n\n# Gamma")
        assert _wait_until(lambda: "Gamma" in (_current_value(view) or ""))

        raw = _eval(
            view,
            "JSON.stringify((function(){"
            "var inner=window.__wysiwygGlue._state.vditor.vditor;"
            "var button=document.querySelector('[data-type=outline]');"
            "var outline=document.querySelector('.vditor-outline');"
            "inner.outline.render(inner);"
            "var items=Array.from(outline.querySelectorAll('[data-target-id]'));"
            "var content=outline.parentElement;"
            "var editor=document.querySelector('.vditor-wysiwyg');"
            "return {"
            "position:inner.options.outline.position,"
            "button:!!button,resize:!!outline.querySelector('.vditor-outline__resize'),"
            "outlineIndex:Array.from(content.children).indexOf(outline),"
            "editorIndex:Array.from(content.children).indexOf(editor),"
            "items:items.map(function(item){return {"
            "text:item.textContent.trim(),target:item.dataset.targetId,"
            "exists:!!document.getElementById(item.dataset.targetId),"
            "nested:!!item.closest('li').parentElement.closest('li')};})"
            "};})())",
        )
        outline = json.loads(raw)
        assert outline["position"] == "left"
        assert outline["button"] is True
        assert outline["resize"] is True
        assert outline["outlineIndex"] < outline["editorIndex"]
        assert [item["text"] for item in outline["items"]] == [
            "Alpha",
            "Beta",
            "Gamma",
        ]
        assert [item["nested"] for item in outline["items"]] == [
            False,
            True,
            False,
        ]
        assert all(item["exists"] for item in outline["items"])
    finally:
        _dispose(view)


@_skip_webengine
def test_exact_outline_defaults_to_280_and_preserves_persisted_width(qapp):
    """The first-use width is 280px; exact-core resize storage still wins."""
    storage_view = WysiwygView()
    default_view = None
    persisted_view = None
    original_raw = None
    try:
        assert _wait_until(lambda: storage_view._ready)
        original_raw = _eval(
            storage_view,
            "localStorage.getItem('vditor-global-settings')",
        )
        try:
            clean_settings = json.loads(original_raw) if original_raw else {}
        except (TypeError, ValueError):
            clean_settings = {}
        if not isinstance(clean_settings, dict):
            clean_settings = {}
        clean_settings.pop("outlineWidth", None)
        clean_settings.pop("outlineEnable", None)
        clean_raw = json.dumps(clean_settings, ensure_ascii=False)
        _eval(
            storage_view,
            "localStorage.setItem('vditor-global-settings',"
            + json.dumps(clean_raw)
            + ")",
        )

        default_view = WysiwygView()
        default_view.resize(1000, 700)
        default_view.show()
        assert _wait_until(lambda: default_view._ready)
        default_state = json.loads(
            _eval(
                default_view,
                "JSON.stringify((function(){var inner=window.__wysiwygGlue"
                "._state.vditor.vditor;var outline=document.querySelector("
                "'.vditor-outline');return {configured:inner.options.outline.width,"
                "rendered:Math.round(outline.getBoundingClientRect().width),"
                "resize:!!outline.querySelector('.vditor-outline__resize')};})())",
            )
        )
        assert default_state == {
            "configured": 280,
            "rendered": 280,
            "resize": True,
        }

        resize_limits = json.loads(
            _eval(
                default_view,
                "JSON.stringify((function(){var outline=document.querySelector("
                "'.vditor-outline');var handle=outline.querySelector("
                "'.vditor-outline__resize');function drag(delta){var start="
                "outline.getBoundingClientRect().right;handle.dispatchEvent("
                "new MouseEvent('mousedown',{clientX:start,bubbles:true,"
                "cancelable:true}));document.dispatchEvent(new MouseEvent("
                "'mousemove',{clientX:start+delta,bubbles:true}));"
                "document.dispatchEvent(new MouseEvent('mouseup',"
                "{clientX:start+delta,bubbles:true}));return Math.round("
                "outline.getBoundingClientRect().width);}return [drag(1000),"
                "drag(-1000)];})())",
            )
        )
        assert resize_limits == [480, 120]
        _dispose(default_view)
        default_view = None

        persisted_settings = dict(clean_settings)
        persisted_settings.update({"outlineEnable": True, "outlineWidth": 333})
        persisted_raw = json.dumps(persisted_settings, ensure_ascii=False)
        _eval(
            storage_view,
            "localStorage.setItem('vditor-global-settings',"
            + json.dumps(persisted_raw)
            + ")",
        )
        persisted_view = WysiwygView()
        persisted_view.resize(1000, 700)
        persisted_view.show()
        assert _wait_until(lambda: persisted_view._ready)
        persisted_state = json.loads(
            _eval(
                persisted_view,
                "JSON.stringify((function(){var inner=window.__wysiwygGlue"
                "._state.vditor.vditor;var outline=document.querySelector("
                "'.vditor-outline');return {configured:inner.options.outline.width,"
                "rendered:Math.round(outline.getBoundingClientRect().width),"
                "resize:!!outline.querySelector('.vditor-outline__resize')};})())",
            )
        )
        assert persisted_state == {
            "configured": 280,
            "rendered": 333,
            "resize": True,
        }
    finally:
        if original_raw is None:
            _eval(
                storage_view,
                "localStorage.removeItem('vditor-global-settings')",
            )
        else:
            _eval(
                storage_view,
                "localStorage.setItem('vditor-global-settings',"
                + json.dumps(original_raw)
                + ")",
            )
        if persisted_view is not None:
            _dispose(persisted_view)
        if default_view is not None:
            _dispose(default_view)
        _dispose(storage_view)


@_skip_webengine
def test_exact_core_owns_block_handles_without_legacy_overlay(qapp):
    """The glue detects Office Viewer's handle and never adds its fallback."""
    view = WysiwygView()
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("Block One\n\nBlock Two")
        assert _wait_until(lambda: "Block Two" in (_current_value(view) or ""))

        raw = _eval(
            view,
            "JSON.stringify((function(){"
            "window.__wysiwygGlue._installBlockHandles();"
            "window.__wysiwygGlue._installBlockHandles();"
            "var root=document.querySelector('.vditor-wysiwyg');"
            "return {"
            "exact:root.querySelectorAll(':scope > .vditor-block-handle').length,"
            "insert:!!root.querySelector('.vditor-block-handle__insert'),"
            "drag:!!root.querySelector('.vditor-block-handle__drag'),"
            "drop:!!root.querySelector('.vditor-block-handle__drop-line'),"
            "legacy:document.querySelectorAll('.vditor-block-handle-group').length,"
            "installed:window.__wysiwygGlue._state.handlesInstalled,"
            "fallbackAllocated:!!window.__wysiwygGlue._state.handleGroup"
            "};})())",
        )
        handles = json.loads(raw)
        assert handles == {
            "exact": 1,
            "insert": True,
            "drag": True,
            "drop": True,
            "legacy": 0,
            "installed": True,
            "fallbackAllocated": False,
        }
    finally:
        _dispose(view)


def _exact_block_rects(view):
    """Return content blocks only, excluding the fork's boundary sentinels."""
    raw = _eval(
        view,
        "JSON.stringify((function(){"
        "var editor=document.querySelector("
        "'.vditor-wysiwyg > pre.vditor-reset');"
        "return Array.from(editor.children).filter(function(node){"
        "return node.matches('[data-block]')&&"
        "!node.classList.contains('vditor-editor-boundary');"
        "}).map(function(node){var rect=node.getBoundingClientRect();return {"
        "text:node.textContent,left:rect.left,top:rect.top,"
        "width:rect.width,height:rect.height};});})())",
    )
    return json.loads(raw)


@_skip_webengine
def test_exact_block_drag_via_qwindow_and_ctrl_z_restores_document(qapp):
    """Drive Office's native handle with OS events and undo the whole move."""
    view = WysiwygView()
    view.resize(1000, 700)
    view.show()
    pushed = []
    view.content_changed.connect(pushed.append)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("Block One\n\nBlock Two\n\nBlock Three")
        assert _wait_until(
            lambda: "Block Three" in (_current_value(view) or ""),
            timeout_ms=5000,
        )
        # setValue becomes readable before the exact core finishes seeding its
        # undo baseline.  Its default undoDelay is currently 600 ms, so a fixed
        # sleep below that threshold makes the drag become history entry #1
        # (and therefore impossible to undo).  Wait for the real baseline
        # instead of coupling this OS-event test to a timing constant.
        assert _wait_until(
            lambda: _eval(
                view,
                "window.__wysiwygGlue._state.vditor.vditor.undo.wysiwyg"
                ".undoStack.length",
            )
            >= 1,
            timeout_ms=3000,
        )
        before = _current_value(view)
        blocks = _exact_block_rects(view)
        assert [block["text"] for block in blocks] == [
            "Block One",
            "Block Two",
            "Block Three",
        ]

        page_window = view.windowHandle()
        assert page_window is not None
        page_window.requestActivate()
        second = blocks[1]
        second_point = QPoint(
            round(second["left"] + 30),
            round(second["top"] + second["height"] / 2),
        )
        QTest.mouseMove(page_window, second_point, 20)
        assert _wait_until(
            lambda: bool(
                _eval(
                    view,
                    "document.querySelector("
                    "'.vditor-wysiwyg > .vditor-block-handle')"
                    ".classList.contains('vditor-block-handle--visible')",
                )
            ),
            timeout_ms=3000,
        )

        handle = json.loads(
            _eval(
                view,
                "JSON.stringify(document.querySelector("
                "'.vditor-wysiwyg > .vditor-block-handle "
                ".vditor-block-handle__drag')"
                ".getBoundingClientRect().toJSON())",
            )
        )
        handle_point = QPoint(
            round(handle["left"] + handle["width"] / 2),
            round(handle["top"] + handle["height"] / 2),
        )
        first = blocks[0]
        drop_point = QPoint(
            round(first["left"] + 20),
            round(first["top"] + 2),
        )

        # These are QWindow mouse events, not JavaScript-created PointerEvents.
        QTest.mouseMove(page_window, handle_point, 20)
        QTest.mousePress(
            page_window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            handle_point,
            20,
        )
        QTest.mouseMove(
            page_window,
            QPoint(handle_point.x() + 12, handle_point.y() + 2),
            100,
        )
        QTest.mouseMove(page_window, drop_point, 200)
        assert _wait_until(
            lambda: _eval(
                view,
                "getComputedStyle(document.querySelector("
                "'.vditor-wysiwyg > .vditor-block-handle__drop-line'))"
                ".display",
            )
            == "block",
            timeout_ms=3000,
        )
        QTest.mouseRelease(
            page_window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            drop_point,
            20,
        )

        def reordered(value):
            return (
                "Block Two" in value
                and "Block One" in value
                and value.index("Block Two") < value.index("Block One")
            )

        assert _wait_until(
            lambda: reordered(_current_value(view) or ""), timeout_ms=5000
        )
        assert _wait_until(
            lambda: bool(pushed) and reordered(pushed[-1]), timeout_ms=3000
        )
        # The input bridge fires as soon as the DOM changes, while the exact
        # core finishes grouping the native move into its undo history on the
        # following turn.  Wait for that user-visible transaction to settle.
        _wait(500)

        # The drag handle owns focus after release.  A real click in the first
        # content block restores the same contenteditable focus a user has
        # before pressing Ctrl+Z.
        moved_blocks = _exact_block_rects(view)
        assert [block["text"] for block in moved_blocks] == [
            "Block Two",
            "Block One",
            "Block Three",
        ]
        first_moved = moved_blocks[0]
        editor_point = QPoint(
            round(first_moved["left"] + 30),
            round(first_moved["top"] + first_moved["height"] / 2),
        )
        QTest.mouseClick(
            page_window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            editor_point,
            20,
        )
        assert _wait_until(
            lambda: bool(
                _eval(
                    view,
                    "document.activeElement.matches("
                    "'.vditor-wysiwyg > pre.vditor-reset')",
                )
            )
        )
        _wait(150)
        QTest.keyClick(page_window, Qt.Key_Z, Qt.ControlModifier)

        restored_value = None

        def restored_to_original():
            nonlocal restored_value
            restored_value = _current_value(view)
            return restored_value == before

        assert _wait_until(restored_to_original, timeout_ms=5000), (
            before,
            restored_value,
        )
        assert _wait_until(
            lambda: bool(pushed) and pushed[-1].rstrip("\n") == before,
            timeout_ms=3000,
        )
        restored = _exact_block_rects(view)
        assert [block["text"] for block in restored] == [
            "Block One",
            "Block Two",
            "Block Three",
        ]
        boundary_state = json.loads(
            _eval(
                view,
                "JSON.stringify((function(){var editor=document.querySelector("
                "'.vditor-wysiwyg > pre.vditor-reset');return {"
                "count:editor.querySelectorAll("
                "':scope > .vditor-editor-boundary').length,"
                "first:editor.firstElementChild.classList.contains("
                "'vditor-editor-boundary'),"
                "last:editor.lastElementChild.classList.contains("
                "'vditor-editor-boundary')};})())",
            )
        )
        assert boundary_state == {"count": 2, "first": True, "last": True}
    finally:
        _dispose(view)


def _activate_exact_code_block(view):
    _eval(
        view,
        "(function(){"
        "var pre=document.querySelector('[data-type=code-block] pre');"
        "if(!pre){return false;}"
        "pre.dispatchEvent(new MouseEvent('mousedown',"
        "{bubbles:true,cancelable:true}));"
        "pre.click();"
        "return true;"
        "})();",
    )
    assert _wait_until(
        lambda: bool(_eval(view, "!!document.querySelector('.vditor-cm-chrome')")),
        timeout_ms=5000,
    )


@_skip_webengine
def test_exact_code_block_language_theme_and_copy_controls(qapp):
    """Exercise the real lazy CodeMirror chrome and its interactive controls."""
    view = WysiwygView()
    original_theme = None
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("```python\nprint(1)\n```\n\nTail")
        assert _wait_until(lambda: "print(1)" in (_current_value(view) or ""))
        _activate_exact_code_block(view)

        raw = _eval(
            view,
            "JSON.stringify((function(){"
            "var block=document.querySelector('[data-type=code-block]');"
            "var chrome=block.querySelector('.vditor-cm-chrome');"
            "return {"
            "codeMirror:block.classList.contains('vditor-code-block--cm'),"
            "language:chrome.querySelector('.vditor-cm-chrome__lang-label').textContent,"
            "theme:!!chrome.querySelector('.vditor-cm-chrome__theme-trigger'),"
            "copy:!!chrome.querySelector('.vditor-cm-chrome__copy'),"
            "remove:!!chrome.querySelector('.vditor-cm-chrome__delete')"
            "};})())",
        )
        chrome = json.loads(raw)
        assert chrome == {
            "codeMirror": True,
            "language": "python",
            "theme": True,
            "copy": True,
            "remove": True,
        }

        raw = _eval(
            view,
            "JSON.stringify((function(){"
            "document.querySelector('.vditor-cm-chrome__lang-trigger').click();"
            "var wrap=document.querySelector('.vditor-cm-chrome__lang');"
            "return {open:wrap.classList.contains('vditor-cm-chrome__lang--open'),"
            "languages:Array.from(wrap.querySelectorAll('[data-lang]'))"
            ".map(function(item){return item.dataset.lang;})};})())",
        )
        languages = json.loads(raw)
        assert languages["open"] is True
        assert "JavaScript" in languages["languages"]

        _eval(
            view,
            "document.querySelector("
            "'.vditor-cm-chrome__lang-item[data-lang=\"JavaScript\"]'"
            ").click();",
        )
        assert _wait_until(
            lambda: "```JavaScript" in (_current_value(view) or ""),
            timeout_ms=5000,
        )

        original_theme = _eval(
            view,
            "window.__wysiwygGlue._state.vditor.vditor.options.codeMirrorTheme",
        )
        raw = _eval(
            view,
            "JSON.stringify((function(){"
            "document.querySelector('.vditor-cm-chrome__theme-trigger').click();"
            "return Array.from(document.querySelectorAll("
            "'.vditor-cm-chrome__theme-panel button[data-theme]'))"
            ".map(function(button){return button.dataset.theme;});})())",
        )
        themes = json.loads(raw)
        assert {"Auto", "Github", "One Dark"}.issubset(themes)
        selected_theme = "One Dark" if original_theme != "One Dark" else "Github"
        _eval(
            view,
            "document.querySelector("
            f"'.vditor-cm-chrome__theme-panel button[data-theme={json.dumps(selected_theme)}]'"
            ").click();",
        )
        assert _wait_until(
            lambda: _eval(
                view,
                "document.documentElement.getAttribute('data-cm-theme')",
            )
            == selected_theme
        )

        # Headless Chromium denies an untrusted programmatic clipboard write.
        # Stub only that OS boundary; the exact-core copy click handler,
        # Markdown-to-plain-text conversion, and success UI remain real.
        _eval(
            view,
            "Object.defineProperty(navigator,'clipboard',{configurable:true,"
            "value:{writeText:function(text){window.__copiedText=text;"
            "return Promise.resolve();}}});"
            "document.querySelector('.vditor-cm-chrome__copy').click();",
        )
        assert _wait_until(
            lambda: bool(
                _eval(
                    view,
                    "document.querySelector('.vditor-cm-chrome__copy')"
                    ".classList.contains('vditor-cm-chrome__copy--done')",
                )
            )
        )
        assert _eval(view, "window.__copiedText") == "print(1)"
    finally:
        if original_theme:
            _eval(
                view,
                "(function(){"
                "var trigger=document.querySelector("
                "'.vditor-cm-chrome__theme-trigger');"
                "if(!trigger){return;}"
                "trigger.click();"
                "var button=document.querySelector("
                f"'.vditor-cm-chrome__theme-panel button[data-theme={json.dumps(original_theme)}]'"
                ");"
                "if(button){button.click();}"
                "})();",
            )
        _dispose(view)


@_skip_webengine
def test_exact_code_block_delete_control_updates_markdown(qapp):
    """Delete in exact-core chrome removes the fenced block through input."""
    view = WysiwygView()
    pushed = []
    view.content_changed.connect(pushed.append)
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("```python\nprint(1)\n```\n\nTail")
        assert _wait_until(lambda: "print(1)" in (_current_value(view) or ""))
        _activate_exact_code_block(view)
        _eval(view, "document.querySelector('.vditor-cm-chrome__delete').click();")

        assert _wait_until(
            lambda: _eval(
                view, "document.querySelectorAll('[data-type=code-block]').length"
            )
            == 0,
            timeout_ms=5000,
        )
        assert _current_value(view) == "Tail"
        assert _wait_until(lambda: bool(pushed), timeout_ms=3000)
        assert pushed[-1].rstrip("\n") == "Tail"
    finally:
        _dispose(view)


@_skip_webengine
def test_relative_image_uses_document_link_base_without_rewriting_markdown(
    qapp, tmp_path
):
    """Relative image URLs resolve beside the document and stay relative."""
    note_dir = tmp_path / "notes"
    note_dir.mkdir()
    document_path = note_dir / "note.md"
    expected_base = QUrl.fromLocalFile(str(note_dir.resolve()) + "/").toString()
    expected_src = QUrl(expected_base).resolved(QUrl("images/pic.png")).toString()

    view = WysiwygView()
    try:
        assert _wait_until(lambda: view._ready)
        view.set_document_path(document_path)
        view.load_markdown("![alt](images/pic.png)")
        assert _wait_until(lambda: "images/pic.png" in (_current_value(view) or ""))

        raw = _eval(
            view,
            "JSON.stringify((function(){"
            "var image=document.querySelector('.vditor-wysiwyg img');"
            "var inner=window.__wysiwygGlue._state.vditor.vditor;"
            "return {attr:image&&image.getAttribute('src'),"
            "property:image&&image.src,"
            "linkBase:inner.options.preview.markdown.linkBase,"
            "markdown:window.__wysiwygGlue.getValue()};})())",
        )
        image = json.loads(raw)
        assert image["linkBase"] == expected_base
        assert image["attr"] == expected_src
        assert image["property"] == expected_src
        assert "![alt](images/pic.png)" in image["markdown"]
        assert expected_src not in image["markdown"]
    finally:
        _dispose(view)


@_skip_webengine
def test_each_document_restores_its_own_caret_and_scroll(qapp, tmp_path):
    """A shared WebEngine keeps tab sessions separate without stale core cache."""
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    token = "TARGET_SESSION_TOKEN"
    first_markdown = "\n\n".join(
        f"Paragraph {index} " + (token if index == 75 else "content")
        for index in range(120)
    )
    second_markdown = "# Second document\n\nNothing from the first tab"

    view = WysiwygView()
    view.resize(800, 500)
    view.show()
    try:
        assert _wait_until(lambda: view._ready)
        view.set_document_path(first)
        first_session_id = view._document_session_id
        view.load_markdown(first_markdown)
        assert _wait_until(lambda: token in (_current_value(view) or ""))

        selected = json.loads(
            _eval(
                view,
                "JSON.stringify((function(){"
                "var inner=window.__wysiwygGlue._state.vditor.vditor;"
                "var surface=inner[inner.currentMode].element;"
                "var walker=document.createTreeWalker(surface,NodeFilter.SHOW_TEXT);"
                f"var token={json.dumps(token)};var node=null,index=-1;"
                "while(walker.nextNode()){index=walker.currentNode.textContent"
                ".indexOf(token);if(index>=0){node=walker.currentNode;break;}}"
                "if(!node){return {ok:false};}surface.focus({preventScroll:true});"
                "var range=document.createRange();"
                "range.setStart(node,index+7);range.collapse(true);"
                "var selection=window.getSelection();selection.removeAllRanges();"
                "selection.addRange(range);inner[inner.currentMode].range=range.cloneRange();"
                "surface.scrollTop=Math.max(0,"
                "node.parentElement.offsetTop-120);return {ok:true,"
                "scrollTop:surface.scrollTop,text:selection.anchorNode.textContent,"
                "offset:selection.anchorOffset};})())",
            )
        )
        assert selected["ok"] is True
        assert selected["scrollTop"] > 0

        view.set_document_path(second)
        second_session_id = view._document_session_id
        assert second_session_id != first_session_id
        view.load_markdown(second_markdown)
        assert _wait_until(lambda: _current_value(view) == second_markdown)

        view.set_document_path(first)
        assert view._document_session_id == first_session_id
        view.load_markdown(first_markdown)
        assert _wait_until(lambda: token in (_current_value(view) or ""))

        def restored_session():
            raw = _eval(
                view,
                "JSON.stringify((function(){"
                "var inner=window.__wysiwygGlue._state.vditor.vditor;"
                "var surface=inner[inner.currentMode].element;"
                "var selection=window.getSelection();var node=selection.anchorNode;"
                "return {text:node&&node.textContent||'',"
                "offset:selection.anchorOffset,scrollTop:surface.scrollTop,"
                "cacheId:inner.options.cache.id};})())",
            )
            return json.loads(raw) if isinstance(raw, str) else {}

        def caret_is_restored():
            current = restored_session()
            return (
                token in current.get("text", "")
                and current.get("offset") == selected["offset"]
            )

        # setValue briefly leaves the fork's default caret at offset 0; wait
        # for glue's double-animation-frame document-session restore.
        assert _wait_until(caret_is_restored, timeout_ms=5000)
        restored = restored_session()
        debug_session = json.loads(
            _eval(
                view,
                "JSON.stringify(window.__wysiwygGlue._state.documentSessions["
                f"{json.dumps(first_session_id)}]||null)",
            )
        )
        assert restored["offset"] == selected["offset"], (
            selected,
            restored,
            {
                key: debug_session.get(key)
                for key in (
                    "startPath",
                    "startOffset",
                    "startTextOffset",
                    "endTextOffset",
                    "scrollTop",
                )
            },
        )
        assert restored["scrollTop"] > 0
        assert restored["cacheId"] == first_session_id
        assert "first.md" not in first_session_id
    finally:
        _dispose(view)


@_skip_webengine
def test_long_lazy_code_block_restores_logical_selection_and_scroll(
    qapp, tmp_path
):
    """A reused WebEngine restores virtualized CodeMirror state per document."""
    first = tmp_path / "long-code.md"
    second = tmp_path / "other.md"
    prelude = "\n\n".join(
        f"Prelude paragraph {index} keeps the code block off screen."
        for index in range(120)
    )
    code = "\n".join(
        f"line_{index:04d} = {index}" for index in range(600)
    )
    first_markdown = f"{prelude}\n\n```python\n{code}\n```\n\nTail"
    second_markdown = "# Other document\n\nNo CodeMirror selection here."

    view = WysiwygView()
    view.resize(800, 500)
    view.show()
    try:
        assert _wait_until(lambda: view._ready)
        view.set_document_path(first)
        first_session_id = view._document_session_id
        view.load_markdown(first_markdown)
        assert _wait_until(
            lambda: "line_0599 = 599" in (_current_value(view) or ""),
            timeout_ms=8000,
        )

        def code_state():
            raw = _eval(
                view,
                "JSON.stringify((function(){"
                "var inner=window.__wysiwygGlue._state.vditor.vditor;"
                "var surface=inner[inner.currentMode].element;"
                "var block=surface.querySelector('[data-type=code-block]');"
                "var content=block&&block.querySelector('.cm-content');"
                "var cm=content&&content.cmTile&&content.cmTile.view;"
                "var main=cm&&cm.state.selection.main;"
                "return {block:!!block,mounted:!!cm,"
                "blockTop:block&&block.offsetTop||0,"
                "blockHeight:block&&block.offsetHeight||0,"
                "clientHeight:surface.clientHeight,scrollTop:surface.scrollTop,"
                "anchor:main&&main.anchor,head:main&&main.head,"
                "docLength:cm&&cm.state.doc.length,"
                "renderedLines:content&&content.querySelectorAll('.cm-line').length||0,"
                "active:document.activeElement===content,"
                "cacheId:inner.options.cache.id};})())",
            )
            return json.loads(raw) if isinstance(raw, str) else {}

        assert _wait_until(lambda: code_state().get("block"), timeout_ms=5000)
        initially_off_screen = code_state()
        assert initially_off_screen["blockTop"] > (
            initially_off_screen["scrollTop"]
            + initially_off_screen["clientHeight"]
        )
        assert initially_off_screen["mounted"] is False

        # Scrolling the root into the distant block lets the exact bundle's
        # IntersectionObserver mount CodeMirror.  Pick a reverse selection
        # near the end, outside CodeMirror's initially rendered line window.
        _eval(
            view,
            "(function(){"
            "var inner=window.__wysiwygGlue._state.vditor.vditor;"
            "var surface=inner[inner.currentMode].element;"
            "var block=surface.querySelector('[data-type=code-block]');"
            "surface.scrollTop=Math.max(0,block.offsetTop-80);"
            "})();",
        )
        assert _wait_until(lambda: code_state().get("mounted"), timeout_ms=5000)

        selected = json.loads(
            _eval(
                view,
                "JSON.stringify((function(){"
                "var inner=window.__wysiwygGlue._state.vditor.vditor;"
                "var surface=inner[inner.currentMode].element;"
                "var block=surface.querySelector('[data-type=code-block]');"
                "var content=block.querySelector('.cm-content');"
                "var cm=content.cmTile.view;var length=cm.state.doc.length;"
                "var anchor=length-11,head=length-29;"
                "surface.scrollTop=Math.max(0,block.offsetTop+block.offsetHeight"
                "-surface.clientHeight-80);"
                "cm.dispatch({selection:{anchor:anchor,head:head},"
                "scrollIntoView:false});cm.focus();"
                "var savedScroll=surface.scrollTop;surface.scrollTop=savedScroll;"
                "var main=cm.state.selection.main;"
                "return {anchor:main.anchor,head:main.head,scrollTop:surface.scrollTop,"
                "docLength:length,renderedLines:content.querySelectorAll('.cm-line').length,"
                "active:document.activeElement===content};})())",
            )
        )
        _wait(200)
        selected = code_state()
        assert selected["active"] is True
        assert selected["anchor"] == selected["docLength"] - 11
        assert selected["head"] == selected["docLength"] - 29
        assert 0 < selected["renderedLines"] < 600
        assert selected["scrollTop"] > initially_off_screen["clientHeight"]

        view.set_document_path(second)
        second_session_id = view._document_session_id
        assert second_session_id != first_session_id
        view.load_markdown(second_markdown)
        assert _wait_until(lambda: _current_value(view) == second_markdown)

        stored = json.loads(
            _eval(
                view,
                "JSON.stringify(window.__wysiwygGlue._state.documentSessions["
                f"{json.dumps(first_session_id)}]||null)",
            )
        )
        assert stored["type"] == "cm"
        assert stored["blockIndex"] == 0
        assert stored["anchor"] == selected["anchor"]
        assert stored["head"] == selected["head"]
        assert stored["scrollTop"] == selected["scrollTop"]

        view.set_document_path(first)
        assert view._document_session_id == first_session_id
        view.load_markdown(first_markdown)
        assert _wait_until(
            lambda: "line_0599 = 599" in (_current_value(view) or ""),
            timeout_ms=8000,
        )

        observed = {}

        def code_session_is_restored():
            current = code_state()
            observed.clear()
            observed.update(current)
            return (
                current.get("mounted") is True
                and current.get("anchor") == selected["anchor"]
                and current.get("head") == selected["head"]
                and current.get("scrollTop") == selected["scrollTop"]
            )

        # Restoration must first apply the saved root scroll so the distant
        # placeholder mounts, then restore logical CodeMirror positions.
        assert _wait_until(code_session_is_restored, timeout_ms=8000), (
            {
                "anchor": selected["anchor"],
                "head": selected["head"],
                "scrollTop": selected["scrollTop"],
            },
            {
                "anchor": observed.get("anchor"),
                "head": observed.get("head"),
                "scrollTop": observed.get("scrollTop"),
                "mounted": observed.get("mounted"),
                "active": observed.get("active"),
            },
        )
        restored = code_state()
        assert restored["anchor"] == selected["anchor"]
        assert restored["head"] == selected["head"]
        assert restored["scrollTop"] == selected["scrollTop"]
        assert restored["active"] is True
        assert restored["cacheId"] == first_session_id
    finally:
        _dispose(view)


@_skip_webengine
def test_exact_toolbar_stays_single_row_at_800_css_pixels(qapp):
    """Office actions compact or scroll horizontally instead of wrapping."""
    view = WysiwygView()
    view.resize(800, 600)
    view.show()
    try:
        assert _wait_until(lambda: view._ready)
        script = (
            "JSON.stringify((function(){var bar=document.querySelector("
            "'.vditor-toolbar');var buttons=Array.from(bar.querySelectorAll("
            "'button[data-type]'));var boxes=buttons.map(function(button){"
            "var rect=button.getBoundingClientRect();return {type:button.dataset.type,"
            "top:Math.round(rect.top),width:rect.width,height:rect.height};});"
            "var tops=boxes.filter(function(box){return box.width&&box.height;})"
            ".map(function(box){return box.top;});return {"
            "style:!!document.querySelector('#wysiwyg-office-layout'),"
            "width:bar.clientWidth,scrollWidth:bar.scrollWidth,"
            "height:bar.clientHeight,scrollHeight:bar.scrollHeight,"
            "tops:Array.from(new Set(tops)),wrap:getComputedStyle(bar).flexWrap,"
            "hidden:boxes.filter(function(box){return !box.width||!box.height;})"
            ".map(function(box){return box.type;}),"
            "overflowX:getComputedStyle(bar).overflowX,"
            "visibleBreaks:Array.from(bar.querySelectorAll("
            "'.vditor-toolbar__br')).filter(function(br){"
            "return getComputedStyle(br).display!=='none';}).length};})())"
        )
        toolbar = json.loads(_eval(view, script))
        assert toolbar["style"] is True
        assert 790 <= toolbar["width"] <= 800
        assert toolbar["wrap"] == "nowrap"
        assert toolbar["overflowX"] == "auto"
        assert toolbar["tops"] and len(toolbar["tops"]) == 1
        assert toolbar["height"] < 50
        assert toolbar["scrollHeight"] <= toolbar["height"]
        assert toolbar["scrollWidth"] >= toolbar["width"]
        assert toolbar["visibleBreaks"] == 0
        assert toolbar["hidden"] == []

        view.resize(700, 600)
        _wait(150)
        narrow = json.loads(_eval(view, script))
        assert narrow["wrap"] == "nowrap"
        assert len(narrow["tops"]) == 1
        assert narrow["height"] < 50
        assert narrow["scrollWidth"] > narrow["width"]
    finally:
        _dispose(view)


@_skip_webengine
def test_escape_closes_exact_toolbar_and_find_overlays_without_host_exit(qapp):
    """Esc dismisses Office settings/find without emitting ``esc_requested``."""
    view = WysiwygView()
    escapes = []
    view.esc_requested.connect(lambda: escapes.append(True))
    try:
        assert _wait_until(lambda: view._ready)
        for data_type in ["editor-theme", "settings", "ai-settings"]:
            opened = _eval(
                view,
                "(function(){var button=document.querySelector("
                f"\"button[data-type='{data_type}']\""
                ");button.click();var panel=button.parentElement.querySelector("
                "':scope > .vditor-hint');return panel.style.display;})()",
            )
            assert opened == "block"
            _eval(
                view,
                "document.dispatchEvent(new KeyboardEvent('keydown',"
                "{key:'Escape',bubbles:true,cancelable:true}));",
            )
            assert _wait_until(
                lambda data_type=data_type: _eval(
                    view,
                    "document.querySelector("
                    f"\"button[data-type='{data_type}']\""
                    ").parentElement.querySelector(':scope > .vditor-hint')"
                    ".style.display",
                )
                == "none"
            )
            assert escapes == []

        find_open = _eval(
            view,
            "(function(){document.querySelector("
            "\"button[data-type='find']\").click();var bar=document.querySelector("
            "'.vditor-find-bar');return bar.style.display;})()",
        )
        assert find_open != "none"
        _eval(
            view,
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'Escape',bubbles:true,cancelable:true}));",
        )
        assert _wait_until(
            lambda: _eval(
                view,
                "document.querySelector('.vditor-find-bar').style.display",
            )
            == "none"
        )

        _eval(
            view,
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'Escape',bubbles:true,cancelable:true}));",
        )
        _wait(150)
        assert escapes == []
    finally:
        _dispose(view)


@_skip_webengine
def test_escape_closes_exact_code_language_and_theme_overlays(qapp):
    """Exact CodeMirror chrome consumes Esc without leaving WYSIWYG."""
    view = WysiwygView()
    escapes = []
    view.esc_requested.connect(lambda: escapes.append(True))
    try:
        assert _wait_until(lambda: view._ready)
        view.load_markdown("```python\nprint(1)\n```\n\nTail")
        assert _wait_until(lambda: "print(1)" in (_current_value(view) or ""))
        _activate_exact_code_block(view)

        for trigger, open_selector in [
            (
                ".vditor-cm-chrome__lang-trigger",
                ".vditor-cm-chrome__lang--open",
            ),
            (
                ".vditor-cm-chrome__theme-trigger",
                ".vditor-cm-chrome__theme--open",
            ),
        ]:
            _eval(view, f"document.querySelector({json.dumps(trigger)}).click();")
            assert _wait_until(
                lambda open_selector=open_selector: bool(
                    _eval(view, f"!!document.querySelector({json.dumps(open_selector)})")
                )
            )
            _eval(
                view,
                "document.dispatchEvent(new KeyboardEvent('keydown',"
                "{key:'Escape',bubbles:true,cancelable:true}));",
            )
            assert _wait_until(
                lambda open_selector=open_selector: not bool(
                    _eval(view, f"!!document.querySelector({json.dumps(open_selector)})")
                )
            )
            assert escapes == []
    finally:
        _dispose(view)
