"""Kill a real worker process and verify safe, idempotent relocation replay."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from app import file_ops, relocation_journal
from app.relocation_journal import recover_pending_relocations


@pytest.fixture(autouse=True)
def isolated_journal(tmp_path, monkeypatch):
    directory = tmp_path.parent / (tmp_path.name + "-journals")
    monkeypatch.setattr(relocation_journal, "default_journal_dir", lambda: directory)
    return directory


_CHILD = r"""
import sys
import time
from pathlib import Path
from app import file_ops, relocation_journal
root, journal_dir, marker = map(Path, sys.argv[1:4])
checkpoint = sys.argv[4]
relocation_journal.default_journal_dir = lambda: journal_dir
old, new = root / "original.md", root / "archive" / "renamed.md"
backlink = root / "referrer.md"
rename = file_ops._rename_no_replace

def stop_here(name):
    if checkpoint == name:
        marker.write_text(name, encoding="utf-8")
        while True:
            time.sleep(1)

def checkpoint_rename(source, target):
    rename(source, target)
    if target.parent.name.startswith(".markdown-relocate-originals-") and target.name == "0":
        stop_here("held_main")
    if target == new:
        stop_here("published_main")
    if target == new.with_name(new.name + ".notes.json"):
        stop_here("published_sidecar")
    if target == backlink:
        stop_here("published_backlink")

file_ops._rename_no_replace = checkpoint_rename
commit = relocation_journal.RelocationJournal.committed
def checkpoint_commit(self):
    commit(self)
    stop_here("committed")
relocation_journal.RelocationJournal.committed = checkpoint_commit
record = relocation_journal.RelocationJournal.record_prepared
def checkpoint_record(self, index):
    record(self, index)
    if index == 0:
        stop_here("preparing")
