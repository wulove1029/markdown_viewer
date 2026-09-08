"""Capture the real non-modal recovery inbox with isolated temporary data."""

import json
import os
from pathlib import Path
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMainWindow

from app.recovery import RecoveryStore
from app.recovery_browser import RecoveryBrowser
from app.theme import LIGHT, app_stylesheet


def main():
    app = QApplication.instance() or QApplication([])
    for name in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    with tempfile.TemporaryDirectory(prefix="mdv-recovery-ui-") as folder:
        root = Path(folder)
        window = QMainWindow()
        window._tab_state = {}
        window._recovery_store = RecoveryStore(root / "recovery")
        window._review_recovery_path = lambda _path: None
        source = root / "工作筆記.md"
        source.write_text("# 原檔內容", encoding="utf-8")
        for path, draft in (
            (source, "# 工作筆記\n\n待辦與想法仍保存在復原草稿，尚未覆寫原檔。\n\n- 整理本週資料\n- 確認下一步"),
            (root / "已刪除的文件.txt", "原檔已刪除，這份內容仍可以另存副本。"),
        ):
            window._recovery_store.save(
                path, draft, encoding="utf-8", newline="\n",
                cursor=0, anchor=0, scroll=0,
            )
        dialog = RecoveryBrowser(window)
        dialog.setStyleSheet(app_stylesheet(LIGHT))
        dialog.resize(820, 540)
        dialog.show()
        app.processEvents()
        dialog._list.setCurrentRow(1)
        app.processEvents()
        output = Path(__file__).parent
        assert dialog.grab().save(str(output / "recovery-inbox.png"))
        result = {
            "scope": "real Qt inbox, offscreen, isolated temporary snapshots",
            "size": [dialog.width(), dialog.height()],
            "minimum_hint": [dialog.minimumSizeHint().width(), dialog.minimumSizeHint().height()],
            "window_modality": dialog.windowModality().name,
            "draft_count": dialog._list.count(),
            "source_unchanged": source.read_text(encoding="utf-8") == "# 原檔內容",
            "missing_source_stays_missing": not (root / "已刪除的文件.txt").exists(),
        }
        (output / "recovery-ui.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        dialog.close()
        window.close()


if __name__ == "__main__":
    main()
