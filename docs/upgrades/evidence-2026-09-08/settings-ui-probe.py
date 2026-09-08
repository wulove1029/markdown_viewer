"""Capture preferences without reading or writing personal preferences."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QTabWidget
from app import settings_dialog
from app.document_libraries import DocumentLibraryStore
from app.theme import LIGHT, DARK, app_stylesheet

app = QApplication([])
for name in ('msjh.ttc', 'segoeui.ttf'):
    QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts') / name))
with tempfile.TemporaryDirectory(prefix='mdv-settings-acceptance-') as directory:
    root = Path(directory)
    with patch.object(settings_dialog, 'QSettings', lambda *args: QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)), patch.object(settings_dialog, 'DocumentLibraryStore', lambda: DocumentLibraryStore(root / 'libraries.json')):
        for theme in (LIGHT, DARK):
            dialog = settings_dialog.SettingsDialog(current_theme=theme.name)
            dialog.setStyleSheet(app_stylesheet(theme))
            dialog.resize(680, 540)
            dialog.show()
            app.processEvents()
            tabs = dialog.findChild(QTabWidget)
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                app.processEvents()
                target = Path(__file__).parent / f'settings-{theme.name}-{index}.png'
                assert dialog.grab().save(str(target))
            dialog.close()
print('Captured all preferences tabs at 680 x 540 in light and dark themes.')