relocation_journal.RelocationJournal.record_prepared = checkpoint_record
before = backlink.read_bytes()
updates = {backlink: (before, before.replace(b"original", b"renamed"))}
file_ops.rename_document(old, new, backlink_updates=updates)
"""


def create_library(root):
    root.mkdir()
    (root / "archive").mkdir()
    files = {
        "original.md": b"# original note\r\n",
        "original.md.notes.json": b'{"annotation":"original"}',
        "original.md.highlights.json": b'{"highlight":"original"}',
        "original.md.bak": b"# earlier note\r\n",
        "referrer.md": b"[[original]]\r\n",
    }
    for name, data in files.items():
        (root / name).write_bytes(data)
    return files


def kill_worker_and_wait(process):
    # Windows venv's python.exe is a launcher. Its wait() can finish while the
    # actual interpreter is still releasing file locks after forced exit.
    # Capture only this worker's descendants before terminating the launcher.
    workers = psutil.Process(process.pid).children(recursive=True)
    process.kill()
    output = process.communicate(timeout=5)
    for worker in workers:
        worker.wait(timeout=5)
    return output


def kill_relocation(tmp_path, journal_dir, checkpoint):
    root = tmp_path / "library"
    files = create_library(root)
    marker = tmp_path / "child-ready.txt"
    process = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            _CHILD,
            str(root),
            str(journal_dir),
            str(marker),
            checkpoint,
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not marker.exists():
            if process.poll() is None:
                stdout, stderr = kill_worker_and_wait(process)
            else:
                stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"Child did not reach checkpoint {checkpoint}: {stdout}\n{stderr}")
        kill_worker_and_wait(process)
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            kill_worker_and_wait(process)
    return root, files


@pytest.mark.parametrize(
    "checkpoint",
    ["preparing", "held_main", "published_main", "published_sidecar", "published_backlink"],
)
def test_forced_process_exit_restores_document_sidecars_backups_and_backlinks(
    tmp_path, isolated_journal, checkpoint
):
    root, files = kill_relocation(tmp_path, isolated_journal, checkpoint)
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert len(results) == 1
    assert results[0].status == "rolled_back", results[0].issues
    for name, expected in files.items():
        assert (root / name).read_bytes() == expected
    assert list((root / "archive").iterdir()) == []
    assert not list(root.glob(".markdown-relocate-*"))
    assert not list(isolated_journal.glob("*.json"))
    assert recover_pending_relocations([root], journal_dir=isolated_journal) == ()


def test_committed_process_exit_keeps_published_files_and_cleans_only_owned_temporaries(
    tmp_path, isolated_journal
):
    root, files = kill_relocation(tmp_path, isolated_journal, "committed")
    unrelated = root / "archive" / "keep.txt"
    unrelated.write_bytes(b"unrelated")
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "committed", results[0].issues
    assert results[0].source == str(root / "original.md")
    assert results[0].target == str(root / "archive" / "renamed.md")
    for name, expected in files.items():
        if name == "referrer.md":
            assert (root / name).read_bytes() == b"[[renamed]]\r\n"
        else:
            assert not (root / name).exists()
            assert (root / "archive" / name.replace("original", "renamed")).read_bytes() == expected
    assert unrelated.read_bytes() == b"unrelated"
    assert not list(root.rglob(".markdown-relocate-*"))


def test_external_destination_edit_with_same_size_and_mtime_is_retained(tmp_path, isolated_journal):
    root, files = kill_relocation(tmp_path, isolated_journal, "published_main")
    target = root / "archive" / "renamed.md"
    before = target.stat()
    changed = b"X" * before.st_size
    target.write_bytes(changed)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "needs_attention"
    assert target.read_bytes() == changed
    assert (root / "original.md").read_bytes() == files["original.md"]
    assert list(isolated_journal.glob("*.json"))


def test_external_source_replacement_is_never_overwritten(tmp_path, isolated_journal):
    root, _files = kill_relocation(tmp_path, isolated_journal, "published_main")
    source = root / "original.md"
    source.write_bytes(b"new external content")
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "needs_attention"
    assert source.read_bytes() == b"new external content"
    retained = list(root.glob(".markdown-relocate-originals-*"))
    assert (retained[0] / "0").read_bytes() == b"# original note\r\n"


def test_committed_external_change_keeps_original_backup_for_review(tmp_path, isolated_journal):
    root, _files = kill_relocation(tmp_path, isolated_journal, "committed")
    target = root / "archive" / "renamed.md"
    target.write_bytes(b"later external edit")
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "needs_attention"
    assert target.read_bytes() == b"later external edit"
    assert not (root / "original.md").exists()
    assert list(root.glob(".markdown-relocate-originals-*"))


@pytest.mark.parametrize(
    "tamper", ["outside_root", "unrelated_entry", "parent_traversal", "directory_identity"]
)
def test_untrusted_manifest_paths_are_rejected_before_mutating_any_file(
    tmp_path, isolated_journal, tamper
):
    root, _files = kill_relocation(tmp_path, isolated_journal, "published_main")
    manifest = next(isolated_journal.glob("*.json"))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must survive")
    if tamper == "outside_root":
        payload["source"] = str(outside)
    elif tamper == "unrelated_entry":
        payload["entries"][1]["source"] = str(root / "unrelated.md")
    elif tamper == "parent_traversal":
        payload["entries"][1]["source"] = str(root / ".." / "outside.txt")
    else:
        payload["originals_identity"] = [0, 0]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    target = root / "archive" / "renamed.md"
    original = list(root.glob(".markdown-relocate-originals-*"))[0] / "0"
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "needs_attention"
    assert not (root / "original.md").exists()
    assert original.read_bytes() == target.read_bytes() == b"# original note\r\n"
    assert outside.read_bytes() == b"must survive"


def test_active_transaction_is_not_replayed(tmp_path, isolated_journal):
    root = tmp_path / "library"
    root.mkdir()
    lock = relocation_journal._JournalLock(isolated_journal)
    try:
        results = recover_pending_relocations([root], journal_dir=isolated_journal)
        assert results[0].status == "active"
    finally:
        lock.close()


def test_unknown_temporary_files_are_preserved(tmp_path, isolated_journal):
    root, _files = kill_relocation(tmp_path, isolated_journal, "committed")
    directory = list(root.glob(".markdown-relocate-originals-*"))[0]
    unknown = directory / "someone-elses-file.txt"
    unknown.write_bytes(b"must not be removed")
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "needs_attention"
    assert unknown.read_bytes() == b"must not be removed"


def test_same_process_rollback_detects_equal_metadata_external_edit(
    tmp_path, isolated_journal, monkeypatch
):
    source, target = tmp_path / "note.md", tmp_path / "renamed.md"
    source.write_bytes(b"original")
    stage = file_ops._stage_relocation_file

    def changed_source(original, prepared, data):
        stage(original, prepared, data)
        before = original.stat()
        original.write_bytes(b"external")
        os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))

    monkeypatch.setattr(file_ops, "_stage_relocation_file", changed_source)
    with pytest.raises(OSError, match="已變更"):
        file_ops.rename_document(source, target)
    assert source.read_bytes() == b"external"
    assert not target.exists()
    assert not list(isolated_journal.glob("*.json"))


def test_recovery_can_resume_after_a_second_forced_process_exit(tmp_path, isolated_journal):
    root, files = kill_relocation(tmp_path, isolated_journal, "published_backlink")
    marker = tmp_path / "recovery-ready.txt"
    child = r"""
import sys
import time
from pathlib import Path
from app import relocation_journal
root, journal, marker = map(Path, sys.argv[1:])
restore = relocation_journal._exclusive_restore
def interrupted_restore(source, target):
    restore(source, target)
    marker.write_text("restored", encoding="utf-8")
    while True:
        time.sleep(1)
relocation_journal._exclusive_restore = interrupted_restore
relocation_journal.recover_pending_relocations([root], journal_dir=journal)
"""
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", child, str(root), str(isolated_journal), str(marker)],
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        kill_worker_and_wait(process)
    finally:
        if process.poll() is None:
            kill_worker_and_wait(process)
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "rolled_back", results[0].issues
    assert {name: (root / name).read_bytes() for name in files} == files


def test_replaced_temporary_directory_link_is_rejected(tmp_path, isolated_journal):
    root, _files = kill_relocation(tmp_path, isolated_journal, "published_main")
    original = list(root.glob(".markdown-relocate-originals-*"))[0]
    held = original.with_name("held-originals")
    original.rename(held)
    try:
        original.symlink_to(held, target_is_directory=True)
    except OSError:
        held.rename(original)
        pytest.skip("Directory symlinks require permission on this test host")
    results = recover_pending_relocations([root], journal_dir=isolated_journal)
    assert results[0].status == "needs_attention"
    assert not (root / "original.md").exists()
    assert (held / "0").read_bytes() == b"# original note\r\n"
