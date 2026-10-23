"""Versioning, bounded-memory and responsive library search regressions."""

import os
import statistics
import time
from threading import Event

import pytest

from app import global_search
from app.global_search import FileSearchResult, GlobalSearchView, SearchHit, SearchReport
from app.md_converter import read_text_detailed
from app.search_index import SearchCancelled, SearchIndex, directory_versions, file_version


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(global_search, "load_excluded_folders", lambda: [])


def search(root, query, index, **kwargs):
    return global_search.search_document_files([root], query, index=index, **kwargs)


def test_unchanged_files_are_decoded_once_and_changed_file_only_is_refreshed(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.txt"
    first.write_text("中文 needle", encoding="utf-8")
    second.write_text("second needle", encoding="utf-8")
    index = SearchIndex()

    assert search(tmp_path, "needle", index).searched_files == 2
    assert index.reads == 2
    assert len(search(tmp_path, "中", index).results) == 1
    assert index.reads == 2
    assert index.hits == 2
    first.write_text("changed needle", encoding="utf-8")
    assert search(tmp_path, "changed", index).results[0].path == first
    assert index.reads == 3


def test_equal_size_rewrite_with_restored_mtime_is_invalidated(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("first", encoding="utf-8")
    index = SearchIndex()
    assert search(tmp_path, "first", index).results
    before = path.stat()
    time.sleep(0.01)
    path.write_text("other", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns

    assert not search(tmp_path, "first", index).results
    assert search(tmp_path, "other", index).results
    assert index.reads == 2


def test_atomic_replacement_same_timestamp_and_size_is_invalidated(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("first", encoding="utf-8")
    index = SearchIndex()
    assert search(tmp_path, "first", index).results
    before = path.stat()
    replacement = tmp_path / "replacement.tmp"
    replacement.write_text("other", encoding="utf-8")
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)
    assert search(tmp_path, "other", index).results
    assert not search(tmp_path, "first", index).results


@pytest.mark.skipif(os.name != "nt", reason="Windows bulk metadata path")
def test_batched_versions_match_direct_versions_and_support_multiple_buffers(tmp_path):
    for number in range(600):
        (tmp_path / f"{number:04d}-中文.md").write_text(str(number), encoding="utf-8")
    versions = directory_versions(tmp_path, lambda: False)
    assert len(versions) == 600
    for name in ("0000-中文.md", "0300-中文.md", "0599-中文.md"):
        assert versions[name] == file_version(tmp_path / name)


def test_external_hardlink_write_with_restored_mtime_invalidates_cached_source(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "note.md"
    alias = tmp_path / "alias.md"
    path.write_text("first", encoding="utf-8")
    index = SearchIndex()
    assert search(root, "first", index).results
    try:
        os.link(path, alias)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem")
    before = path.stat()
    alias.write_text("other", encoding="utf-8")
    os.utime(alias, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not search(root, "first", index).results
    assert search(root, "other", index).results


def test_same_names_new_files_deletion_and_rename_follow_current_sources(tmp_path):
    first = tmp_path / "one" / "same.md"
    second = tmp_path / "two" / "same.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first needle", encoding="utf-8")
    second.write_text("second needle", encoding="utf-8")
    index = SearchIndex()
    assert len(search(tmp_path, "needle", index).results) == 2
    first.unlink()
    renamed = second.with_name("renamed.md")
    second.rename(renamed)
    added = tmp_path / "added.txt"
    added.write_text("third needle", encoding="utf-8")
    assert {result.path for result in search(tmp_path, "needle", index).results} == {renamed, added}


@pytest.mark.parametrize("query", ["中", "中文", "文. A", ".*", "İ", "ı", "s", "k", "needle"])
def test_index_keeps_exact_unicode_literal_regex_spans_and_line_numbers(tmp_path, query):
    path = tmp_path / "note.md"
    text = "heading\r\n中文. A.*\r\nİ ı i I ſ S K K\r\nNEEDLE needle\r\n"
    path.write_bytes(text.encode("utf-8"))
    uncached = search(tmp_path, query, SearchIndex(max_bytes=0))
    index = SearchIndex()
    search(tmp_path, "prime", index)
    cached = search(tmp_path, query, index)
    assert cached == uncached
    assert index.reads == 1
    assert index.hits == 1


def test_small_budget_and_oversize_files_are_fully_searched(tmp_path):
    for number in range(8):
        (tmp_path / f"{number}.md").write_text("prefix\n" * 1000 + "needle", encoding="utf-8")
    index = SearchIndex(max_bytes=100)
    report = search(tmp_path, "needle", index)
    assert len(report.results) == 8
    assert all(result.hits[0].line_number == 1001 for result in report.results)
    assert index.entry_count == 0
    assert index.retained_bytes <= 100


def test_lru_eviction_and_explicit_rebuild_are_bounded(tmp_path):
    index = SearchIndex(max_bytes=1000)
    for number in range(8):
        path = tmp_path / f"{number}.md"
        path.write_text("needle", encoding="utf-8")
        assert index.read(path, read_text_detailed, lambda: False) == "needle"
    assert 0 < index.entry_count < 8
    assert index.evictions > 0
    assert index.retained_bytes <= index.max_bytes
    index.invalidate()
    assert index.entry_count == index.retained_bytes == 0


def test_peek_reuses_only_the_supplied_fresh_version_without_reading(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("", encoding="utf-8")
    index = SearchIndex()
    assert index.peek(path, file_version(path)) is None
    index.read(path, read_text_detailed, lambda: False)
    assert index.peek(path, file_version(path)) == ""
    assert index.reads == 1
    path.write_text("changed", encoding="utf-8")
    assert index.peek(path, file_version(path)) is None
    assert index.reads == 1


def test_decoder_race_retries_and_never_caches_a_mixed_version(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("before", encoding="utf-8")
    calls = 0

    def racing_decoder(filename):
        nonlocal calls
        calls += 1
        decoded = read_text_detailed(filename)
        if calls == 1:
            filename.write_text("after rewrite", encoding="utf-8")
        return decoded

    index = SearchIndex()
    assert index.read(path, racing_decoder, lambda: False) == "after rewrite"
    assert calls == 2
    assert index.read(path, racing_decoder, lambda: False) == "after rewrite"
    assert calls == 2


def test_continuously_changing_source_is_reported_without_stale_cache(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("needle", encoding="utf-8")

    def racing_decoder(filename):
        decoded = read_text_detailed(filename)
        filename.write_text(decoded[0] + " changed", encoding="utf-8")
        return decoded

    monkeypatch.setattr(global_search, "read_text_detailed", racing_decoder)
    index = SearchIndex()
    report = search(tmp_path, "needle", index)
    assert not report.results
    assert len(report.issues) == 1
    assert "持續變動" in report.issues[0].reason
    assert not index.entry_count


def test_cancelled_decode_does_not_publish_or_cache_text(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("needle", encoding="utf-8")
    cancelled = Event()

    def decoder(filename):
        cancelled.set()
        return read_text_detailed(filename)

    index = SearchIndex()
    with pytest.raises(SearchCancelled):
        index.read(path, decoder, cancelled.is_set)
    assert not index.entry_count


def test_unavailable_version_metadata_falls_back_to_full_read(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("needle", encoding="utf-8")

    def unavailable(_path):
        raise OSError("Metadata provider unavailable")

    monkeypatch.setattr("app.search_index.file_version", unavailable)
    monkeypatch.setattr(global_search, "directory_versions", lambda *_args: {})
    index = SearchIndex()
    report = search(tmp_path, "needle", index)
    assert report.results[0].path == path
    assert not report.issues
    assert not index.entry_count
    path.write_text("other", encoding="utf-8")
    assert not search(tmp_path, "needle", index).results


def test_overlays_replace_disk_text_and_include_deleted_buffer_only_in_scope(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    note = root / "note.md"
    note.write_text("disk needle", encoding="utf-8")
    outside = tmp_path / "outside.md"
    missing = root / "missing.md"
    excluded = root / "private" / "hidden.md"
    monkeypatch.setattr(global_search, "load_excluded_folders", lambda: ["private"])
    overlays = {note: "new buffer", missing: "buffer too", outside: "buffer", excluded: "buffer"}
    index = SearchIndex()

    assert not search(root, "needle", index, overlays=overlays).results
    report = search(root, "buffer", index, overlays=overlays)
    assert {result.path for result in report.results} == {note, missing}
    assert index.entry_count == 0
    assert search(root, "needle", index).results


def test_progress_batches_cover_all_results_and_cancellation_discards_report(tmp_path):
    for number in range(5):
        (tmp_path / f"{number}.md").write_text("needle", encoding="utf-8")
    batches = []
    report = search(
        tmp_path, "needle", SearchIndex(), on_results=lambda batch, _count: batches.extend(batch)
    )
    assert set(batches) == set(report.results)
    assert len(batches) == len(report.results)
    cancelled = Event()
    report = search(
        tmp_path,
        "needle",
        SearchIndex(),
        should_cancel=cancelled.is_set,
        on_results=lambda *_args: cancelled.set(),
    )
    assert report.cancelled
    assert not report.results


def test_search_view_renders_all_rows_without_child_labels_and_rejects_stale_batches(
    qapp, tmp_path
):
    view = GlobalSearchView(lambda: [tmp_path], lambda *_args: None)
    view._input.setText("needle")
    view._debounce.stop()
    results = [
        FileSearchResult(tmp_path / f"{number}.md", (SearchHit(1, "needle", ((0, 6),)),))
        for number in range(1500)
    ]
    view._render_results("needle", results)
    deadline = time.monotonic() + 5
    while view._render_queue and time.monotonic() < deadline:
        qapp.processEvents()
    assert view._list.count() == 3000
    assert not view._render_queue
    assert view._list.findChildren(global_search.QLabel) == []
    assert view._list.itemWidget(view._list.item(1)) is None
    view._on_search_progress(view._request_id - 1, "old", results, 1500)
    assert view._list.count() == 3000
    view.show()
    qapp.processEvents()
    assert not view.grab().isNull()
    view.close()


def test_worker_queue_coalesces_to_latest_request_and_snapshots_overlays(qapp, tmp_path):
    class ManualPool:
        def __init__(self):
            self.tasks = []

        def start(self, task, priority=0):
            self.tasks.append(task)

    note = tmp_path / "note.md"
    overlay = {note: "buffer"}
    view = GlobalSearchView(
        lambda: [tmp_path], lambda *_args: None, overlays_provider=lambda: overlay
    )
    pool = ManualPool()
    view._pool = pool
    for query in ("one", "two", "three"):
        view._input.setText(query)
        view._search_now()
    assert len(pool.tasks) == 1
    assert pool.tasks[0].cancel_event.is_set()
    assert view._pending_task.query == "three"
    overlay[note] = "changed after snapshot"
    first = pool.tasks[0]
    view._on_search_finished(first.request_id, first.query, SearchReport(cancelled=True))
    assert len(pool.tasks) == 2
    assert pool.tasks[1].query == "three"
    assert pool.tasks[1].overlays[note] == "buffer"
    view.close()


@pytest.mark.skipif(
    os.environ.get("RUN_SEARCH_BENCHMARK") != "1", reason="Opt-in filesystem benchmark"
)
@pytest.mark.parametrize("file_count", [1000, 10000])
def test_search_benchmark(tmp_path, file_count):
    """Measure complete freshness scans separately from first-result latency."""
    for number in range(file_count):
        folder = tmp_path / f"folder-{number // 100:03d}"
        folder.mkdir(exist_ok=True)
        body = "# 範例文件\n中文寫作與閱讀 plain text paragraph.\n" * 12
        if number % 100 == 0:
            body += "search needle 中文測試\n"
        (folder / f"note-{number:05d}.md").write_text(body, encoding="utf-8")
    index = SearchIndex()

    def timed_search():
        started = time.perf_counter()
        first = []
        report = search(
            tmp_path,
            "needle",
            index,
            on_results=lambda *_args: first.append((time.perf_counter() - started) * 1000),
        )
        assert report.searched_files == file_count
        assert len(report.results) == file_count // 100
        return (time.perf_counter() - started) * 1000, first[0]

    cold, cold_first = timed_search()
    reads_after_cold = index.reads
    warm = [timed_search() for _iteration in range(7)]
    complete = sorted(value[0] for value in warm)
    first = sorted(value[1] for value in warm)
    assert index.reads == reads_after_cold == file_count
    print(
        f"\nSEARCH_BENCH files={file_count} cold_ms={cold:.2f} cold_first_ms={cold_first:.2f} "
        f"warm_median_ms={statistics.median(complete):.2f} warm_p95_ms={complete[-1]:.2f} "
        f"warm_first_p95_ms={first[-1]:.2f} reads={index.reads} hits={index.hits} "
        f"retained_mib={index.retained_bytes / 1024 / 1024:.2f}"
    )
