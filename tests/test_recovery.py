"""Tests for AppData crash-recovery snapshots."""

from __future__ import annotations

import json

import pytest

from app import recovery
from app.recovery import RecoveryStore


def _save(store: RecoveryStore, source, draft="尚未儲存 ✅", **overrides):
    state = {
        "encoding": "utf-8",
        "newline": "\n",
        "cursor": 8,
        "anchor": 3,
        "scroll": 27,
        "updated_at": "2026-08-25T08:00:00.000Z",
    }
    state.update(overrides)
    return store.save(source, draft, **state)


def test_save_round_trips_full_state_without_touching_source(tmp_path):
    source = tmp_path / "筆記.txt"
    source.write_bytes("原始內容\r\n".encode("utf-8"))
    source_before = source.read_bytes()
    signature_before = (source.stat().st_mtime_ns, source.stat().st_size)
    store = RecoveryStore(tmp_path / "app-data" / "recovery")

    saved = _save(
        store,
        source,
        draft="草稿內容 ✅\n第二行",
        encoding="cp950",
        newline="\r\n",
        cursor=14,
        anchor=2,
        scroll=91,
    )

    assert source.read_bytes() == source_before
    assert (source.stat().st_mtime_ns, source.stat().st_size) == signature_before
    assert saved.signature_pair == signature_before
    assert saved.source_signature == f"{signature_before[0]}:{signature_before[1]}"
    assert saved.encoding == "cp950"
    assert saved.newline == "\r\n"
    assert saved.cursor == 14
    assert saved.anchor == 2
    assert saved.scroll == 91
    assert store.load(source) == saved

    snapshot_path = store.snapshot_path(source)
    snapshot_text = snapshot_path.read_text(encoding="utf-8")
    assert "草稿內容 ✅" in snapshot_text
    assert json.loads(snapshot_text)["schema"] == recovery.SCHEMA_VERSION
    assert not snapshot_path.with_name(snapshot_path.name + ".tmp").exists()
    assert not snapshot_path.with_name(snapshot_path.name + ".bak").exists()


def test_save_can_keep_the_revision_loaded_by_the_editor(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("new disk revision", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")

    snapshot = _save(store, source, source_signature=(123456789, 17))

    assert snapshot.source_mtime_ns == 123456789
    assert snapshot.source_size == 17
    assert snapshot.source_signature == "123456789:17"


def test_missing_source_still_has_a_recoverable_snapshot(tmp_path):
    source = tmp_path / "deleted-before-debounce.md"
    store = RecoveryStore(tmp_path / "recovery")

    snapshot = _save(store, source)

    assert snapshot.source_signature is None
    assert snapshot.source_mtime_ns is None
    assert snapshot.source_size is None
    assert store.load(source) == snapshot


def test_same_filename_in_different_folders_uses_distinct_snapshots(tmp_path):
    first = tmp_path / "one" / "README.md"
    second = tmp_path / "two" / "README.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")

    _save(store, first, "draft one")
    _save(store, second, "draft two")

    assert store.snapshot_path(first) != store.snapshot_path(second)
    assert store.load(first).draft == "draft one"
    assert store.load(second).draft == "draft two"
    assert len(list(store.directory.glob("*.json"))) == 2


def test_list_is_newest_first_and_safely_skips_corrupt_snapshots(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")
    _save(store, first, updated_at="2026-08-25T08:00:00.000Z")
    _save(store, second, updated_at="2026-08-25T09:00:00.000Z")
    (store.directory / "broken.json").write_bytes(b"\xffnot-json")
    (store.directory / "old-schema.json").write_text(
        json.dumps({"schema": 0}), encoding="utf-8"
    )

    snapshots = store.list()

    assert [snapshot.draft for snapshot in snapshots] == [
        "尚未儲存 ✅",
        "尚未儲存 ✅",
    ]
    assert [snapshot.source_path for snapshot in snapshots] == [
        str(second.resolve()),
        str(first.resolve()),
    ]


def test_load_safely_skips_malformed_or_misdirected_snapshot(tmp_path):
    source = tmp_path / "note.md"
    other = tmp_path / "other.md"
    source.write_text("source", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")
    _save(store, source)
    snapshot_path = store.snapshot_path(source)

    snapshot_path.write_text("{", encoding="utf-8")
    assert store.load(source) is None

    saved = _save(store, source)
    payload = saved.to_dict()
    payload["source_path"] = str(other.resolve())
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load(source) is None


def test_discard_and_clear_after_save_remove_only_recovery_data(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("source stays", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")
    _save(store, source)
    snapshot_path = store.snapshot_path(source)
    snapshot_path.with_name(snapshot_path.name + ".tmp").write_text(
        "partial", encoding="utf-8"
    )
    snapshot_path.with_name(snapshot_path.name + ".bak").write_text(
        "old", encoding="utf-8"
    )

    assert store.clear_after_save(source) is True
    assert store.load(source) is None
    assert not snapshot_path.exists()
    assert not snapshot_path.with_name(snapshot_path.name + ".tmp").exists()
    assert not snapshot_path.with_name(snapshot_path.name + ".bak").exists()
    assert source.read_text(encoding="utf-8") == "source stays"
    assert store.clear_after_save(source) is False

    _save(store, source)
    assert store.discard(source) is True
    assert store.load(source) is None


def test_repeated_save_replaces_one_snapshot_without_backup(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("source", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")

    _save(store, source, "draft one")
    _save(
        store,
        source,
        "draft two",
        updated_at="2026-08-25T10:00:00.000Z",
    )

    assert store.load(source).draft == "draft two"
    assert len(list(store.directory.glob("*.json"))) == 1
    path = store.snapshot_path(source)
    assert not path.with_name(path.name + ".bak").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("newline", "invalid"),
        ("cursor", -1),
        ("anchor", True),
        ("scroll", -1),
        ("encoding", ""),
        ("updated_at", "not-a-date"),
        ("source_signature", (1, -1)),
    ],
)
def test_save_rejects_invalid_state(tmp_path, field, value):
    source = tmp_path / "note.md"
    source.write_text("source", encoding="utf-8")
    store = RecoveryStore(tmp_path / "recovery")
    state = {
        "encoding": "utf-8",
        "newline": "\n",
        "cursor": 0,
        "anchor": 0,
        "scroll": 0,
        "updated_at": "2026-08-25T08:00:00.000Z",
    }
    state[field] = value

    with pytest.raises((TypeError, ValueError)):
        store.save(source, "draft", **state)

    assert not store.snapshot_path(source).exists()
    assert source.read_text(encoding="utf-8") == "source"


def test_module_api_uses_the_default_appdata_store(tmp_path, monkeypatch):
    source = tmp_path / "note.md"
    source.write_text("source", encoding="utf-8")
    appdata = tmp_path / "appdata" / "recovery"
    monkeypatch.setattr(recovery, "_default_recovery_dir", lambda: appdata)

    saved = recovery.save(
        source,
        "module draft",
        encoding="utf-8",
        newline="\n",
        cursor=2,
        anchor=1,
        scroll=5,
        updated_at="2026-08-25T08:00:00.000Z",
    )

    assert recovery.load(source) == saved
    assert recovery.list_snapshots() == [saved]
    assert recovery.clear_after_save(source) is True
    assert recovery.load(source) is None
