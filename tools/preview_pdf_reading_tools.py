"""Run an isolated real-window PDF smoke and save visual evidence."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt, QThreadPool, QTimer
from PySide6.QtGui import QContextMenuEvent, QFontDatabase, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mdv-pdf-tools-") as folder:
        root = Path(folder)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, folder)
        settings = QSettings("markdown-viewer", "MarkdownViewer")
        settings.setValue("update_check_enabled", False)
        settings.sync()
        import app.tag_index
        import app.recovery
        app.tag_index._default_index_path = lambda: root / "tags.json"
        app.recovery._default_recovery_dir = lambda: root / "recovery"
        from app.window import MainWindow

        app = QApplication([])
        app.setApplicationName("MarkdownViewerPdfToolsSmoke")
        for name in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        path = root / "電路圖閱讀工具.pdf"
        with pymupdf.open() as doc:
            for number in range(2):
                page = doc.new_page(width=1100, height=740)
                page.insert_text((70, 75), f"Circuit reading tools - page {number + 1}", fontsize=28)
                for row in range(5):
                    y = 135 + row * 110
                    page.draw_line((80, y), (1020, y), color=(0, 0, .65), width=2)
                    for col in range(6):
                        x = 150 + col * 155
                        page.draw_rect((x, y - 20, x + 55, y + 20), color=(.8, 0, 0),
                                       fill=(1, 1, .75), width=2)
                        page.insert_text((x, y - 30), f"R{row * 6 + col + 1}  2.4k", fontsize=12)
                note = page.add_text_annot((820, 80), "Check the resistor values")
                note.set_info(title="Review")
                note.update()
            doc.set_metadata({"title": "Circuit reading sample", "author": "Markdown Viewer"})
            doc.save(path)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        window = MainWindow()
        window.resize(1360, 900)
        window.show()
        window.open_path(str(path))
        view = window._pdf_view
        view.set_zoom_factor(2.0)

        def wait_until(predicate, timeout=10):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                QTest.qWait(20)
            raise AssertionError("Timed out waiting for PDF UI")

        wait_until(lambda: bool(view._cache))
        assert view.isVisible()
        view.set_hand_mode(True)
        view.horizontalScrollBar().setValue(200)
        view.verticalScrollBar().setValue(200)
        start, end = QPoint(300, 260), QPoint(230, 210)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end),
                            QPointF(view.viewport().mapToGlobal(end)),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        app.sendEvent(view.viewport(), event)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert view.horizontalScrollBar().value() == 270
        assert view.verticalScrollBar().value() == 250
        assert not view.has_selection()

        # Exercise an actual modal menu and actual mouse selection of an action.
        original_build = view._build_context_menu
        menu_evidence = []
        for theme in ("light", "dark"):
            window._theme_name = theme
            window._apply_theme()
            QTest.qWait(150)
            assert window.grab().save(str(args.output / f"pdf-tools-{theme}.png"))

            def capture_menu(pos, theme=theme):
                menu = original_build(pos)
                def choose_snapshot():
                    assert menu.isVisible()
                    assert menu.grab().save(str(args.output / f"pdf-menu-{theme}.png"))
                    target = next(a for a in menu.actions() if a.text().startswith("拍攝快照"))
                    menu_evidence.append([a.text() for a in menu.actions() if not a.isSeparator()])
                    QTest.mouseClick(menu, Qt.MouseButton.LeftButton,
                                     pos=menu.actionGeometry(target).center())
                QTimer.singleShot(80, choose_snapshot)
                return menu

            view._build_context_menu = capture_menu
            pos = QPoint(400, 300)
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos,
                                     view.viewport().mapToGlobal(pos))
            app.sendEvent(view.viewport(), event)
            assert view._snapshot_mode
            assert view.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(120, 100))
            end = QPoint(560, 390)
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end),
                                QPointF(view.viewport().mapToGlobal(end)),
                                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            app.sendEvent(view.viewport(), event)
            assert view._snapshot_band.isVisible()
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            snapshot = app.clipboard().pixmap()
            assert not snapshot.isNull()
            assert snapshot.save(str(args.output / f"pdf-snapshot-{theme}.png"))
            assert view.hand_mode()
            assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor

        view._build_context_menu = original_build
        menu = view._build_context_menu(QPoint(250, 200))
        next(a for a in menu.actions() if a.text().startswith("尋找文字")).trigger()
        assert window._search_bar.isVisible()
        assert "Circuit reading sample" in view.document_info()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        report = {
            "status": "passed", "platform": "Qt offscreen; real MainWindow and PdfView",
            "pan_scroll": [270, 250], "themes": ["light", "dark"],
            "menu_mouse_selection": "snapshot entered after menu closed",
            "snapshot": "clipboard image saved in both themes", "search": "visible",
            "source_pdf_unchanged": True, "menus": menu_evidence,
        }
        window.close()
        view._reset_render_pipeline(clear_cache=True)
        wait_until(lambda: not view._render_scheduler._sessions_by_generation)
        QThreadPool.globalInstance().waitForDone(10000)
        view._doc.close()
        window._renderer.setHtml("")
        window.deleteLater()
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    report["temporary_files_cleaned"] = True
    (args.output / "pdf-tools-smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
