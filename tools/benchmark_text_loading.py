"""Measure complete TXT loading, Qt attachment/paint stalls, and exact saving.

Use --strategy gui for the original GUI chunk builder, or hybrid for the
long-paragraph worker path. NoWrap is applied in both runs for a fair comparison.
Offscreen component results exclude the window's status/outline and real display.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("gui", "hybrid"), default="hybrid")
    parser.add_argument("--cases", nargs="+", choices=("5mb", "50mb", "1m-line"),
                        default=("5mb", "50mb", "1m-line"))
    parser.add_argument("--unicode-line", action="store_true")
    parser.add_argument("--source-editor", action="store_true")
    parser.add_argument("--output", default="tmp/text-loading-benchmark.json")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from tools.isolated_runtime import install

    runtime = install(tempfile.mkdtemp(prefix="mdv-text-benchmark-"),
                      application="MDVTextBenchmark")
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QApplication, QPlainTextEdit

    from app import text_loading
    from app.atomic_io import atomic_write_bytes
    from app.editor import EditorView

    if args.strategy == "gui":
        text_loading._has_long_line = lambda _text: False
    app = QApplication.instance() or QApplication([])
    rows = []
    with tempfile.TemporaryDirectory(prefix="documents-", dir=runtime.root) as temp:
        for case in args.cases:
            single = case == "1m-line"
            paragraph = "文字😀 with words and tabs\t\n"
            size = 1_000_000 if single else int(case[:-2]) * 1_000_000
            source = (("文字😀a" * 250_000 if args.unicode_line else "a" * size)
                      if single else paragraph * (size // len(paragraph.encode("utf8"))))
            path = Path(temp) / f"{case}.txt"
            path.write_text(source, encoding="utf8", newline="")
            editor = EditorView() if args.source_editor else QPlainTextEdit()
            editor.resize(900, 600)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap if single
                                   else QPlainTextEdit.LineWrapMode.WidgetWidth)
            editor.setPlainText("文字😀 Warm font paths\n" * 100)
            editor.show()
            app.processEvents()
            editor.clear()
            app.processEvents()
            loader = text_loading.TextLoader()
            received, errors, gaps = [], [], []
            row = {"case": case, "bytes": path.stat().st_size,
                   "characters": len(source), "no_wrap": single}
            started = last = time.perf_counter()

            def receive(path, document, encoding, newline, signature):
                row["ready_ms"] = (time.perf_counter() - started) * 1000
                begin = time.perf_counter()
                if args.source_editor:
                    document.setProperty("text_loader_no_wrap", single)
                    editor.use_buffer_document(
                        document, plain_text_mode=True, document_path=path,
                    )
                else:
                    editor.setDocument(document)
                row["attach_ms"] = (time.perf_counter() - begin) * 1000
                received.append((document, encoding, newline))

            loader.ready.connect(receive)
            loader.failed.connect(lambda *error: errors.append(error))
            loader.start(path, editor.font())
            while not received and not errors:
                app.processEvents()
                now = time.perf_counter()
                gaps.append((now - last) * 1000)
                last = now
                if now - started > 120:
                    raise TimeoutError(f"Loading {case} did not finish")
                time.sleep(.001)
            if errors:
                raise RuntimeError(errors)
            row["load_and_attach_gap_max_ms"] = max(gaps)
            begin = time.perf_counter()
            editor.viewport().repaint()
            app.processEvents()
            now = time.perf_counter()
            row["first_repaint_ms"] = (now - begin) * 1000
            gaps.append((now - last) * 1000)
            row["ui_gap_max_ms"] = max(gaps)
            row["total_ms"] = (now - started) * 1000
            document, encoding, newline = received[0]
            assert document.toPlainText() == source
            assert document.thread() == app.thread()
            cursor = QTextCursor(document)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(" 編輯😀")
            document.undo()
            assert document.toPlainText() == source
            document.redo()
            saved = document.toPlainText().replace("\n", newline).encode(encoding)
            target = Path(temp) / f"{case}-saved.txt"
            atomic_write_bytes(target, saved)
            assert target.read_bytes().decode(encoding) == source + " 編輯😀"
            row["exact_roundtrip_and_save"] = True
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            loader.cancel()
            if args.source_editor:
                editor.release_buffer_document()
            else:
                editor.setDocument(None)
            editor.close()
            document.deleteLater()
            editor.deleteLater()
            loader.deleteLater()
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert QThreadPool.globalInstance().waitForDone(10_000)
    result = {"strategy": args.strategy, "python": platform.python_version(),
              "temp": str(runtime.root),
              "pyside": pyside_version, "platform": platform.platform(),
              "unicode_line": args.unicode_line, "rows": rows,
              "source_editor": args.source_editor,
              "scope": "Offscreen TXT component; all Qt attach/paint stalls retained."}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
