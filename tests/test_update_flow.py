"""Update-flow tests covering toolbar state without network or modal UI."""

import sys
import time

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication, QWidget

from app import update_flow
from app.toolbar_utilities import (
    UPDATE_AVAILABLE,
    UPDATE_CHECKING,
    UPDATE_DOWNLOADING,
    UPDATE_ERROR,
    UPDATE_IDLE,
    ToolbarUtilities,
)
from app.updater import UpdateInfo


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _StatusBar:
    def __init__(self):
        self.message = ""

    def showMessage(self, message):  # noqa: N802 (Qt-compatible fake)
        self.message = message

    def clearMessage(self):  # noqa: N802 (Qt-compatible fake)
        self.message = ""


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.controls = ToolbarUtilities(current_version="1.25.0", parent=self)
        self._status = _StatusBar()
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._available_update = None
        self._cached_update_version = ""

    def statusBar(self):  # noqa: N802 (Qt-compatible fake)
        return self._status

    def _set_update_state(self, state, *, version=""):
        self.controls.set_update_state(state, version=version)


class _FakeCheckThread:
    instances = []

    def __init__(self, _parent):
        self.finished_check = _Signal()
        self.finished = _Signal()
        self.running = False
        self.start_count = 0
        self.deleted = False
        self.__class__.instances.append(self)

    def isRunning(self):  # noqa: N802 (Qt-compatible fake)
        return self.running

    def start(self):
        self.start_count += 1
        self.running = True

    def deleteLater(self):  # noqa: N802 (Qt-compatible fake)
        self.deleted = True

    def complete(self, update=None, error=None):
        self.running = False
        self.finished_check.emit(update, error)
        self.finished.emit()


class _FakeDownloadThread:
    instances = []

    def __init__(self, update, _parent):
        self.update = update
        self.finished_download = _Signal()
        self.finished = _Signal()
        self.running = False
        self.deleted = False
        self.__class__.instances.append(self)

    def isRunning(self):  # noqa: N802 (Qt-compatible fake)
        return self.running

    def start(self):
        self.running = True

    def deleteLater(self):  # noqa: N802 (Qt-compatible fake)
        self.deleted = True

    def complete(self, path=None, error=None):
        self.running = False
        self.finished_download.emit(path, error)
        self.finished.emit()


class _FakeProgress:
    def __init__(self, *_args):
        self.closed = False
        self.shown = False

    def setWindowTitle(self, _title):  # noqa: N802
        pass

    def setWindowModality(self, _modality):  # noqa: N802
        pass

    def setMinimumDuration(self, _duration):  # noqa: N802
        pass

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True


def _settings_factory(tmp_path, monkeypatch):
    path = tmp_path / "update-settings.ini"

    def settings(*_args, **_kwargs):
        return QSettings(str(path), QSettings.Format.IniFormat)

    monkeypatch.setattr(update_flow, "QSettings", settings)
    return settings


def _available_update():
    return UpdateInfo(
        True,
        "1.25.0",
        "1.26.0",
        "https://github.com/example/release",
        asset_name="MarkdownViewerSetup.exe",
        asset_url="https://github.com/example/MarkdownViewerSetup.exe",
    )


def test_manual_check_enters_busy_once_then_returns_idle(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _FakeCheckThread.instances.clear()
    monkeypatch.setattr(update_flow, "UpdateCheckThread", _FakeCheckThread)
    information = []
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "information",
        lambda *args: information.append(args),
    )
    window = _Window()
    try:
        update_flow.check_for_updates(window, manual=True)
        thread = _FakeCheckThread.instances[-1]
        assert window.controls.update_state == UPDATE_CHECKING
        assert window.controls.update_button.isEnabled() is False
        assert window.statusBar().message == "正在檢查更新..."
        assert thread.start_count == 1

        update_flow.check_for_updates(window, manual=True)
        assert len(_FakeCheckThread.instances) == 1
        assert thread.start_count == 1

        thread.complete(UpdateInfo(False, "1.25.0", "1.25.0", ""), None)
        assert window._update_check_thread is None
        assert window.controls.update_state == UPDATE_IDLE
        assert window.controls.update_button.isEnabled() is True
        assert window.statusBar().message == ""
        assert len(information) == 1
        assert thread.deleted is True
    finally:
        window.close()


def test_silent_available_update_sets_badge_without_modal(
    qapp, tmp_path, monkeypatch
):
    settings = _settings_factory(tmp_path, monkeypatch)
    questions = []
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "question",
        lambda *args: questions.append(args),
    )
    window = _Window()
    update = _available_update()
    try:
        update_flow.on_update_check_done(window, update, None, manual=False)

        assert questions == []
        assert window._available_update is update
        assert window._cached_update_version == "1.26.0"
        assert window.controls.update_state == UPDATE_AVAILABLE
        assert window.controls.update_button.property("badgeVisible") is True
        assert "v1.26.0" in window.controls.update_button.toolTip()
        assert settings().value("available_update_version") == "1.26.0"
    finally:
        window.close()


