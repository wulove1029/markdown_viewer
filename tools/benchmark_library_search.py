"""Measure real search-view first rows, debounce and complete results separately.

The generated library and all settings are isolated. Offscreen repaint is a
proxy; it does not measure monitor presentation or an installed executable.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path


def summary(values):
    ordered = sorted(values)
    return {"n": len(values), "p50_ms": statistics.median(values),
            "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1], "max_ms": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.files < 100 or args.repeats < 30:
        parser.error("Use at least 100 files and 30 repeats")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from tools.isolated_runtime import install

    folder = Path(tempfile.mkdtemp(prefix="mdv-search-benchmark-"))
    install(folder, application="MDVSearchBenchmark")
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication

    from app import global_search
    from app.search_index import SearchIndex

    global_search.load_excluded_folders = lambda: []
    app = QApplication([])
    corpus = folder / "library"
    total_bytes = 0
    for number in range(args.files):
        parent = corpus / f"folder-{number // 100:04d}"
        parent.mkdir(parents=True, exist_ok=True)
        body = "# 文件\n一般閱讀 writing paragraph.\n" * 12
        if number % 100 == 0:
            body += "尋 找資料 needle 中文 mixed\n"
        raw = body.encode("utf-8")
        (parent / f"note-{number:05d}.md").write_bytes(raw)
        total_bytes += len(raw)

    class TracedSearchView(global_search.GlobalSearchView):
        def __init__(self):
            self.dispatched_at = 0.0
            self.batch_durations = []
            super().__init__(lambda: [corpus], lambda *_args: None)

        def _launch_task(self, task):
            self.dispatched_at = time.perf_counter()
            super()._launch_task(task)

        def _append_pending_rows(self):
            started = time.perf_counter()
            super()._append_pending_rows()
            self.batch_durations.append((time.perf_counter() - started) * 1000)

    view = TracedSearchView()
    view._index = SearchIndex()
    view.resize(900, 650)
    view.show()
    app.processEvents()
    samples = []
    queries = ["needle", "尋", "找資"]
    expected_files = (args.files + 99) // 100

    def run(query, debounced, warm):
        view._input.clear()
        app.processEvents()
        started = time.perf_counter()
        view.dispatched_at = 0
        view._input.setText(query)
        if not debounced:
            view._search_now()
        first = None
        max_gap = 0.0
        previous = started
        while True:
            app.processEvents()
            now = time.perf_counter()
            max_gap = max(max_gap, (now - previous) * 1000)
            previous = now
            if first is None and view._list.count():
                view._list.viewport().repaint()
                first = time.perf_counter()
            if view._report is not None and not view._render_queue and not view._tasks:
                break
            if now - started > 60:
                raise RuntimeError("Search did not finish within 60 seconds")
            time.sleep(0.001)
        assert len(view._results) == expected_files, (query, len(view._results))
        assert view._report.searched_files == args.files
        assert view._list.count() == expected_files * 2
        assert first is not None
        samples.append({
            "query": query, "warm": warm, "debounced": debounced,
            "first_row_end_to_end_ms": (first - started) * 1000,
            "first_row_after_dispatch_ms": (first - view.dispatched_at) * 1000,
            "debounce_and_dispatch_ms": (view.dispatched_at - started) * 1000,
            "complete_ms": (time.perf_counter() - started) * 1000,
            "max_ui_gap_ms": max_gap, "result_files": len(view._results),
            "decoded_total": view._index.reads,
        })

    run("needle", False, False)
    for debounced in (False, True):
        for query in queries:
            for _ in range(args.repeats):
                run(query, debounced, True)
    groups = []
    for debounced in (False, True):
        for query in queries:
            rows = [row for row in samples if row["warm"] and row["query"] == query
                    and row["debounced"] == debounced]
            groups.append({"query": query, "debounced": debounced, **{
                key: summary([row[key] for row in rows]) for key in (
                    "first_row_end_to_end_ms", "first_row_after_dispatch_ms",
                    "debounce_and_dispatch_ms", "complete_ms", "max_ui_gap_ms")}})
    report = {
        "python": sys.version, "pyside": pyside_version, "platform": platform.platform(),
        "cpu": platform.processor(), "files": args.files, "corpus_bytes": total_bytes,
        "mode": f"{os.environ['QT_QPA_PLATFORM']} GlobalSearchView; filesystem cache warm",
        "debounce_interval_ms": view._debounce.interval(),
        "temp": str(folder), "samples": samples, "summary": groups,
        "ui_batch_duration_ms": summary(view.batch_durations),
        "retained_bytes": view._index.retained_bytes,
        "passed": view._index.reads == args.files,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("passed", "files", "summary", "ui_batch_duration_ms")},
                     ensure_ascii=False, indent=2))
    view.close()
    QThreadPool.globalInstance().waitForDone(10000)
    app.processEvents()


if __name__ == "__main__":
    main()
