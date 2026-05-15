"""Entry point for Markdown Viewer."""

import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt
from app.window import MainWindow
from app.version import VERSION

# 圖示路徑：打包後從 _MEIPASS 找，開發時從專案目錄找
def _find_icon() -> QIcon:
    candidates = [
        Path(__file__).parent / "ICON" / "icon.ico",
    ]
    if hasattr(sys, "_MEIPASS"):
        candidates.insert(0, Path(sys._MEIPASS) / "ICON" / "icon.ico")
    for p in candidates:
        if p.exists():
            return QIcon(str(p))
    return QIcon()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Markdown Viewer")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("markdown-viewer")
    app.setWindowIcon(_find_icon())

    window = MainWindow()
    window.setWindowIcon(_find_icon())
    window.show()

    # Support: py main.py path/to/file.md
    if len(sys.argv) > 1:
        window.open_path(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
