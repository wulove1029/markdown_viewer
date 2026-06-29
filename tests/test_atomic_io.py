"""Tests for crash-safe atomic file writes."""

from app.atomic_io import atomic_write_bytes, atomic_write_text, sha256_hex


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