def test_manual_available_prompts_once_and_no_keeps_badge(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    questions = []
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "question",
        lambda *args: (
            questions.append(args)
            or update_flow.QMessageBox.StandardButton.No
        ),
    )
    window = _Window()
    try:
        update_flow.on_update_check_done(
            window, _available_update(), None, manual=True
        )
        assert len(questions) == 1
        assert window.controls.update_state == UPDATE_AVAILABLE
        assert window.controls.update_button.property("badgeVisible") is True
    finally:
        window.close()


def test_check_error_restores_retry_or_existing_available_state(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    warnings = []
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    window = _Window()
    try:
        update_flow.on_update_check_done(
            window, None, RuntimeError("offline"), manual=False
        )
        assert warnings == []
        assert window.controls.update_state == UPDATE_ERROR
        assert window.controls.update_button.isEnabled() is True

        window._cached_update_version = "1.26.0"
        update_flow.on_update_check_done(
            window, None, RuntimeError("offline"), manual=True
        )
        assert len(warnings) == 1
        assert window.controls.update_state == UPDATE_AVAILABLE
        assert "v1.26.0" in window.controls.update_button.toolTip()
    finally:
        window.close()


def test_download_error_restores_available_badge_and_releases_thread(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _FakeDownloadThread.instances.clear()
    monkeypatch.setattr(
        update_flow, "UpdateDownloadThread", _FakeDownloadThread
    )
    monkeypatch.setattr(update_flow, "QProgressDialog", _FakeProgress)
    warnings = []
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    window = _Window()
    update = _available_update()
    window._available_update = update
    window._cached_update_version = update.latest_version
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        progress = window._update_progress
        assert window.controls.update_state == UPDATE_DOWNLOADING
        assert window.controls.update_button.isEnabled() is False
        assert progress.shown is True

        thread.complete(None, RuntimeError("download failed"))
        assert thread.deleted is True
        assert window._update_download_thread is None
        assert progress.closed is True
        assert window._update_progress is None
        assert len(warnings) == 1
        assert window.controls.update_state == UPDATE_AVAILABLE
        assert window.controls.update_button.isEnabled() is True
    finally:
        window.close()


def test_check_and_download_workers_are_mutually_exclusive(
    qapp, tmp_path, monkeypatch
):
    settings = _settings_factory(tmp_path, monkeypatch)
    update = _available_update()
    window = _Window()
    try:
        download = _FakeDownloadThread(update, window)
        download.start()
        window._update_download_thread = download
        window.controls.set_update_state(UPDATE_DOWNLOADING)
        monkeypatch.setattr(
            update_flow,
            "UpdateCheckThread",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("check worker must not be created")
            ),
        )

        update_flow.check_updates_silent(window)
        update_flow.check_for_updates(window, manual=True)
        assert window.controls.update_state == UPDATE_DOWNLOADING
        assert settings().contains("last_update_check") is False

        download.running = False
        window._update_download_thread = None
        check = _FakeCheckThread(window)
        check.start()
        window._update_check_thread = check
        window.controls.set_update_state(UPDATE_CHECKING)
        monkeypatch.setattr(
            update_flow,
            "QProgressDialog",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("download progress must not be created")
            ),
        )

        update_flow.download_update(window, update)
        assert window.controls.update_state == UPDATE_CHECKING
        assert window._update_progress is None
    finally:
        window.close()


def test_running_check_survives_direct_window_deletion(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    callback_errors = []
    monkeypatch.setattr(
        sys, "excepthook", lambda *error: callback_errors.append(error)
    )

    def slow_check():
        time.sleep(0.05)
        return UpdateInfo(False, "1.25.0", "1.25.0", "")

    monkeypatch.setattr(update_flow, "check_for_update", slow_check)
    window = _Window()
    update_flow.check_for_updates(window, manual=False)
    thread = window._update_check_thread

    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert thread.wait(1000) is True
    qapp.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert callback_errors == []


def test_deferred_close_still_clears_stale_cached_badge(
    qapp, tmp_path, monkeypatch
):
    settings = _settings_factory(tmp_path, monkeypatch)
    settings().setValue("available_update_version", "1.26.0")
    window = _Window()
    try:
        window._cached_update_version = "1.26.0"
        window.controls.set_update_state(UPDATE_CHECKING)
        window._update_close_pending = True

        update_flow.on_update_check_done(
            window,
            UpdateInfo(False, "1.25.0", "1.25.0", ""),
            None,
            manual=False,
        )

        assert window._cached_update_version == ""
        assert window._available_update is None
        assert settings().contains("available_update_version") is False
        # The hidden window is closing, so no redundant repaint is needed.
        assert window.controls.update_state == UPDATE_CHECKING
    finally:
        window.close()
