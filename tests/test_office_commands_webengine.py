"""Real Chromium acceptance for host-routed Office commands and IME guards."""

import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QTextCursor

from app import wysiwyg_view as wysiwyg_view_mod
from app.wysiwyg_view import WysiwygView
from tests.test_wysiwyg_webengine import _current_value, _dispose, _eval, _wait_until

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="Set RUN_WEBENGINE_TESTS=1 to execute real Chromium",
)


@pytest.fixture
def office(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        wysiwyg_view_mod,
        "QSettings",
        lambda *_args: QSettings(str(tmp_path / "office.ini"), QSettings.Format.IniFormat),
    )
    view = WysiwygView()
    view.resize(1100, 700)
    view.show()
    assert _wait_until(lambda: view._ready)
    view.set_document_path(tmp_path / "note.md")
    view.load_markdown("Alpha beta")
    assert _wait_until(lambda: _current_value(view) == "Alpha beta")
    assert _wait_until(
        lambda: (
            _eval(view, "window.__wysiwygGlue._state.vditor.vditor.undo.wysiwyg.undoStack.length")
            >= 1
        )
    )
    yield view
    _dispose(view)


def command(view, name):
    results = []
    view.run_command(name, results.append)
    assert _wait_until(lambda: bool(results))
    return results[0]


def select_alpha(view, *, collapsed=False):
    js = """
    (function(){
      var inner=window.__wysiwygGlue._state.vditor.vditor;
      var surface=inner[inner.currentMode].element;
      var walker=document.createTreeWalker(surface, NodeFilter.SHOW_TEXT);
      var nodes=[], text='';
      while(walker.nextNode()) {
        var node=walker.currentNode;
        if(node.parentElement.closest('.vditor-editor-boundary')) continue;
        nodes.push({node:node,start:text.length});
        text+=node.textContent;
      }
      var at=text.indexOf('Alpha beta');
      if(at<0) return false;
      function point(offset) {
        for(var i=0;i<nodes.length;i++) {
          var entry=nodes[i];
          if(offset<=entry.start+entry.node.textContent.length) {
            return {node:entry.node,offset:offset-entry.start};
          }
        }
      }
      var start=point(at+(COLLAPSED?10:0));
      var end=point(at+(COLLAPSED?10:5));
      var range=document.createRange();
      range.setStart(start.node,start.offset);
      range.setEnd(end.node,end.offset);
      surface.focus();
      var selection=window.getSelection();
      selection.removeAllRanges();selection.addRange(range);
      inner[inner.currentMode].range=range;
      return true;
    })()
    """.replace("COLLAPSED", "true" if collapsed else "false")
    assert _eval(view, js) is True


def surface_html(view):
    return _eval(
        view,
        "(function(){var v=window.__wysiwygGlue._state.vditor.vditor;"
        "return v[v.currentMode].element.innerHTML;})()",
    )


@pytest.mark.parametrize("name", ["undo", "redo", "unknown-command"])
def test_unavailable_commands_do_not_create_paragraphs_or_claim_success(office, name):
    before = surface_html(office)
    assert command(office, name) is False
    assert surface_html(office) == before
    assert _current_value(office) == "Alpha beta"


def test_bold_undo_redo_use_office_history_without_caret_side_effects(office):
    select_alpha(office)
    assert command(office, "bold") is True
    assert _wait_until(lambda: _current_value(office) == "**Alpha** beta")
    assert _wait_until(
        lambda: (
            _eval(office, "window.__wysiwygGlue._state.vditor.vditor.undo.wysiwyg.undoStack.length")
            >= 2
        )
    )
    _eval(
        office,
        "window.getSelection().removeAllRanges();"
        "window.__wysiwygGlue._state.vditor.vditor.wysiwyg.range=null;",
    )
    assert command(office, "undo") is True
    assert _wait_until(lambda: _current_value(office) == "Alpha beta")
    assert command(office, "redo") is True
    assert _wait_until(lambda: _current_value(office) == "**Alpha** beta")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("italic", "*Alpha* beta"),
        ("strikethrough", "~~Alpha~~ beta"),
        ("bullet_list", "* Alpha beta"),
        ("ordered_list", "1. Alpha beta"),
        ("task_list", "* [ ] Alpha beta"),
        ("quote", "> Alpha beta"),
    ],
)
def test_host_format_command_mapping_mutates_selected_office_content(office, name, expected):
    select_alpha(office)
    assert command(office, name) is True
    assert _wait_until(lambda: _current_value(office) == expected), _current_value(office)


