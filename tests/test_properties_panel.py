"""Asynchronous property loading never enables stale or partial saves."""

import threading
import time

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from app import properties_panel
from app.frontmatter_properties import PropertiesDocument
from app.properties_panel import PropertiesPanel
from tests import test_editor_data_safety as safety
from tests.test_editor_workspace_integration import _enter_markdown_editor

_isolated_editor_dependencies = safety._isolated_editor_dependencies
make_data_safety_window = safety.make_data_safety_window


def _wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def test_slow_old_request_is_cancelled_without_blocking_or_replacing_new_file(
    qapp, tmp_path, monkeypatch,
):
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_text("---\ntitle: old\n---\nOld body", encoding="utf-8")
    second.write_text("---\ntitle: new\n---\nNew body", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    parse = PropertiesDocument.parse

    def delayed(raw):
        if b"old" in raw:
            entered.set()
            assert release.wait(5)
        return parse(raw)

    monkeypatch.setattr(PropertiesDocument, "parse", delayed)
    panel = PropertiesPanel(lambda *_: True)
    try:
        panel.set_document(first)
        _wait(entered.is_set)
        stale_generation = panel._generation
        assert panel._document is None
        assert not panel._save.isEnabled()
        panel.set_document(second)
        _wait(lambda: panel._document is not None)
        assert panel._document.values == {"title": "new"}
        panel._read_done(stale_generation, first, (parse(first.read_bytes()), []), "")
        release.set()
        assert QThreadPool.globalInstance().waitForDone(5000)
        QTest.qWait(20)
        assert panel._table.item(0, 1).text() == "new"
        assert panel._save.isEnabled()
    finally:
        release.set()
        panel.set_document(None)
        panel.close()
        assert QThreadPool.globalInstance().waitForDone(5000)



def test_read_outliving_its_panel_reports_nothing_and_holds_no_qt_object(
    qapp, tmp_path, monkeypatch,
):
    from PySide6.QtCore import QObject
    from shiboken6 import delete

    path = tmp_path / "late.md"
    path.write_text("---\ntitle: late\n---\nBody", encoding="utf-8")
    entered, release, workers, delivered = threading.Event(), threading.Event(), [], []
    parse = PropertiesDocument.parse

    def delayed(raw):
        entered.set()
        assert release.wait(5)
        return parse(raw)

    class TrackedRead(properties_panel._ReadProperties):
        def __init__(self, *args):
            super().__init__(*args)
            workers.append(vars(self).copy())

    monkeypatch.setattr(PropertiesDocument, "parse", delayed)
    monkeypatch.setattr(properties_panel, "_ReadProperties", TrackedRead)
    monkeypatch.setattr(PropertiesPanel, "_read_done",
                        lambda _self, *args: delivered.append(args))
    panel = PropertiesPanel(lambda *_: True)
    try:
        panel.set_document(path)
        _wait(entered.is_set)
        assert not any(isinstance(value, QObject) for value in workers[0].values())
        relay = panel._read_relay
        delete(panel)
        assert relay.closed
    finally:
        release.set()
        assert QThreadPool.globalInstance().waitForDone(5000)
    QTest.qWait(20)
    assert delivered == []

def test_changed_file_is_rejected_and_cannot_be_saved(qapp, tmp_path, monkeypatch):
    path = tmp_path / "changing.md"
    path.write_text("---\ntitle: old\n---\nbody", encoding="utf-8")
    parse = PropertiesDocument.parse

    def change_during_parse(raw):
        result = parse(raw)
        path.write_text("different bytes", encoding="utf-8")
        return result

    monkeypatch.setattr(PropertiesDocument, "parse", change_during_parse)
    saved = []
    panel = PropertiesPanel(lambda *args: saved.append(args))
    panel.set_document(path)
    _wait(lambda: "已變更" in panel._message.text())
    panel._save_properties()
    assert saved == []
    assert not panel._save.isEnabled()
    assert panel._document is None
    panel.close()
    assert QThreadPool.globalInstance().waitForDone(5000)


def test_loaded_properties_save_keeps_exact_original_and_body(qapp, tmp_path):
    path = tmp_path / "note.md"
    original = b"---\r\ntitle: old # comment\r\n---\r\nbody  \r\n"
    path.write_bytes(original)
    saved = []

    def save(target, expected, updated):
        assert target == path
        assert expected == original
        assert path.read_bytes() == expected
        target.write_bytes(updated)
        saved.append(updated)
        return True

    panel = PropertiesPanel(save)
    panel.set_document(path)
    _wait(lambda: panel._document is not None)
    panel._table.item(0, 1).setText("new")
    panel._save_properties()
    _wait(lambda: panel._document is not None and panel._document.values["title"] == "new")
    assert len(saved) == 1
    assert saved[0].endswith(b"body  \r\n")
    assert b"# comment" in saved[0]
    panel.set_document(None)
    assert not panel._table.isEnabled()
    assert panel._table.rowCount() == 0
    panel.close()
    assert QThreadPool.globalInstance().waitForDone(5000)


def test_property_rows_yield_before_enabling_save(qapp, monkeypatch):
    panel = PropertiesPanel(lambda *_: True)
    document = PropertiesDocument.parse(b"# body")
    panel._pending_document = document
    panel._pending_rows = [("one", "1"), ("two", "2")]
    times = iter((0.0, 0.009))
    monkeypatch.setattr(properties_panel.time, "perf_counter", lambda: next(times))
    panel._append_rows()
    assert panel._table.rowCount() == 1
    assert panel._document is None
    assert not panel._save.isEnabled()
    panel.set_document(None)
    assert panel._pending_document is None
    assert not panel._row_timer.isActive()
    panel.close()


def test_panel_edits_value_and_preserves_body(make_data_safety_window, tmp_path):
    path = tmp_path / "note.md"
    raw = b"---\ntitle: old\n---\n# body\r\nend  \n"
    path.write_bytes(raw)
    window = make_data_safety_window()
    window.open_path(str(path))
    panel = window._properties_panel
    panel.set_document(path)
    _wait(lambda: panel._document is not None)
    panel._table.item(0, 1).setText('"new"')
    panel._save_properties()
    result = PropertiesDocument.parse(path.read_bytes())
    assert result.values["title"] == "new"
    assert result.body_bytes == PropertiesDocument.parse(raw).body_bytes
    assert path.with_name("note.md.bak").read_bytes() == raw


def test_invalid_frontmatter_disables_save(qapp, tmp_path):
    path = tmp_path / "note.md"
    raw = b"---\ninvalid: [\n---\nbody"
    path.write_bytes(raw)
    panel = PropertiesPanel(lambda *args: pytest.fail("must not write invalid YAML"))
    panel.set_document(path)
    _wait(lambda: "無法載入屬性" in panel._message.text())
    assert not panel._save.isEnabled()
    assert path.read_bytes() == raw
    panel.close()


def test_properties_cannot_overwrite_dirty_editor_or_external_change(
    make_data_safety_window, tmp_path,
):
    path = tmp_path / "note.md"
    raw = b"# body"
    path.write_bytes(raw)
    window = make_data_safety_window()
    _enter_markdown_editor(window, path)
    safety._replace_with_dirty_text(window, "draft")
    with pytest.raises(OSError, match="編輯模式"):
        window._save_properties(path, raw, b"replacement")
    assert path.read_bytes() == raw
    assert window._editor.toPlainText() == "draft"


def test_stale_properties_snapshot_refuses_external_overwrite(make_data_safety_window, tmp_path):
    path = tmp_path / "note.md"
    raw = b"# body"
    path.write_bytes(raw)
    window = make_data_safety_window()
    window.open_path(str(path))
    window._fs_watcher.blockSignals(True)
    path.write_bytes(b"external")
    with pytest.raises(OSError, match="磁碟文件已變更"):
        window._save_properties(path, raw, b"replacement")
    assert path.read_bytes() == b"external"
