"""Measure source-editor commit/paint and exact incremental writing statistics.

This offscreen component benchmark does not measure physical display latency,
Windows IME candidate UI, Office, or installed EXE performance.
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
    return {
        "n": len(values),
        "p50_ms": statistics.median(values),
        "p95_ms": ordered[math.ceil(len(values) * 0.95) - 1],
        "max_ms": max(values),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="tmp/editor-writing-benchmark.json")
    parser.add_argument("--repeats", type=int, default=100)
    args = parser.parse_args()
    if args.repeats < 30:
        parser.error("--repeats must be at least 30")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from tools.isolated_runtime import install

    runtime = install(tempfile.mkdtemp(prefix="mdv-writing-benchmark-"),
                      application="MDVWritingBenchmark")
    from PySide6 import __version__ as pyside_version
    from PySide6.QtGui import QInputMethodEvent, QTextCursor
    from PySide6.QtWidgets import QApplication

    from app.editor import EditorView
    from app.editor_status import (
        DocumentWritingCounter,
        _visible_markdown_text,
        count_writing_units,
    )

    app = QApplication.instance() or QApplication([])
    rows = []
    paragraph = "繁中寫作😀 English note-taking [文件](notes/example.md)\n"
    for size in (100_000, 1_000_000):
        text = paragraph * math.ceil(size / len(paragraph.encode("utf-8")))
        editor = EditorView()
        editor.resize(900, 650)
        editor.set_content(text)
        editor.show()
        editor.setFocus()
        app.processEvents()
        started = time.perf_counter()
        counter = DocumentWritingCounter.for_document(
            editor.document(), markdown=True, background=True
        )
        initial_call_ms = (time.perf_counter() - started) * 1000
        build_turns = []
        while not counter.ready:
            turn = time.perf_counter()
            app.processEvents()
            build_turns.append((time.perf_counter() - turn) * 1000)
        initial_ms = (time.perf_counter() - started) * 1000
        expected = count_writing_units(_visible_markdown_text(text))
        assert counter.writing_units == expected
        cursor = QTextCursor(editor.document().findBlockByNumber(editor.blockCount() // 2))
        editor.setTextCursor(cursor)
        app.processEvents()
        commits = []
        full_counts = []
        changed_blocks = counter.blocks_recounted
        for _ in range(args.repeats):
            QApplication.sendEvent(editor, QInputMethodEvent("ㄒ", []))
            commit = QInputMethodEvent("", [])
            commit.setCommitString("新😀")
            started = time.perf_counter()
            QApplication.sendEvent(editor, commit)
            units = counter.writing_units
            app.processEvents()
            editor.viewport().repaint()
            commits.append((time.perf_counter() - started) * 1000)
            expected += 1
            assert units == expected
        for _ in range(30):
            started = time.perf_counter()
            full = count_writing_units(_visible_markdown_text(editor.toPlainText()))
            full_counts.append((time.perf_counter() - started) * 1000)
            assert full == expected
        rows.append({
            "utf8_bytes": len(text.encode("utf-8")),
            "blocks": editor.blockCount(),
            "counter_initial_build_ms": initial_ms,
            "counter_initial_call_ms": initial_call_ms,
            "counter_background_event_turn": summary(build_turns),
            "commit_statistics_offscreen_paint": summary(commits),
            "full_snapshot_recount": summary(full_counts),
            "recounted_blocks_during_commits": counter.blocks_recounted - changed_blocks,
            "uses_full_fallback": counter.uses_full_fallback,
            "raw_commit_ms": commits,
            "raw_full_recount_ms": full_counts,
        })
        editor.close()
    result = {
        "python": sys.version,
        "pyside": pyside_version,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "qpa_platform": os.environ["QT_QPA_PLATFORM"],
        "temp": str(runtime.root),
        "scope": "Source EditorView component, synthetic IME commit + statistics + offscreen paint",
        "not_measured": "Physical display, Windows IME candidate UI, Office, EXE, full MainWindow",
        "results": rows,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "results": [
        {key: value for key, value in row.items() if not key.startswith("raw_")}
        for row in rows
    ]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
