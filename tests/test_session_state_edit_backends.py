"""Focused tests for per-document editor preferences in app session state."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from app import edit_backend, session_state


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    settings_path = tmp_path / "session-state.ini"

    def factory(*_args, **_kwargs):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(session_state, "QSettings", factory)
    return factory


def _stored_map(settings_factory) -> dict:
    raw = settings_factory().value(session_state.DOCUMENT_EDIT_BACKENDS_KEY)
    return json.loads(raw) if raw else {}


def test_load_missing_and_remember_valid_markdown_backends(isolated_settings, tmp_path):
    source_note = tmp_path / "source.md"
    office_note = tmp_path / "office.markdown"

    assert session_state.DOCUMENT_EDIT_BACKENDS_KEY == "document_edit_backends_v1"
    assert session_state.load_document_edit_backend(source_note) is None

    session_state.remember_document_edit_backend(
        source_note, edit_backend.SPLIT_BACKEND
    )
    session_state.remember_document_edit_backend(
        office_note, edit_backend.WYSIWYG_BACKEND
    )

    assert session_state.load_document_edit_backend(source_note) == "split"
    assert session_state.load_document_edit_backend(office_note) == "wysiwyg"
    assert _stored_map(isolated_settings) == {
        str(Path(source_note)): "split",
        str(Path(office_note)): "wysiwyg",
    }


@pytest.mark.parametrize("bad_backend", [None, "", "office", "WYSIWYG", 1])
def test_remember_rejects_unknown_backends(
    isolated_settings, tmp_path, bad_backend
):
    note = tmp_path / "note.md"

    session_state.remember_document_edit_backend(note, bad_backend)

    assert session_state.load_document_edit_backend(note) is None
    assert _stored_map(isolated_settings) == {}


@pytest.mark.parametrize("suffix", [".txt", ".pdf", ".html", ""])
def test_helpers_ignore_non_markdown_paths(isolated_settings, tmp_path, suffix):
    path = tmp_path / f"note{suffix}"

    session_state.remember_document_edit_backend(
        path, edit_backend.WYSIWYG_BACKEND
    )

    assert session_state.load_document_edit_backend(path) is None
    assert _stored_map(isolated_settings) == {}


@pytest.mark.parametrize("raw", ["{broken", "[]", "null", '"text"'])
def test_bad_or_non_dict_json_is_tolerated_and_replaced_by_valid_remember(
    isolated_settings, tmp_path, raw
):
    settings = isolated_settings()
    settings.setValue(session_state.DOCUMENT_EDIT_BACKENDS_KEY, raw)
    note = tmp_path / "recovered.md"

    assert session_state.load_document_edit_backend(note) is None
    session_state.remember_document_edit_backend(
        note, edit_backend.WYSIWYG_BACKEND
    )

    assert _stored_map(isolated_settings) == {str(note): "wysiwyg"}


def test_read_filters_unknown_values_and_non_markdown_keys(
    isolated_settings, tmp_path
):
    valid = tmp_path / "valid.md"
    unknown = tmp_path / "unknown.md"
    text = tmp_path / "plain.txt"
    isolated_settings().setValue(
        session_state.DOCUMENT_EDIT_BACKENDS_KEY,
        json.dumps(
            {
                str(valid): "split",
                str(unknown): "visual",
                str(text): "wysiwyg",
            }
        ),
    )

    assert session_state.load_document_edit_backend(valid) == "split"
    assert session_state.load_document_edit_backend(unknown) is None
    assert session_state.load_document_edit_backend(text) is None


def test_remember_pop_reinserts_as_newest_entry(isolated_settings, tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    session_state.remember_document_edit_backend(first, "split")
    session_state.remember_document_edit_backend(second, "wysiwyg")

    session_state.remember_document_edit_backend(first, "wysiwyg")

    stored = _stored_map(isolated_settings)
    assert list(stored) == [str(second), str(first)]
    assert stored[str(first)] == "wysiwyg"


def test_remember_keeps_only_300_newest_entries(isolated_settings, tmp_path):
    paths = [tmp_path / f"note-{index:03}.md" for index in range(301)]
    isolated_settings().setValue(
        session_state.DOCUMENT_EDIT_BACKENDS_KEY,
        json.dumps({str(path): "split" for path in paths[:-1]}),
    )

    session_state.remember_document_edit_backend(paths[-1], "split")

    stored = _stored_map(isolated_settings)
    assert len(stored) == 300
    assert str(paths[0]) not in stored
    assert list(stored)[0] == str(paths[1])
    assert list(stored)[-1] == str(paths[-1])


def test_migrate_moves_and_refreshes_entries_and_drops_non_markdown_target(
    isolated_settings, tmp_path
):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    destination = tmp_path / "renamed.md"
    session_state.remember_document_edit_backend(first, "wysiwyg")
    session_state.remember_document_edit_backend(destination, "split")
    session_state.remember_document_edit_backend(second, "split")

    session_state.migrate_document_edit_backends(
        {first: destination, second: tmp_path / "second.txt"}
    )

    stored = _stored_map(isolated_settings)
    assert stored == {str(destination): "wysiwyg"}
    assert session_state.load_document_edit_backend(first) is None
    assert session_state.load_document_edit_backend(destination) == "wysiwyg"


def test_forget_accepts_one_path_or_an_iterable(isolated_settings, tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    third = tmp_path / "third.md"
    for path in (first, second, third):
        session_state.remember_document_edit_backend(path, "split")

    session_state.forget_document_edit_backends(first)
    session_state.forget_document_edit_backends([second, tmp_path / "missing.md"])

    assert _stored_map(isolated_settings) == {str(third): "split"}


def test_preferences_never_modify_markdown_or_create_sidecars(
    isolated_settings, tmp_path
):
    notes = tmp_path / "notes"
    notes.mkdir()
    old_path = notes / "original.md"
    new_path = notes / "renamed.md"
    original_bytes = b"\xef\xbb\xbf# title\r\n\r\n"
    old_path.write_bytes(original_bytes)

    session_state.remember_document_edit_backend(old_path, "wysiwyg")
    session_state.migrate_document_edit_backends({old_path: new_path})
    session_state.forget_document_edit_backends(new_path)

    assert old_path.read_bytes() == original_bytes
    assert list(notes.iterdir()) == [old_path]