def test_host_table_command_inserts_real_editable_table(office):
    select_alpha(office, collapsed=True)
    assert command(office, "table") is True
    assert _wait_until(lambda: "col1" in (_current_value(office) or ""))
    assert _eval(office, "document.querySelectorAll('.vditor-wysiwyg table th').length") == 3
    assert "Alpha beta" in _current_value(office)


def test_insert_value_retains_markdown_and_can_be_undone(office):
    select_alpha(office, collapsed=True)
    office.insert_value("\n\n新增中文段落 **bold**")
    assert _wait_until(lambda: "新增中文段落 **bold**" in (_current_value(office) or ""))
    assert _wait_until(
        lambda: (
            _eval(office, "window.__wysiwygGlue._state.vditor.vditor.undo.wysiwyg.undoStack.length")
            >= 2
        )
    )
    assert command(office, "undo") is True
    assert _wait_until(lambda: _current_value(office) == "Alpha beta")


def test_synthetic_ime_composition_blocks_host_mutations_until_commit(office):
    select_alpha(office)
    before = _current_value(office)
    _eval(
        office,
        "document.dispatchEvent(new CompositionEvent('compositionstart',"
        "{data:'注音',bubbles:true}));",
    )
    assert command(office, "bold") is False
    assert command(office, "undo") is False
    assert _eval(office, "window.__wysiwygGlue.insertValue('must not insert')") is False
    for key, ctrl in (("Enter", False), ("Escape", False), ("s", True)):
        script = (
            "(function(){var event=new KeyboardEvent('keydown',"
            f"{{key:'{key}',ctrlKey:{str(ctrl).lower()},isComposing:true,"
            "bubbles:true,cancelable:true});document.dispatchEvent(event);"
            "return event.defaultPrevented;})()"
        )
        assert _eval(office, script) is False
    assert _current_value(office) == before
    _eval(
        office,
        "document.dispatchEvent(new CompositionEvent('compositionend',"
        "{data:'中文',bubbles:true}));",
    )
    assert command(office, "bold") is True
    assert _wait_until(lambda: _current_value(office) == "**Alpha** beta")


def test_host_code_and_link_commands_use_native_office_widgets(office):
    select_alpha(office)
    assert command(office, "link") is True
    assert _eval(office, "document.querySelector('.vditor-wysiwyg a').textContent") == "Alpha"
    office.load_markdown("Alpha beta")
    assert _wait_until(lambda: _current_value(office) == "Alpha beta")
    select_alpha(office)
    assert command(office, "code_block") is True
    assert _wait_until(lambda: "```" in (_current_value(office) or ""))
    assert "Alpha" in _current_value(office)
    assert "beta" in _current_value(office)


@pytest.mark.parametrize("backend", ["split", "wysiwyg"])
def test_host_routes_bold_undo_redo_to_only_the_visible_backend(office, backend):
    from app.editor import EditorView
    from app.window import MainWindow

    source = EditorView()
    source.setPlainText("Alpha beta")
    cursor = source.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    source.setTextCursor(cursor)
    host = SimpleNamespace(
        _edit_mode=True,
        _current_kind="markdown",
        _active_edit_backend=backend,
        _editor=source,
        _wysiwyg_view=office,
    )
    try:
        select_alpha(office)
        MainWindow._apply_format_action(host, "bold")
        if backend == "split":
            assert source.toPlainText() == "**Alpha** beta"
            assert _current_value(office) == "Alpha beta"
            MainWindow._run_native_editor_command(host, "undo")
            assert source.toPlainText() == "Alpha beta"
            MainWindow._run_native_editor_command(host, "redo")
            assert source.toPlainText() == "**Alpha** beta"
        else:
            assert _wait_until(lambda: _current_value(office) == "**Alpha** beta")
            assert source.toPlainText() == "Alpha beta"
            assert _wait_until(
                lambda: _eval(
                    office,
                    "window.__wysiwygGlue._state.vditor.vditor.undo.wysiwyg.undoStack.length",
                )
                >= 2
            )
            MainWindow._run_native_editor_command(host, "undo")
            assert _wait_until(lambda: _current_value(office) == "Alpha beta")
            MainWindow._run_native_editor_command(host, "redo")
            assert _wait_until(lambda: _current_value(office) == "**Alpha** beta")
            assert source.toPlainText() == "Alpha beta"
    finally:
        source.close()
        source.deleteLater()
