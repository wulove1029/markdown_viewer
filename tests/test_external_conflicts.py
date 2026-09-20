from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QPlainTextEdit

from app import external_conflicts
from tests import test_editor_data_safety as safety
from tests.test_editor_workspace_integration import _enter_markdown_editor

_isolated_editor_dependencies = safety._isolated_editor_dependencies
make_data_safety_window = safety.make_data_safety_window
_replace_with_dirty_text = safety._replace_with_dirty_text


def test_keep_both_retains_disk_and_local_copy_then_loads_disk(
    make_data_safety_window, tmp_path, monkeypatch,
):
    note = tmp_path / "note.md"
    note.write_bytes(b"old\r\n")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._fs_watcher.blockSignals(True)
    _replace_with_dirty_text(window, "local draft\n")
    note.write_bytes(b"external change\r\n")
    existing = tmp_path / "note (本機副本).md"
    existing.write_bytes(b"older copy")
    monkeypatch.setattr(external_conflicts, "choose_action", lambda *args: "both")
    window._prompt_external_change()
    assert existing.read_bytes() == b"older copy"
    assert (tmp_path / "note (本機副本) 2.md").read_bytes() == b"local draft\r\n"
    assert note.read_bytes() == b"external change\r\n"
    assert window._editor.toPlainText() == "external change\n"
    assert not window._editor.is_modified()


def test_diff_is_read_only_and_does_not_modify_disk_or_buffer(
    make_data_safety_window, tmp_path, monkeypatch,
):
    note = tmp_path / "note.md"
    note.write_bytes(b"original\n")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._fs_watcher.blockSignals(True)
    _replace_with_dirty_text(window, "local\n")
    note.write_bytes(b"external\n")
    choices = iter(["diff", "cancel"])
    monkeypatch.setattr(external_conflicts, "choose_action", lambda *args: next(choices))
    seen = []

    def inspect(dialog):
        edit = dialog.findChild(QPlainTextEdit)
        assert edit.isReadOnly()
        seen.append(edit.toPlainText())
        return 0

    monkeypatch.setattr(QDialog, "exec", inspect)
    window._prompt_external_change()
    assert "-external" in seen[0] and "+local" in seen[0]
    assert note.read_bytes() == b"external\n"
    assert window._editor.toPlainText() == "local\n"
    assert window._editor.is_modified()
    assert not list(tmp_path.glob("*本機副本*"))


def test_copy_write_failure_preserves_external_and_local_buffer(
    make_data_safety_window, tmp_path, monkeypatch,
):
    note = tmp_path / "note.md"
    note.write_bytes(b"original")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._fs_watcher.blockSignals(True)
    _replace_with_dirty_text(window, "local")
    note.write_bytes(b"external")
    monkeypatch.setattr(external_conflicts, "choose_action", lambda *args: "both")
    monkeypatch.setattr(external_conflicts.os, "fsync",
                        lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    window._prompt_external_change()
    assert note.read_bytes() == b"external"
    assert window._editor.toPlainText() == "local"
    assert window._editor.is_modified()
    assert len(list(tmp_path.glob("*本機副本*"))) == 1


def test_exclusive_create_retries_when_another_process_takes_copy_name(tmp_path, monkeypatch):
    note = tmp_path / "note.md"
    original_open = Path.open
    collided = []

    def raced_open(path, mode="r", *args, **kwargs):
        if mode == "xb" and not collided:
            collided.append(path)
            path.write_bytes(b"other process")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", raced_open)
    copied = external_conflicts.write_local_copy(note, "local")
    assert copied != collided[0]
    assert copied.read_bytes() == b"local"
    assert collided[0].read_bytes() == b"other process"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp950"])
def test_copy_preserves_supported_encoding(tmp_path, encoding):
    copied = external_conflicts.write_local_copy(tmp_path / "note.md", "中文\n", encoding, "\r\n")
    assert copied.read_bytes() == "中文\r\n".encode(encoding)
