"""Startup recovery works independently of session membership and source files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QTabBar

from app import recovery_browser, session_state
from app.recovery import RecoveryStore
from app.recovery_browser import pending_recovery_snapshots, save_recovery_copy


class StartupWindow(QMainWindow):
    """Exercise real routing/dialogs without loading WebEngine or user settings."""

    def __init__(self, store):
        super().__init__()
        self._recovery_store = store
        self._tab_state = {}
        self._tab_bar = QTabBar(self)
        self.opened = []
        self.reviewed = []
        self.activated = []

    def _add_tab(self, path, kind):
        index = self._tab_bar.addTab(path.name)
        self._tab_bar.setTabData(index, str(path))
        self._tab_state[str(path)] = {"kind": kind, "editor_document": None}
        return index

    def _activate_tab(self, index):
        self.activated.append(self._tab_bar.tabData(index))

    def _open_file(self, path):
        self.opened.append(str(path))

    open_path = _open_file

    def _review_recovery_path(self, path):
        self.reviewed.append(path)


@pytest.fixture
def setup_startup(qapp, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(session_state, "QSettings", lambda *_: settings)
    monkeypatch.setattr(session_state, "restore_file_tree_state", lambda _window: None)
    store = RecoveryStore(tmp_path / "recovery")
    window = StartupWindow(store)
    yield window, store, settings
    window.close()
    window.deleteLater()
    qapp.processEvents()


def save_draft(store, path, text="unsaved draft", **kwargs):
    return store.save(
        path, text, encoding=kwargs.get("encoding", "utf-8"),
        newline=kwargs.get("newline", "\n"), cursor=0, anchor=0, scroll=0,
    )


def test_startup_discovers_orphan_draft_without_writing_source(setup_startup, tmp_path):
    window, store, settings = setup_startup
    source = tmp_path / "orphan.md"
    source.write_bytes(b"disk version")
    save_draft(store, source)
    settings.setValue("open_tabs", "[]")
    original = source.read_bytes()
    stamp = source.stat().st_mtime_ns

    session_state.restore_startup(window)

    browser = window._recovery_browser
    assert browser.isVisible()
    assert not browser.isModal()
    assert browser.windowModality() == Qt.WindowModality.NonModal
    assert browser._list.count() == 1
    assert browser._preview.toPlainText() == "unsaved draft"
    assert window.opened == []
    assert source.read_bytes() == original
    assert source.stat().st_mtime_ns == stamp
    assert store.load(source).draft == "unsaved draft"


def test_cli_file_stays_primary_and_orphan_drafts_are_discovered(setup_startup, tmp_path):
    window, store, settings = setup_startup
    cli = tmp_path / "cli.md"
    cli.write_text("requested file", encoding="utf-8")
    old = tmp_path / "old.md"
    old.write_text("old session", encoding="utf-8")
    orphan = tmp_path / "orphan.txt"
    save_draft(store, orphan)
    settings.setValue("open_tabs", json.dumps([str(old)]))

    session_state.restore_startup(window, str(cli))

    assert window.opened == [str(cli)]
    assert window._tab_bar.count() == 0
    assert window._recovery_browser.selected_snapshot().source_path == str(orphan)
    assert cli.read_text(encoding="utf-8") == "requested file"
    assert not orphan.exists()


def test_session_drafts_and_duplicate_json_appear_once(setup_startup, tmp_path):
    window, store, settings = setup_startup
    source = tmp_path / "existing.md"
    source.write_text("disk", encoding="utf-8")
    snapshot = save_draft(store, source)
    (store.directory / "duplicate.json").write_text(
        json.dumps(snapshot.to_dict()), encoding="utf-8"
    )
    settings.setValue("open_tabs", json.dumps([str(source)]))

    session_state.restore_last_session(window)

    assert window._tab_bar.count() == 1
    assert window.activated == [str(source)]
    assert window._recovery_browser._list.count() == 1


def test_missing_empty_source_is_recoverable_and_corrupt_snapshots_are_skipped(
    setup_startup, tmp_path,
):
    window, store, _settings = setup_startup
    missing = tmp_path / "deleted.txt"
    save_draft(store, missing, "")
    (store.directory / "broken.json").write_bytes(b"\xffnot-json")
    bad = tmp_path / "corrupt.md"
    save_draft(store, bad)
    store.snapshot_path(bad).write_text("{", encoding="utf-8")
    invalid = store.snapshot_path(tmp_path / "invalid.md")
    payload = store.load(missing).to_dict()
    payload["source_path"] = "bad\u0000.md"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    session_state.restore_startup(window)

    assert window._recovery_browser._list.count() == 1
    assert "原檔不存在" in window._recovery_browser._list.item(0).text()
    assert store.load(missing).draft == ""
    assert not missing.exists()
    assert invalid.exists()  # corrupt entries are not silently deleted


def test_later_retains_snapshot_and_reopening_reuses_nonmodal_inbox(setup_startup, tmp_path):
    window, store, _settings = setup_startup
    source = tmp_path / "later.md"
    save_draft(store, source)
    session_state.restore_startup(window)
    browser = window._recovery_browser

    QTest.mouseClick(browser._later, Qt.MouseButton.LeftButton)

    assert not browser.isVisible()
    assert store.load(source).draft == "unsaved draft"
    reopened = session_state.show_pending_recovery(window)
    assert reopened is browser
    assert browser.isVisible()
    assert browser._list.count() == 1


def test_review_selection_uses_existing_window_recovery_gate(setup_startup, tmp_path):
    window, store, _settings = setup_startup
    source = tmp_path / "review.md"
    save_draft(store, source)
    browser = session_state.show_pending_recovery(window)

    browser._list.setFocus()
    QTest.keyClick(browser._list, Qt.Key.Key_Return)

    assert window.reviewed == [str(source)]
    assert store.load(source) is not None  # review can choose Later


def test_live_buffer_and_identical_disk_are_not_reoffered(setup_startup, tmp_path):
    window, store, _settings = setup_startup
    live = tmp_path / "live.md"
    same = tmp_path / "same.txt"
    save_draft(store, live, "older draft")
    same.write_text("already saved", encoding="utf-8")
    save_draft(store, same, "already saved")
    window._tab_state[str(live)] = {"editor_document": QTextDocument("newer live draft")}

    assert pending_recovery_snapshots(window) == []
    assert session_state.show_pending_recovery(window, notify_empty=True) is None
    assert "沒有待復原" in window.statusBar().currentMessage()
    assert store.load(live).draft == "older draft"
    assert store.load(same).draft == "already saved"


@pytest.mark.parametrize("timestamp", ["9999-12-31T23:59:59Z", "0001-01-01T00:00:00+14:00"])
def test_valid_extreme_timestamp_cannot_block_recovery_inbox(setup_startup, tmp_path, timestamp):
    window, store, _settings = setup_startup
    source = tmp_path / "extreme.md"
    store.save(
        source, "safe draft", encoding="utf-8", newline="\n",
        cursor=0, anchor=0, scroll=0, updated_at=timestamp,
    )

    browser = session_state.show_pending_recovery(window)

    assert browser._list.count() == 1
    assert browser._preview.toPlainText() == "safe draft"
    assert store.load(source).updated_at == timestamp


def test_copy_preserves_encoding_newlines_source_and_snapshot(setup_startup, tmp_path, monkeypatch):
    window, store, _settings = setup_startup
    source = tmp_path / "source.txt"
    source.write_bytes(b"source stays")
    snapshot = save_draft(store, source, "draft\nline", encoding="utf-16", newline="\r\n")
    target = tmp_path / "copy.txt"
    monkeypatch.setattr(
        recovery_browser.QFileDialog, "getSaveFileName", lambda *_args: (str(target), "")
    )
    browser = session_state.show_pending_recovery(window)

    QTest.mouseClick(browser._copy, Qt.MouseButton.LeftButton)

    assert target.read_bytes() == "draft\r\nline".encode("utf-16")
    assert source.read_bytes() == b"source stays"
    assert store.load(source) == snapshot
    assert "原草稿仍保留" in browser._message.text()


def test_copy_refuses_source_and_hardlink_alias_without_modifying_files(setup_startup, tmp_path):
    _window, store, _settings = setup_startup
    source = tmp_path / "source.md"
    source.write_bytes(b"original")
    snapshot = save_draft(store, source)
    alias = tmp_path / "alias.md"
    os.link(source, alias)

    for target in (source, alias):
        with pytest.raises(ValueError, match="原始檔"):
            save_recovery_copy(snapshot, target)

    assert source.read_bytes() == b"original"
    assert alias.read_bytes() == b"original"
    assert store.load(source) == snapshot


@pytest.mark.parametrize("suffix", [".tmp", ".bak"])
def test_copy_refuses_atomic_write_sibling_aliases_of_source(setup_startup, tmp_path, suffix):
    _window, store, _settings = setup_startup
    source = tmp_path / "source.md"
    source.write_bytes(b"source must stay")
    snapshot = save_draft(store, source)
    target = tmp_path / "copy.md"
    target.write_bytes(b"previous target")
    alias = target.with_name(target.name + suffix)
    os.link(source, alias)

    with pytest.raises(ValueError, match="原始檔"):
        save_recovery_copy(snapshot, target)

    assert source.read_bytes() == b"source must stay"
    assert target.read_bytes() == b"previous target"
    assert store.load(source) == snapshot


def test_markdown_copy_keeps_relative_attachment_target(setup_startup, tmp_path):
    _window, store, _settings = setup_startup
    source = tmp_path / "source.md"
    source.write_bytes(b"original")
    asset = tmp_path / "assets" / "attachment.txt"
    asset.parent.mkdir()
    asset.write_bytes(b"attachment")
    snapshot = save_draft(store, source, "[file](assets/attachment.txt)")
    destination = tmp_path / "archive" / "copy.md"
    destination.parent.mkdir()

    save_recovery_copy(snapshot, destination)

    assert destination.read_text(encoding="utf-8") == "[file](../assets/attachment.txt)"
    assert source.read_bytes() == b"original"
    assert store.load(source) == snapshot


def test_save_copy_cancel_and_failure_keep_recovery(setup_startup, tmp_path, monkeypatch):
    window, store, _settings = setup_startup
    source = tmp_path / "missing.md"
    snapshot = save_draft(store, source)
    browser = session_state.show_pending_recovery(window)
    warnings = []
    monkeypatch.setattr(recovery_browser.QMessageBox, "warning", lambda *_args: warnings.append(_args))
    monkeypatch.setattr(recovery_browser.QFileDialog, "getSaveFileName", lambda *_args: ("", ""))
    browser._save_copy()
    assert not warnings
    monkeypatch.setattr(recovery_browser.QFileDialog, "getSaveFileName", lambda *_args: (str(source), ""))
    browser._save_copy()
    assert len(warnings) == 1
    assert store.load(source) == snapshot
    assert not source.exists()


@pytest.mark.parametrize("bad_session", ['{}', '"not a list"', '[null, 8, false]', '["bad\\u0000.md"]'])
def test_malformed_session_cannot_hide_valid_recovery(setup_startup, tmp_path, bad_session):
    window, store, settings = setup_startup
    source = tmp_path / "orphan.md"
    save_draft(store, source)
    settings.setValue("open_tabs", bad_session)

    session_state.restore_startup(window)

    assert window._recovery_browser.selected_snapshot().source_path == str(source)
