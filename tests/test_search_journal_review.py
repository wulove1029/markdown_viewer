"""Independent review probes for search cancellation and relocation cleanup."""

import os
import subprocess
from pathlib import Path
from threading import Event

import pytest

from app import file_ops, global_search, relocation_journal
from app.search_index import SearchIndex


@pytest.mark.parametrize("directory_key", ["originals_dir", "prepared_dir"])
def test_cleanup_preserves_foreign_tmp_named_like_an_entry(tmp_path, monkeypatch, directory_key):
    source, target = tmp_path / "note.md", tmp_path / "renamed.md"
    source.write_bytes(b"original note")
    committed = relocation_journal.RelocationJournal.committed
    foreign_paths = []

    def add_foreign_file(journal):
        committed(journal)
        foreign = Path(journal.payload[directory_key]) / "0.tmp"
        foreign.write_bytes(b"external data must survive")
        foreign_paths.append(foreign)

    monkeypatch.setattr(relocation_journal.RelocationJournal, "committed", add_foreign_file)
    assert file_ops.rename_document(source, target) == {str(source): str(target)}
    assert target.read_bytes() == b"original note"
    assert foreign_paths[0].exists(), "Cleanup deleted an unowned file only because of its name"
    assert foreign_paths[0].read_bytes() == b"external data must survive"


def test_final_progress_callback_cancellation_discards_report(tmp_path, monkeypatch):
    for number in range(3):
        (tmp_path / f"{number}.md").write_bytes(b"needle")
    monkeypatch.setattr(global_search, "load_excluded_folders", lambda: [])
    monkeypatch.setattr(global_search, "monotonic", lambda: 1.0)
    cancelled = Event()
    batches = []

    def on_results(batch, _scanned):
        batches.append(batch)
        if len(batches) == 2:
            cancelled.set()

    report = global_search.search_document_files(
        [tmp_path], "needle", should_cancel=cancelled.is_set,
        index=SearchIndex(), on_results=on_results,
    )
    assert len(batches) == 2
    assert cancelled.is_set()
    assert report.cancelled, "Cancellation from the final batch was ignored"
    assert not report.results


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, RuntimeError])
def test_non_oserror_after_holding_source_retains_a_recoverable_copy(
    tmp_path, monkeypatch, error_type,
):
    source, target = tmp_path / "note.md", tmp_path / "renamed.md"
    source.write_bytes(b"irreplaceable original")
    rename = file_ops._rename_no_replace

    def interrupt_publish(old, new):
        if new == target:
            raise error_type("interrupt after source was held")
        rename(old, new)

    monkeypatch.setattr(file_ops, "_rename_no_replace", interrupt_publish)
    with pytest.raises(error_type):
        file_ops.rename_document(source, target)
    copies = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert any(path.read_bytes() == b"irreplaceable original" for path in copies), (
        "Unexpected interruption deleted source, prepared copy, original backup, and journal"
    )
    results = relocation_journal.recover_pending_relocations([tmp_path])
    assert len(results) == 1
    assert results[0].status == "rolled_back", results[0].issues
    assert source.read_bytes() == b"irreplaceable original"
    assert not target.exists()
    assert relocation_journal.recover_pending_relocations([tmp_path]) == ()


def test_real_scan_keeps_all_matches_across_many_files_and_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(global_search, "load_excluded_folders", lambda: [])
    many_lines = tmp_path / "large.md"
    many_lines.write_bytes(b"needle needle\n" * 6001)
    for number in range(1201):
        (tmp_path / f"note-{number:04}.txt").write_bytes(b"needle\nneedle\n")
    batches = []
    report = global_search.search_document_files(
        [tmp_path], "needle", index=SearchIndex(max_bytes=1024),
        on_results=lambda batch, _scanned: batches.extend(batch),
    )
    assert report.searched_files == len(report.results) == len(batches) == 1202
    assert sum(result.match_count for result in report.results) == 6001 * 2 + 1201 * 2
    large_result = next(result for result in report.results if result.path == many_lines)
    assert large_result.hits[-1].line_number == 6001


def test_late_finished_signal_does_not_replace_current_query(qapp, tmp_path):
    view = global_search.GlobalSearchView(lambda: [tmp_path], lambda *_args: None)
    view._input.setText("new")
    view._debounce.stop()
    current = global_search.FileSearchResult(
        tmp_path / "new.md", (global_search.SearchHit(1, "new", ((0, 3),)),)
    )
    obsolete = global_search.FileSearchResult(
        tmp_path / "old.md", (global_search.SearchHit(1, "old", ((0, 3),)),)
    )
    view._on_search_finished(
        view._request_id, "new", global_search.SearchReport(results=(current,)),
    )
    view._on_search_finished(
        view._request_id - 1, "old", global_search.SearchReport(results=(obsolete,)),
    )
    view._on_search_progress(view._request_id - 1, "old", (obsolete,), 1)
    assert view._results == [current]
    assert view._list.count() == 2
    assert view._list.item(0).text().startswith("new.md")
    view.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_recovery_rejects_windows_junction_for_originals_directory(tmp_path, monkeypatch):
    source, target = tmp_path / "note.md", tmp_path / "renamed.md"
    source.write_bytes(b"original data")
    rename = file_ops._rename_no_replace

    def interrupt_publish(old, new):
        if new == target:
            raise RuntimeError("preserve transaction for replay")
        rename(old, new)

    monkeypatch.setattr(file_ops, "_rename_no_replace", interrupt_publish)
    with pytest.raises(RuntimeError):
        file_ops.rename_document(source, target)
    originals = next(tmp_path.glob(".markdown-relocate-originals-*"))
    held = tmp_path / "externally-held-originals"
    originals.rename(held)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(originals), str(held)],
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        results = relocation_journal.recover_pending_relocations([tmp_path])
        assert len(results) == 1
        assert results[0].status == "needs_attention"
        assert not source.exists()
        assert not target.exists()
        assert (held / "0").read_bytes() == b"original data"
        assert any(relocation_journal.default_journal_dir().glob("*.json"))
    finally:
        originals.rmdir()
