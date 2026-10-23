"""Isolated UI smoke/capture: never restores or writes the user's session."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--width", type=int, default=1360)
    parser.add_argument("--empty", action="store_true")
    parser.add_argument("--popup", action="store_true")
    parser.add_argument("--mode", choices=("reading", "source", "split", "office", "focus", "tools"), default="reading")
    args = parser.parse_args()
    def timed_out():
        import faulthandler
        faulthandler.dump_traceback(all_threads=True)
        os._exit(124)
    watchdog = threading.Timer(30, timed_out)
    watchdog.daemon = True
    watchdog.start()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mdv-ui-") as folder:
        from tools.isolated_runtime import configure_webengine, install

        runtime = install(folder, application="MDVWorkspacePreview")
        from PySide6.QtCore import QSettings

        settings = QSettings("markdown-viewer", "MarkdownViewer")
        runtime.assert_settings(settings)
        settings.setValue("theme", args.theme)
        settings.setValue("update_check_enabled", False)
        settings.sync()
        from app.md_converter import convert
        from app.window import MainWindow
        app = QApplication([])
        configure_webengine(runtime)
        for font in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
            font_path = Path("C:/Windows/Fonts") / font
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        app.setApplicationName("MarkdownViewerPreview")
        window = MainWindow()
        window._theme_name = args.theme
        window._apply_theme()
        window.resize(args.width, 900)
        path = Path(folder) / "閱讀工作台.md"
        path.write_text("""# 把注意力留給內容

閱讀、整理與寫作，都從一份清楚的文件開始。

## 今天的工作

這是一個安靜的文件工作台。左側整理文件，中央保留舒適的閱讀空間；需要編輯時，從上方切換 **Markdown、並排預覽或 Office**。

> 好的閱讀體驗，是每次回來都能接續原本的思路。

### 升級重點

| 項目 | 改善方向 |
| --- | --- |
| 開啟文件 | 減少重複載入，保留常用文件快取 |
| 操作回饋 | 清楚的模式切換，穩定的閱讀位置 |
| 視覺層次 | 柔和底色、舒適行距、克制的工具列 |

- [x] 閱讀優先的版面
- [x] 亮色與暗色主題
- [ ] 整理下一份文件

## 程式碼與筆記

```python
def read_document(path):
    return path.read_text(encoding="utf-8")
```
""", encoding="utf-8")
        window.show()
        def report_modal():
            modal = QApplication.activeModalWidget()
            if modal is not None:
                print(f"Capture blocked by {type(modal).__name__}: {modal.windowTitle()}", flush=True)
        QTimer.singleShot(7000, report_modal)
        if not args.empty:
            window.open_path(str(path))
            if args.mode in {"source", "split"}:
                window._open_source_editor("edit" if args.mode == "source" else "split")
            elif args.mode == "office":
                window._open_office_editor()
            elif args.mode == "focus":
                window._workspace_controls.toggle_focus()
            elif args.mode == "tools":
                window._reading_tools.dock.show()
        def capture():
            target = args.output / f"workspace-{args.theme}.png"
            assert window._workspace_controls.mode.isVisible() or args.mode == "focus"
            assert window._tab_bar.count() == (0 if args.empty else 1)
            assert window._workspace_controls.mode.geometry().right() < window._toolbar.width()
            assert window.grab().save(str(target))
            if args.popup:
                popup = window._workspace_controls.mode.view().window()
                assert popup.isVisible()
                assert popup.grab().save(str(args.output / f"mode-popup-{args.theme}.png"))
                window._workspace_controls.mode.hidePopup()
            html, _ = convert(path, args.theme)
            (args.output / f"reading-{args.theme}.html").write_text(html, encoding="utf-8")
            print(target.resolve())
            if args.empty:
                def click_recent(point):
                    if isinstance(point, str):
                        import json
                        x, y = json.loads(point)
                        QTest.mouseClick(window._renderer.focusProxy(), Qt.MouseButton.LeftButton,
                                         pos=QPoint(round(x), round(y)))
                window._renderer.page().runJavaScript(
                    "(function(){var r=document.querySelector('a[href=\"https://markdown-viewer.invalid/home/recent\"]').getBoundingClientRect();"
                    "return JSON.stringify([r.x+r.width/2,r.y+r.height/2]);})()", click_recent)
                def verify_home():
                    assert window._panel._tabs.currentIndex() == 1
                    print("Home recent-files navigation passed")
                    app.quit()
                QTimer.singleShot(300, verify_home)
            else:
                if args.mode == "office":
                    # Capture is not a close/save lifecycle test. Office may
                    # normalize the disposable fixture and ask to save on
                    # app.quit(); native offscreen destruction can also crash.
                    # Leave only this isolated temporary directory behind.
                    sys.stdout.flush()
                    watchdog.cancel()
                    os._exit(0)
                app.quit()
        if args.popup:
            QTimer.singleShot(3000, window._workspace_controls.mode.showPopup)
        QTimer.singleShot(3500, capture)
        app.exec()
        window._renderer.setHtml("")
        window.deleteLater()
        app.processEvents()
    # WebEngine offscreen teardown can wait for Chromium indefinitely on Windows.
    # Artifacts and temporary settings are already flushed/removed above.
    sys.stdout.flush()
    watchdog.cancel()
    os._exit(0)


if __name__ == "__main__":
    main()
