"""Real Chromium clipboard events and live Office selection preservation."""
import json
import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage

from app import wysiwyg_view
from app.paste_preview import parse_clipboard_table
from tests.test_wysiwyg_webengine import _current_value, _dispose, _eval, _wait, _wait_until

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1", reason="requires real Chromium",
)


@pytest.fixture
def office(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(wysiwyg_view, "QSettings", lambda *_: QSettings(
        str(tmp_path / "office.ini"), QSettings.Format.IniFormat,
    ))
    view = wysiwyg_view.WysiwygView()
    view.resize(1100, 750)
    view.show()
    assert _wait_until(lambda: view._ready)
    assert _eval(view, "typeof window.__wysiwygGlue.resolvePastePreview") == "function"
    # WebEngine profiles share localStorage even with isolated QSettings. A
    # unique document avoids restoring a different test's "untitled" caret.
    view.set_document_path(tmp_path / "note.md")
    view.load_markdown("before selected after")
    assert _wait_until(lambda: _current_value(view) == "before selected after")
    # setValue publishes content before the fork finishes its initial history
    # transaction. Establish a usable editor before programmatic selection.
    assert _wait_until(lambda: _eval(
        view, "window.__wysiwygGlue._state.vditor.vditor.undo.wysiwyg.undoStack.length",
    ) >= 1)
    yield view
    QGuiApplication.clipboard().clear()
    _dispose(view)


def _select_word(view):
    assert _eval(view, """(function () {
      var inner=window.__wysiwygGlue._state.vditor.vditor;
      var surface=inner.wysiwyg.element;
      var walker=document.createTreeWalker(surface, NodeFilter.SHOW_TEXT), node;
      while ((node=walker.nextNode())) {
        var at=node.textContent.indexOf('selected');
        if (at<0) continue;
        var range=document.createRange(); range.setStart(node,at); range.setEnd(node,at+8);
        surface.focus(); window.getSelection().removeAllRanges();
        window.getSelection().addRange(range); inner.wysiwyg.range=range.cloneRange();
        return window.getSelection().toString();
      } return '';
    })()""") == "selected"


def _paste(view, text, *, method="action", html=None):
    _eval(view, """document.addEventListener('paste', function(e) {
      window.__pasteProbe={types:Array.from(e.clipboardData.types),
        text:e.clipboardData.getData('text/plain'),html:e.clipboardData.getData('text/html'),
        target:e.target.className};
    }, true);""")
    mime = QMimeData()
    mime.setText(text)
    if html:
        mime.setHtml(html)
    QGuiApplication.clipboard().setMimeData(mime)
    if method == "keyboard":
        window = view.windowHandle()
        window.requestActivate()
        QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    else:
        view.page().triggerAction(QWebEnginePage.WebAction.Paste)


@pytest.mark.parametrize("method", ["action", "keyboard"])
def test_office_native_paste_previews_then_replaces_original_selection_once(office, method):
    requests, resolved = [], []
    office.paste_preview_requested.connect(lambda *args: requests.append(args))
    _select_word(office)
    _paste(office, "name\tcount\napple\t3", method=method)
    assert _wait_until(lambda: bool(requests)), _eval(
        office, "JSON.stringify({probe:window.__pasteProbe,pending:"
        "window.__wysiwygGlue._state.pendingPaste,bridge:typeof "
        "window.__wysiwygGlue._state.bridge.pastePreviewRequested,ready:"
        "window.__wysiwygGlue._state.ready,composing:window.__wysiwygGlue._state.composing,"
        "active:document.activeElement.outerHTML.slice(0,120),mode:"
        "window.__wysiwygGlue._state.vditor.vditor.currentMode})",
    )
    assert _current_value(office) == "before selected after"
    text, token, generation = requests[0]
    table = parse_clipboard_table(text)
    office.resolve_paste_preview(token, generation, "table", table.markdown(), resolved.append)
    assert _wait_until(lambda: bool(resolved)) and resolved == [True]
    assert _wait_until(lambda: "apple" in _current_value(office))
    value = _current_value(office)
    assert "before" in value and "after" in value and "selected" not in value
    assert _eval(office, "document.querySelectorAll('.vditor-wysiwyg table').length") == 1
    _wait(400)
    command = []
    office.run_command("undo", command.append)
    assert _wait_until(lambda: bool(command)) and command == [True]
    assert _wait_until(lambda: _current_value(office) == "before selected after")


def test_office_cancel_and_plain_choice_keep_the_original_range(office):
    requests, resolved = [], []
    office.paste_preview_requested.connect(lambda *args: requests.append(args))
    _select_word(office)
    _paste(office, "a\tb\nc\td")
    assert _wait_until(lambda: len(requests) == 1)
    _, token, generation = requests[-1]
    assert _eval(office, "window.__wysiwygGlue._state.pendingPaste.range.toString()") == "selected"
    office.resolve_paste_preview(token, generation, "cancel", callback=resolved.append)
    assert _wait_until(lambda: bool(resolved)) and resolved[-1]
    assert _current_value(office) == "before selected after"
    assert _wait_until(
        lambda: _eval(office, "window.getSelection().toString()") == "selected",
    ), _eval(
        office, "JSON.stringify({selection:window.getSelection().toString(),range:"
        "window.__wysiwygGlue._state.vditor.vditor.wysiwyg.range.toString()})",
    )
    _paste(office, "a\tb\nc\td")
    assert _wait_until(lambda: len(requests) == 2)
    _, token, generation = requests[-1]
    office.resolve_paste_preview(token, generation, "plain", callback=resolved.append)
    assert _wait_until(lambda: len(resolved) == 2) and resolved[-1]
    assert _wait_until(lambda: "selected" not in _current_value(office))
    value = _current_value(office)
    assert all(part in value for part in ("before", "after", "a", "b", "c", "d"))
    assert _eval(office, "document.querySelectorAll('.vditor-wysiwyg table').length") == 0
    office.run_command("undo")
    assert _wait_until(lambda: _current_value(office) == "before selected after")


def test_office_reloaded_document_rejects_old_preview_response(office):
    requests, resolved = [], []
    office.paste_preview_requested.connect(lambda *args: requests.append(args))
    _select_word(office)
    _paste(office, "a\tb\nc\td")
    assert _wait_until(lambda: bool(requests))
    _, token, generation = requests[0]
    office.load_markdown("new document")
    assert _wait_until(lambda: _current_value(office) == "new document")
    office.resolve_paste_preview(token, generation, "table", "unwanted", resolved.append)
    assert _wait_until(lambda: bool(resolved)) and resolved == [False]
    assert _current_value(office) == "new document"


def test_office_composition_and_html_clipboard_keep_native_paste_path(office):
    requests = []
    office.paste_preview_requested.connect(lambda *args: requests.append(args))
    _select_word(office)
    assert _eval(office, r"""(function () {
      var root=window.__wysiwygGlue._state.vditor.vditor.wysiwyg.element;
      root.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'候選'}));
      var data=new DataTransfer(); data.setData('text/plain','a\tb\nc\td');
      var event=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});
      root.dispatchEvent(event);
      root.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:''}));
      return window.__wysiwygGlue._state.pendingPaste===null;
    })()""")
    _wait(100)
    assert not requests
    office.load_markdown("before selected after")
    assert _wait_until(lambda: _current_value(office) == "before selected after")
    _select_word(office)
    _eval(office, r"""(function () {
      var root=window.__wysiwygGlue._state.vditor.vditor.wysiwyg.element;
      var data=new DataTransfer(); data.setData('text/plain','a\tb\nc\td');
      data.setData('text/html','<table><tr><td>a</td><td>b</td></tr>'+
        '<tr><td>c</td><td>d</td></tr></table>');
      root.dispatchEvent(new ClipboardEvent('paste',{
        clipboardData:data,bubbles:true,cancelable:true}));
    })()""")
    _wait(200)
    assert not requests
    assert _eval(office, "window.__wysiwygGlue._state.pendingPaste===null")


def test_office_file_clipboard_window_route_cancel_insert_and_undo(
    office, tmp_path, monkeypatch,
):
    from app import edit_backend, paste_preview
    from app.window import MainWindow

    file = tmp_path / "document.pdf"
    file.write_bytes(b"attachment")
    note = tmp_path / "notes" / "note.md"
    note.parent.mkdir()
    note.write_text("before selected after", encoding="utf8")
    requests, recent, messages = [], [], []
    accepted = [False]
    owner = SimpleNamespace(
        _wysiwyg_view=office, _current_file=note, _active_path=str(note), _edit_mode=True,
        _active_edit_backend=edit_backend.WYSIWYG_BACKEND, _resources_available=lambda: True,
        _record_recent_resource=recent.append,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: messages.append(args)),
    )
    monkeypatch.setattr(paste_preview, "confirm_resource_paste", lambda *_: accepted[0])
    office.resource_paste_requested.connect(lambda *args: requests.append(args))
    office.resource_paste_requested.connect(
        lambda *args: MainWindow._on_wysiwyg_resource_paste(owner, *args),
    )
    _select_word(office)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    QGuiApplication.clipboard().setMimeData(mime)
    office.page().triggerAction(QWebEnginePage.WebAction.Paste)
    assert _wait_until(lambda: bool(requests))
    names, _, _ = requests[0]
    assert json.loads(names) == ["document.pdf"]
    assert _current_value(office) == "before selected after"
    assert _wait_until(lambda: _eval(office, "window.__wysiwygGlue._state.pendingPaste===null"))
    assert not (note.parent / "assets").exists()
    assert _wait_until(
        lambda: _eval(office, "window.getSelection().toString()") == "selected",
    ), _eval(
        office, "JSON.stringify({range:window.__wysiwygGlue._state.vditor.vditor.wysiwyg.range"
        ".toString(),selection:window.getSelection().toString(),active:document.activeElement"
        ".outerHTML.slice(0,180),pending:window.__wysiwygGlue._state.pendingPaste})",
    )
    accepted[0] = True
    office.page().triggerAction(QWebEnginePage.WebAction.Paste)
    assert _wait_until(lambda: bool(recent)), messages
    assert len(requests) == 2
    assert recent == ["[document.pdf](assets/document.pdf)"]
    assert (note.parent / "assets" / "document.pdf").read_bytes() == b"attachment"
    assert "[document.pdf](assets/document.pdf)" in _current_value(office)
    assert "selected" not in _current_value(office)
    _wait(400)
    office.run_command("undo")
    assert _wait_until(lambda: _current_value(office) == "before selected after")
    assert (note.parent / "assets" / "document.pdf").read_bytes() == b"attachment"
