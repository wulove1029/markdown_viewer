"""Isolated preferences geometry probe; never accepts or saves settings."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea, QTabWidget
from app import settings_dialog
from app.theme import LIGHT, app_stylesheet


def main():
    output = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="mdv-settings-audit-") as folder:
        settings_dialog.QSettings = lambda *a, **kw: QSettings(
            str(Path(folder) / "settings.ini"), QSettings.Format.IniFormat
        )

        class EmptyLibraries:
            def load(self):
                return []

        settings_dialog.DocumentLibraryStore = EmptyLibraries
        app = QApplication([])
        for font in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
            font_path = Path("C:/Windows/Fonts") / font
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        dialog = settings_dialog.SettingsDialog()
        dialog.setStyleSheet(app_stylesheet(LIGHT))
        dialog.resize(800, 540)
        dialog.show()
        app.processEvents()
        tabs = dialog.findChild(QTabWidget)
        tabs.setCurrentIndex(2)
        app.processEvents()
        buttons = dialog.findChild(QDialogButtonBox)
        result = {
            "scope": "offscreen logical geometry; not a physical display test",
            "requested_size": [800, 540],
            "actual_size": [dialog.width(), dialog.height()],
            "minimum_size_hint": [dialog.minimumSizeHint().width(), dialog.minimumSizeHint().height()],
            "button_box_bottom": buttons.geometry().bottom(),
            "scroll_area_count": len(dialog.findChildren(QScrollArea)),
            "logical_viewport_examples": {"1366x768_at_100_percent": 768,
                                          "1920x1080_at_150_percent": 720},
        }
        assert dialog.grab().save(str(output / "settings-behavior.png"))
        (output / "settings-geometry.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        dialog.reject()


if __name__ == "__main__":
    main()
