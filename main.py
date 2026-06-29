"""Entry point for Markdown Viewer."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import Qt, QStandardPaths
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app.version import VERSION
from app.window import MainWindow

log = logging.getLogger("markdown_viewer")


def _setup_logging() -> None:
    """Write a rotating log + capture unhandled exceptions for field triage."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    log_dir = Path(base or ".") / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "markdown-viewer.log",
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("markdown_viewer").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


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
    # Crisper rendering on Windows fractional display scaling (125%, 150%).
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Markdown Viewer")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("markdown-viewer")
    _setup_logging()
    log.info("Markdown Viewer %s starting", VERSION)
    app.setWindowIcon(_find_icon())

    window = MainWindow()
    window.setWindowIcon(_find_icon())
    window.show()

    # Support: py main.py path/to/file.md — otherwise reopen the last document.
    if len(sys.argv) > 1:
        window.open_path(sys.argv[1])
    else:
        window.restore_last_session()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
