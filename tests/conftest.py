import os
import sys

if sys.platform == "win32":
    import ctypes

    # A native crash (e.g. inside QtWebEngine) must end the test process at once instead of
    # opening a blocking "application error" dialog that stalls unattended runs. pytest's
    # faulthandler still prints the Python traceback; child processes inherit the mode.
    # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
    _kernel32 = ctypes.windll.kernel32
    _kernel32.SetErrorMode(_kernel32.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def webengine_gui_thread_lifetime(request, monkeypatch):
    """Destroy browser test views on the GUI thread, never a parser's GC pass."""
    if os.environ.get("RUN_WEBENGINE_TESTS") != "1":
        yield
        return

    import gc

    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from shiboken6 import isValid

    app = request.getfixturevalue("qapp")
    views = []
    original = QWebEngineView.__init__

    def retain_view(self, *args, **kwargs):
        original(self, *args, **kwargs)
        views.append(self)

    monkeypatch.setattr(QWebEngineView, "__init__", retain_view)
    yield
    # Render workers must finish before pages are destroyed. Holding references
    # prevents cyclic GC in those workers from deleting a previous test's page.
    assert QThreadPool.globalInstance().waitForDone(15000), "Render worker did not stop"
    app.processEvents()
    for view in views:
        if isValid(view):
            view.stop()
            view.close()
            view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    views.clear()
    gc.collect()


@pytest.fixture(autouse=True)
def isolated_relocation_journals(tmp_path, monkeypatch):
    from app import relocation_journal
    monkeypatch.setattr(relocation_journal, "default_journal_dir",
                        lambda: tmp_path.parent / (tmp_path.name + "-journals"))


@pytest.fixture(autouse=True)
def isolated_aiwatch_home(tmp_path, monkeypatch):
    """Never read the real ~/.aiwatch (no test may find a real install.json)."""
    monkeypatch.setenv("AIWATCH_HOME", str(tmp_path.parent / (tmp_path.name + "-aiwatch")))
    # ... nor a Watchdog copy: neither one a developer pointed the application
    # at nor the one tracked in the repository.  Tests that need a bundled copy
    # point MDV_AGENT_WATCHDOG_DIR (or the repository root) at their own.
    monkeypatch.setenv("MDV_AGENT_WATCHDOG_DIR",
                       str(tmp_path.parent / (tmp_path.name + "-no-agent-bundle")))
