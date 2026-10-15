"""Independent acceptance probes; run with pytest from repository root."""
import time
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from tests.conftest import qapp
from tests.test_editor_workspace_integration import _isolated_workspace_dependencies, make_workspace_window
from app import view_mode


def wait_for(predicate):
    deadline = time.monotonic() + 15
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def test_large_text_pending_cannot_save_previous_dirty_buffer(make_workspace_window, tmp_path, monkeypatch):
    first, large = tmp_path / 'first.txt', tmp_path / 'large.txt'
    first.write_text('original', encoding='utf-8')
    body = 'line body\n' * 40000
    large.write_text(body, encoding='utf-8')
    window = make_workspace_window()
    window.open_path(str(first))
    old_doc = window._editor.document()
    window._editor.insertPlainText('unsaved ')
    starts = []
    monkeypatch.setattr(window._text_loader, 'start', lambda *args: starts.append(args))
    window.open_path(str(large))
    assert starts
    assert not window._edit_mode
    assert window._save_edits() is False
    assert first.read_text(encoding='utf-8') == 'original'
    assert large.read_text(encoding='utf-8') == body
    window.open_path(str(first))
    assert window._editor.document() is old_doc
    assert old_doc.isModified()
    assert 'unsaved ' in old_doc.toPlainText()


def test_large_text_real_loading_switch_roundtrip_preserves_document(make_workspace_window, tmp_path):
    large, other = tmp_path / 'large.txt', tmp_path / 'other.txt'
    body = 'Unicode line 文字\n' * 20000
    large.write_text(body, encoding='utf-8')
    other.write_text('other', encoding='utf-8')
    window = make_workspace_window()
    window.open_path(str(large))
    window.open_path(str(other))
    QTest.qWait(25)
    assert window._editor.document().toPlainText() == 'other'
    window.open_path(str(large))
    wait_for(lambda: window._edit_mode)
    assert window._editor.document().toPlainText() == body
    assert not window._editor.document().isModified()
    assert QThreadPool.globalInstance().waitForDone(10000)
