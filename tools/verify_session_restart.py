"""Verify session persistence across isolated real Qt processes, including a crash."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def child(workspace: Path, stage: str, output: Path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6 import QtCore
    from PySide6.QtCore import QEvent, QSettings, QThreadPool
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    class IsolatedSettings(QSettings):
        def __init__(self, *_args, **_kwargs):
            super().__init__(str(workspace / "settings.ini"), QSettings.Format.IniFormat)
            self.setFallbacksEnabled(False)

    # Patch before importing any application modules. The org/app constructor
    # ignores setDefaultFormat on Windows and otherwise writes the real registry.
    QtCore.QSettings = IsolatedSettings
    settings = IsolatedSettings()
    assert Path(settings.fileName()).resolve() == workspace / "settings.ini"
    if stage == "save":
        assert not settings.allKeys()
    settings.setValue("update_check_enabled", False)
    settings.sync()
    import app.recovery
    import app.tag_index
    from app.url_schemes import register_document_schemes
    app.tag_index._default_index_path = lambda: workspace / "tags.json"
    app.recovery._default_recovery_dir = lambda: workspace / "recovery"
    register_document_schemes()
    app = QApplication([])
    app.setApplicationName("MarkdownViewerSessionSmoke")
    for name in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    from app import session_state
    from app.window import MainWindow

    files = [workspace / f"reading-{i}.pdf" for i in range(4)]
    window = MainWindow()
    window.resize(1180, 820)
    window.show()
    view = window._pdf_view

    def wait_until(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            QTest.qWait(20)
        raise AssertionError("Timed out waiting for PDF")

    def tab_names():
        return [Path(window._tab_bar.tabData(i)).name for i in range(window._tab_bar.count())]

    session_state.restore_startup(window, str(files[3]) if stage == "file-launch" else "")
    if stage == "save":
        assert tab_names() == []
        for index, page in enumerate((3, 1, 0)):
            window.open_path(str(files[index]))
            wait_until(lambda: bool(view._cache))
            view.jump_to_page(page)
            app.processEvents()
            assert view.current_page() == page
        window._tab_bar.moveTab(2, 0)
        window._tab_bar.setCurrentIndex(2)
        assert tab_names() == [files[i].name for i in (2, 0, 1)]
    elif stage == "restore":
        assert tab_names() == [files[i].name for i in (2, 0, 1)]
        assert window._current_file == files[1]
        assert view.current_page() == 1
        for index, page in ((2, 0), (0, 3), (1, 1)):
            window.open_path(str(files[index]))
            app.processEvents()
            assert view.current_page() == page
    elif stage == "file-launch":
        assert tab_names() == [files[i].name for i in (2, 0, 1, 3)]
        assert window._current_file == files[3]
        assert view.current_page() == 0
    elif stage == "abrupt":
        assert window._on_tab_close(1)
        window._tab_bar.moveTab(2, 0)
        window._tab_bar.setCurrentIndex(2)
        view.jump_to_page(2)
    elif stage == "after-abrupt":
        assert tab_names() == [files[i].name for i in (3, 2, 1)]
        assert window._current_file == files[1]
        assert view.current_page() == 2
    QTest.qWait(450)
    assert json.loads(settings.value("open_tabs")) == [
        window._tab_bar.tabData(i) for i in range(window._tab_bar.count())
    ]
    result = {
        "stage": stage, "tabs": tab_names(), "active": window._current_file.name,
        "page": view.current_page(), "checkpoint_before_close": True,
        "close_event": stage != "abrupt", "status": "passed",
        "settings_isolated": True,
    }
    if stage in ("restore", "file-launch", "after-abrupt"):
        wait_until(lambda: bool(view._cache))
        assert window.grab().save(str(output / f"session-{stage}.png"))
    (output / f"session-{stage}.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    if stage == "abrupt":
        # Intentional hard exit: no QWidget.close(), no QApplication teardown.
        os._exit(0)
    window.close()
    view._reset_render_pipeline(clear_cache=True)
    wait_until(lambda: not view._render_scheduler._sessions_by_generation)
    QThreadPool.globalInstance().waitForDone(10000)
    view._doc.close()
    window._renderer.setHtml("")
    window.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    os._exit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--stage")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.stage:
        child(args.workspace, args.stage, output)
        return
    import pymupdf
    from PySide6.QtCore import QSettings
    native = QSettings("markdown-viewer", "MarkdownViewer")
    session_keys = ("open_tabs", "active_tab", "active_tab_path", "last_file", "pdf_last_pages")
    before = {key: native.value(key) for key in session_keys}
    with tempfile.TemporaryDirectory(prefix="mdv-session-restart-") as directory:
        workspace = Path(directory)
        files = []
        for index in range(4):
            path = workspace / f"reading-{index}.pdf"
            with pymupdf.open() as document:
                for page_index in range(5):
                    page = document.new_page()
                    page.insert_text((55, 80), f"Document {index} - page {page_index + 1}", fontsize=24)
                    page.insert_text((55, 125), "Session restore smoke", fontsize=16)
                document.save(path)
            files.append(path)
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
        results = []
        for stage in ("save", "restore", "file-launch", "abrupt", "after-abrupt"):
            run = subprocess.run(
                [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                 "--output", str(output), "--workspace", str(workspace), "--stage", stage],
                capture_output=True, text=True, encoding="utf-8", timeout=45,
            )
            (output / f"session-{stage}.txt").write_text(run.stdout + run.stderr, encoding="utf-8")
            assert run.returncode == 0, f"{stage}: {run.returncode}\n{run.stdout}\n{run.stderr}"
            result = json.loads((output / f"session-{stage}.json").read_text(encoding="utf-8"))
            results.append(result)
            print(json.dumps(result), flush=True)
        assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    native.sync()
    assert before == {key: native.value(key) for key in session_keys}, "Native session changed"
    report = {"status": "passed", "separate_processes": 5,
              "source_pdfs_unchanged": True, "native_session_unchanged": True,
              "temporary_files_cleaned": True, "stages": results}
    (output / "session-restart-smoke.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Cross-process session smoke passed")


if __name__ == "__main__":
    main()
