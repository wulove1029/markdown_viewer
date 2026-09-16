from pathlib import Path
import sys, tempfile
from PySide6 import QtCore
NativeSettings = QtCore.QSettings
native = NativeSettings('markdown-viewer', 'MarkdownViewer')
keys = ('open_tabs', 'active_tab', 'active_tab_path', 'last_file', 'pdf_last_pages')
before = {key: native.value(key) for key in keys}
with tempfile.TemporaryDirectory(prefix='mdv-pytest-settings-') as directory:
    class IsolatedSettings(NativeSettings):
        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and isinstance(args[1], NativeSettings.Format):
                super().__init__(*args, **kwargs)
            else:
                super().__init__(str(Path(directory) / 'settings.ini'), NativeSettings.Format.IniFormat)
            self.setFallbacksEnabled(False)
    QtCore.QSettings = IsolatedSettings
    import pytest
    result = pytest.main(['tests', '-q', '-p', 'no:cacheprovider'])
native.sync()
assert before == {key: native.value(key) for key in keys}, 'Native session changed during tests'
print('Native session unchanged after full regression')
sys.exit(result)
