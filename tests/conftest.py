import os

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
