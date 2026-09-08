import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton
from app import update_flow
from app.updater import UpdateInfo

app = QApplication.instance() or QApplication([])
window = QMainWindow()
window._update_check_thread = None
window._update_download_thread = None
window._update_progress = None
class FakeThread(QObject):
    finished_download = Signal(object, object)
    finished = Signal()
    def __init__(self, update, parent=None):
        super().__init__(parent)
    def start(self):
        pass
    def isRunning(self):
        return False
update = UpdateInfo(True, '1.0.0', '2.0.0', 'https://github.com/example/release')
with patch.object(update_flow, 'UpdateDownloadThread', FakeThread):
    update_flow.download_update(window, update)
    progress = window._update_progress
    result = {
        'window_modality': progress.windowModality().name,
        'minimum': progress.minimum(),
        'maximum': progress.maximum(),
        'visible_push_buttons': [button.text() for button in progress.findChildren(QPushButton) if button.isVisibleTo(progress)],
        'network_requested': False,
    }
    progress.close()
    window.close()
output = json.dumps(result, ensure_ascii=False, indent=2)
Path(__file__).with_name('reliability-update-progress-ui.json').write_text(output + '\n', encoding='utf-8')
print(output)
