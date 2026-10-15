"""Navigation work and delayed metadata must belong to the active document."""
# ruff: noqa: F811 -- imported pytest fixtures are requested by argument name.

import pytest

from app import tag_flow, view_mode
from app.document_metadata import DocumentMetadata, signature
from app.tag_index import TagIndex
from tests.test_editor_workspace_integration import (  # noqa: F401
    _isolated_workspace_dependencies,
    make_workspace_window,
)


@pytest.mark.parametrize("suffix", [".md", ".txt", ".pdf"])
def test_switch_deactivates_previous_preview_and_rejects_old_metadata(
    make_workspace_window, tmp_path, suffix
):
    first = tmp_path / "first.md"
    target = tmp_path / ("second" + suffix)
    first.write_text("# First\n#original", encoding="utf-8")
    target.write_text("# Second", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(first))
    generation = window._metadata_generation
    result = DocumentMetadata(("old",), ("original",), signature(first))
    count = window._renderer.deactivated
    window.open_path(str(target))
    assert window._renderer.deactivated > count
    window._on_document_metadata_ready(generation, first, result)
    assert window._current_file == target
    assert "old" not in window._current_front_tags
    assert "original" not in window._current_body_tags


def test_open_preview_does_not_synchronously_scan_tags(
    make_workspace_window, tmp_path, monkeypatch
):
    path = tmp_path / "large.md"
    path.write_text("# Document\n" + "text " * 60_000, encoding="utf-8")
    monkeypatch.setattr(tag_flow, "read_text", lambda *_: pytest.fail("UI read"))
    monkeypatch.setattr(tag_flow, "body_hashtags", lambda *_: pytest.fail("UI scan"))
    monkeypatch.setattr(tag_flow, "read_metadata", lambda *_a, **_k: pytest.fail("duplicate scan"))
    window = make_workspace_window()
    window.open_path(str(path))
    assert window._renderer.loaded_paths == [path]
    assert window._front_tags_pending


def test_source_workspace_return_does_not_schedule_hidden_disk_preview(
    make_workspace_window, tmp_path
):
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_text("# First", encoding="utf-8")
    second.write_text("# Second", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(first))
    assert window._enter_edit_mode(view_mode.EDIT)
    document = window._editor.document()
    window._editor.insertPlainText("unsaved ")
    window.open_path(str(second))
    before = list(window._renderer.loaded_paths)
    window.open_path(str(first))
    assert window._renderer.loaded_paths == before
    assert window._editor.document() is document
    assert document.isModified()
    assert "unsaved" in document.toPlainText()


def test_leaving_source_editor_reloads_visible_preview(make_workspace_window, tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(path))
    assert window._enter_edit_mode(view_mode.EDIT)
    count = len(window._renderer.loaded_paths)
    window._leave_edit_ui()
    assert window._renderer.loaded_paths[count:] == [path]
    assert window._view_mode == view_mode.PREVIEW


def test_leaving_source_editor_reloads_visible_preview(make_workspace_window, tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(path))
    assert window._enter_edit_mode(view_mode.EDIT)
    count = len(window._renderer.loaded_paths)
    window._leave_edit_ui()
    assert window._renderer.loaded_paths[count:] == [path]
    assert window._view_mode == view_mode.PREVIEW


def test_pending_metadata_does_not_erase_index_on_annotation_save(
    make_workspace_window, tmp_path
):
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")
    window = make_workspace_window()
    window._tag_index = TagIndex(tmp_path / "index.json")
    window.open_path(str(path))
    window._tag_index.update(path, window._doc_annotations,
                             front_tags=["existing-front"], body_tags=["existing-body"])
    assert window._front_tags_pending
    window._doc_annotations.doc_tags = ["new-document-tag"]
    window._persist_annotations()
    assert window._tag_index.tags_for(path) == {
        "existing-front", "existing-body", "new-document-tag",
    }


def test_metadata_changed_after_worker_completion_is_not_applied(
    make_workspace_window, tmp_path
):
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(path))
    result = DocumentMetadata(("old",), (), signature(path))
    path.write_text("# Changed content", encoding="utf-8")
    window._on_document_metadata_ready(window._metadata_generation, path, result)
    assert window._current_front_tags == []
    assert window._front_tags_pending


def test_metadata_deleted_after_worker_completion_is_not_applied(
    make_workspace_window, tmp_path
):
    path = tmp_path / "note.md"
    path.write_text("# Note", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(path))
    result = DocumentMetadata(("old",), (), signature(path))
    path.unlink()
    window._on_document_metadata_ready(window._metadata_generation, path, result)
    assert window._current_front_tags == []
