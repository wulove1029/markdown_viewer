"""Check release metadata and updater/window tests without native QSettings."""

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.version import RELEASE_NOTES, VERSION

assert VERSION == "1.33.1"
assert len(RELEASE_NOTES) == 3
installer = (ROOT / "installer.iss").read_text(encoding="utf-8")
assert re.search(r'#define MyAppVersion "([^"]+)"', installer).group(1) == VERSION
changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M).group(1) == VERSION
report = {
    "version": VERSION,
    "release_notes": RELEASE_NOTES,
    "product_sha256": {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("app/session_state.py", "app/window.py", "app/pdf_view.py", "main.py")
    },
}

from PySide6 import QtCore

NativeSettings = QtCore.QSettings
with tempfile.TemporaryDirectory(prefix="mdv-release-tests-") as directory:
    class IsolatedSettings(NativeSettings):
        def __init__(self, *args, **kwargs):
            if len(args) >= 2 and isinstance(args[1], NativeSettings.Format):
                assert args[1] == NativeSettings.Format.IniFormat
                super().__init__(*args, **kwargs)
            else:
                super().__init__(str(Path(directory) / "settings.ini"), NativeSettings.Format.IniFormat)
            self.setFallbacksEnabled(False)
            assert self.format() == NativeSettings.Format.IniFormat

    QtCore.QSettings = IsolatedSettings
    import pytest

    result = pytest.main([
        "tests/test_updater.py", "tests/test_update_flow.py",
        "tests/test_window_integration.py", "-q", "-p", "no:cacheprovider",
    ])

report["tests_exit_code"] = int(result)
report["settings_isolation"] = "Explicit INI before application imports; native format rejected"
Path(__file__).with_suffix(".json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"Release {VERSION} metadata consistent; tests exit {result}")
sys.exit(result)
