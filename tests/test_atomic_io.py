"""Tests for crash-safe atomic file writes."""

import os

import pytest

from app.atomic_io import (
    atomic_write_bytes,
    atomic_write_text,
    set_hidden,
    sha256_hex,
)

# The hidden attribute only exists on Windows; everywhere else set_hidden is a
# no-op, so the attribute assertions can only run on ``nt``.
_FILE_ATTRIBUTE_HIDDEN = 0x2
_windows_only = pytest.mark.skipif(
    os.name != "nt", reason="hidden attribute is Windows-only"
)


def _is_hidden(path) -> bool:
    return bool(os.stat(path).st_file_attributes & _FILE_ATTRIBUTE_HIDDEN)


def test_write_creates_file(tmp_path):
    target = tmp_path / "note.md"
    atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_second_write_keeps_single_bak(tmp_path):
    target = tmp_path / "note.md"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    assert target.read_text(encoding="utf-8") == "v2"
    bak = tmp_path / "note.md.bak"
    assert bak.read_text(encoding="utf-8") == "v1"


def test_no_bak_on_first_write(tmp_path):
    target = tmp_path / "note.md"
    atomic_write_bytes(target, b"first")
    assert not (tmp_path / "note.md.bak").exists()


def test_no_leftover_tmp(tmp_path):
    target = tmp_path / "note.md"
    atomic_write_bytes(target, b"data")
    assert not (tmp_path / "note.md.tmp").exists()


def test_backup_disabled(tmp_path):
    target = tmp_path / "note.md"
    atomic_write_bytes(target, b"v1")
    atomic_write_bytes(target, b"v2", backup=False)
    assert not (tmp_path / "note.md.bak").exists()


def test_sha256_hex():
    assert sha256_hex(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_set_hidden_never_raises_on_missing(tmp_path):
    # Best-effort: a nonexistent path must be swallowed, not raise.
    set_hidden(tmp_path / "ghost.notes.json")


@_windows_only
def test_set_hidden_marks_existing_file(tmp_path):
    target = tmp_path / "s.notes.json"
    target.write_text("{}", encoding="utf-8")
    assert not _is_hidden(target)
    set_hidden(target)
    assert _is_hidden(target)


@_windows_only
def test_atomic_write_hidden_marks_file(tmp_path):
    target = tmp_path / "s.notes.json"
    atomic_write_text(target, "{}", backup=False, hidden=True)
    assert _is_hidden(target)


@_windows_only
def test_atomic_write_can_replace_already_hidden_target(tmp_path):
    # Rewriting an already-hidden sidecar must not trip Windows' hidden-replace
    # access error, and the result stays hidden.
    target = tmp_path / "s.notes.json"
    atomic_write_text(target, "v1", backup=False, hidden=True)
    atomic_write_text(target, "v2", backup=False, hidden=True)
    assert target.read_text(encoding="utf-8") == "v2"
    assert _is_hidden(target)


@_windows_only
def test_atomic_write_hides_backup_too(tmp_path):
    target = tmp_path / "s.notes.json"
    atomic_write_text(target, "v1", hidden=True)  # backup defaults to True
    atomic_write_text(target, "v2", hidden=True)
    bak = tmp_path / "s.notes.json.bak"
    assert bak.read_text(encoding="utf-8") == "v1"
    assert _is_hidden(bak)
