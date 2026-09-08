"""Update-flow tests covering the download job, cancel/retry and install.

Everything here runs against fakes: no network, no modal dialog, and no real
installer launch. ``QProcess.startDetached`` and ``QApplication.quit`` are
always replaced so a failing test can never install anything or kill pytest.
"""

import sys
import threading
import time

import pytest
from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication, QWidget

from app import update_flow
from app.toolbar_utilities import (
    UPDATE_AVAILABLE,
    UPDATE_CANCELLED,
    UPDATE_CHECKING,
    UPDATE_DOWNLOADING,
    UPDATE_ERROR,
    UPDATE_IDLE,
    UPDATE_READY,
    UPDATE_VERIFYING,
    ToolbarUtilities,
)
from app.updater import UpdateCancelled, UpdateDigestUnavailable, UpdateInfo


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

    def showMessage(self, message, _timeout=0):  # noqa: N802 (Qt-compatible fake)
        self.message = message

    def clearMessage(self):  # noqa: N802 (Qt-compatible fake)
        self.message = ""


_LIVE_WINDOWS = []


@pytest.fixture(autouse=True)
def _destroy_test_windows(qapp):
    """Tear every fake window down for real after each test.

    These fakes live in the session-wide QApplication. Merely close()ing them
    leaves hidden top-level widgets, their event filters and their queued
    connections alive for the rest of the run, which was observed wedging the
    Qt event loop inside unrelated QtWebEngine tests much later on.
    """
    yield
    while _LIVE_WINDOWS:
        window = _LIVE_WINDOWS.pop()
        try:
            window.close()
            window.setParent(None)
            window.deleteLater()
        except RuntimeError:
            pass  # already destroyed by the test itself
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        _LIVE_WINDOWS.append(self)
        self.controls = ToolbarUtilities(current_version="1.25.0", parent=self)
        self._toolbar_utilities = self.controls
        self._status = _StatusBar()
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._update_cancel_event = None
        self._update_request_id = 0
        self._update_downloading_info = None
        self._update_ready_installer = None
        self._update_ready_digest = None
        self._update_close_pending = False
        self._update_close_deferred = False
        self._available_update = None
        self._cached_update_version = ""
        # Editing-protection doubles.
        self.confirm_result = True
        self.confirm_calls = 0
        self.snapshot_calls = []

    def statusBar(self):  # noqa: N802 (Qt-compatible fake)
        return self._status

    def _set_update_state(self, state, *, version=""):
        self.controls.set_update_state(state, version=version)

    def _confirm_close_all_edits(self):
        self.confirm_calls += 1
        return self.confirm_result

    def _request_live_wysiwyg_snapshot(self, continuation, *, purpose, on_failure=None):
        self.snapshot_calls.append(purpose)
        continuation()
        return True


class _FakeCheckThread:
    instances = []

    def __init__(self, _parent):
        self.finished_check = _Signal()
        self.finished = _Signal()
        self.cancel_event = threading.Event()
        self.running = False
        self.start_count = 0
        self.deleted = False
        self.__class__.instances.append(self)

    def cancel(self):
        self.cancel_event.set()

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

    def __init__(self, update, _parent, *, dest_dir=None):
        self.update = update
        self.dest_dir = dest_dir
        self.finished_download = _Signal()
        self.progress_changed = _Signal()
        self.verifying = _Signal()
        self.finished = _Signal()
        self.cancel_event = threading.Event()
        self.running = False
        self.deleted = False
        self.__class__.instances.append(self)

    def isRunning(self):  # noqa: N802 (Qt-compatible fake)
        return self.running

    def start(self):
        self.running = True

    def cancel(self):
        self.cancel_event.set()

    def deleteLater(self):  # noqa: N802 (Qt-compatible fake)
        self.deleted = True

    def complete(self, path=None, error=None):
        self.running = False
        self.finished_download.emit(path, error)
        self.finished.emit()


class _FakeProgress:
    instances = []

    def __init__(self, *_args):
        self.closed = False
        self.shown = False
        self.hidden = False
        self.maximum = 0
        self.value = 0
        self.label = ""
        self.modality = None
        self.canceled = _Signal()
        self.filters = []
        self.__class__.instances.append(self)

    def setWindowTitle(self, _title):  # noqa: N802
        pass

    def setWindowModality(self, modality):  # noqa: N802
        self.modality = modality

    def setAutoClose(self, _value):  # noqa: N802
        pass

    def setAutoReset(self, _value):  # noqa: N802
        pass

    def setMinimumDuration(self, _duration):  # noqa: N802
        pass

    def setMaximum(self, value):  # noqa: N802
        self.maximum = value

    def setValue(self, value):  # noqa: N802
        self.value = value

    def setLabelText(self, text):  # noqa: N802
        self.label = text

    def installEventFilter(self, obj):  # noqa: N802
        self.filters.append(obj)

    def show(self):
        self.shown = True
        self.hidden = False

    def hide(self):
        self.hidden = True

    def raise_(self):
        pass

    def activateWindow(self):  # noqa: N802
        pass

    def close(self):
        self.closed = True


def _settings_factory(tmp_path, monkeypatch):
    path = tmp_path / "update-settings.ini"

    def settings(*_args, **_kwargs):
        return QSettings(str(path), QSettings.Format.IniFormat)

    monkeypatch.setattr(update_flow, "QSettings", settings)
    return settings


_DIGEST = "sha256:" + ("a" * 64)


def _available_update(digest=_DIGEST):
    return UpdateInfo(
        True,
        "1.25.0",
        "1.26.0",
        "https://github.com/example/release",
        asset_name="MarkdownViewerSetup.exe",
        asset_url="https://github.com/example/MarkdownViewerSetup.exe",
        asset_digest=digest,
    )


def _patch_download_stack(monkeypatch):
    _FakeDownloadThread.instances.clear()
    _FakeProgress.instances.clear()
    monkeypatch.setattr(update_flow, "UpdateDownloadThread", _FakeDownloadThread)
    monkeypatch.setattr(update_flow, "QProgressDialog", _FakeProgress)


def _patch_launcher(monkeypatch, *, started=True):
    """Never launch anything or quit the test process.

    The module attributes are swapped rather than the real Qt classes: patching
    ``QApplication.quit`` itself would leave a Python shadow over a C++ slot and
    can disturb the shared qapp for every later test.
    """
    launches = []
    quits = []

    class _FakeProcess:
        @staticmethod
        def startDetached(*args):  # noqa: N802 (Qt-compatible fake)
            launches.append(args)
            return started

    class _FakeApp:
        @staticmethod
        def quit():
            quits.append(1)

        @staticmethod
        def instance():
            return QApplication.instance()

    monkeypatch.setattr(update_flow, "QProcess", _FakeProcess)
    monkeypatch.setattr(update_flow, "QApplication", _FakeApp)
    return launches, quits


def _ready(window, tmp_path, monkeypatch, *, valid=True):
    installer = tmp_path / "MarkdownViewerSetup.exe"
    installer.write_bytes(b"installer")
    window._update_ready_installer = installer
    window._update_ready_digest = _DIGEST
    monkeypatch.setattr(update_flow, "verify_installer", lambda *_a: valid)
    return installer


# --- check flow (unchanged behaviour) -----------------------------------


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


# --- download job -------------------------------------------------------


def test_download_is_non_modal_and_reports_percentage(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    window._available_update = update
    window._cached_update_version = update.latest_version
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        progress = window._update_progress
        assert window.controls.update_state == UPDATE_DOWNLOADING
        # The reader/editor stays usable: the toolbar entry point remains
        # clickable and the dialog is explicitly non-modal.
        assert window.controls.update_button.isEnabled() is True
        assert progress.shown is True
        assert progress.modality == update_flow.Qt.WindowModality.NonModal

        thread.progress_changed.emit(512, 2048)
        assert progress.maximum == 2048
        assert progress.value == 512
        assert "25%" in progress.label
        assert "完成" not in progress.label
    finally:
        window.close()


def test_download_without_content_length_stays_indeterminate(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        progress = window._update_progress
        thread.progress_changed.emit(4096, None)
        assert progress.maximum == 0          # indeterminate bar
        assert "%" not in progress.label
        assert "4.0 KB" in progress.label
    finally:
        window.close()


def test_verifying_stage_is_not_reported_as_finished(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    window._available_update = update
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        thread.progress_changed.emit(2048, 2048)
        thread.verifying.emit()
        assert window.controls.update_state == UPDATE_VERIFYING
        assert window._update_progress.label == "正在驗證下載內容…"
        assert window._update_ready_installer is None
    finally:
        window.close()


def test_second_download_press_reuses_the_running_job(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    window._available_update = update
    try:
        update_flow.download_update(window, update)
        progress = window._update_progress
        progress.hide()
        progress.shown = False

        update_flow.download_update(window, update)
        assert len(_FakeDownloadThread.instances) == 1
        # The hidden progress window comes back instead of a second download.
        assert progress.shown is True

        update_flow.on_update_button_clicked(window)
        assert len(_FakeDownloadThread.instances) == 1
    finally:
        window.close()


def test_stale_progress_and_result_are_ignored(qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    cleaned = []
    monkeypatch.setattr(update_flow, "cleanup_job_dir", cleaned.append)
    window = _Window()
    update = _available_update()
    window._available_update = update
    try:
        update_flow.download_update(window, update)
        stale = _FakeDownloadThread.instances[-1]
        update_flow.cancel_update_download(window)

        # Everything the abandoned worker emits afterwards must be dropped.
        stale.progress_changed.emit(999, 1000)
        stale.verifying.emit()
        stale.complete(tmp_path / "MarkdownViewerSetup.exe", None)

        assert window.controls.update_state == UPDATE_CANCELLED
        assert window._update_ready_installer is None
        assert cleaned == [tmp_path / "MarkdownViewerSetup.exe"]
    finally:
        window.close()


def test_cancel_sets_event_closes_progress_and_allows_retry(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    window._available_update = update
    window._cached_update_version = update.latest_version
    try:
        update_flow.download_update(window, update)
        first = _FakeDownloadThread.instances[-1]
        progress = window._update_progress

        started = time.monotonic()
        update_flow.cancel_update_download(window)
        ui_elapsed = time.monotonic() - started

        assert first.cancel_event.is_set() is True
        assert progress.closed is True
        assert window._update_progress is None
        assert window.controls.update_state == UPDATE_CANCELLED
        # The UI must not wait on the worker at all.
        assert ui_elapsed < 0.5

        first.running = False
        window._update_download_thread = None
        update_flow.retry_update_download(window)
        second = _FakeDownloadThread.instances[-1]
        assert second is not first
        assert len(_FakeDownloadThread.instances) == 2
        assert window.controls.update_state == UPDATE_DOWNLOADING
    finally:
        window.close()


def test_progress_cancel_button_stops_the_job(qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        window._update_progress.canceled.emit()
        assert thread.cancel_event.is_set() is True
        assert window.controls.update_state == UPDATE_CANCELLED
    finally:
        window.close()


def test_download_error_restores_available_badge_and_releases_thread(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
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

        thread.complete(None, RuntimeError("download failed"))
        assert thread.deleted is True
        assert window._update_download_thread is None
        assert progress.closed is True
        assert window._update_progress is None
        assert len(warnings) == 1
        assert window.controls.update_state == UPDATE_AVAILABLE
        assert window._update_ready_installer is None
    finally:
        window.close()


def test_cancelled_worker_error_does_not_warn(qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    warnings = []
    monkeypatch.setattr(
        update_flow.QMessageBox, "warning", lambda *a: warnings.append(a)
    )
    window = _Window()
    update = _available_update()
    window._available_update = update
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        thread.complete(None, UpdateCancelled("cancelled"))
        assert warnings == []
        assert window.controls.update_state == UPDATE_CANCELLED
    finally:
        window.close()


def test_missing_digest_offers_manual_release_download(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    questions = []
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "question",
        lambda *args: (
            questions.append(args)
            or update_flow.QMessageBox.StandardButton.Yes
        ),
    )
    opened = []
    monkeypatch.setattr(
        update_flow.QDesktopServices, "openUrl", staticmethod(opened.append)
    )
    window = _Window()
    update = _available_update(digest="sha256:not-a-hash")
    window._available_update = update
    window._cached_update_version = update.latest_version
    try:
        update_flow.download_update(window, update)
        # No worker, no "verified" claim: only the official page.
        assert _FakeDownloadThread.instances == []
        assert len(questions) == 1
        assert "無法驗證更新" in questions[0][1]
        assert opened[0].toString() == update.release_url
        assert window._update_ready_installer is None
        assert window.controls.update_state == UPDATE_AVAILABLE
    finally:
        window.close()


def test_worker_digest_error_also_offers_manual_download(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    monkeypatch.setattr(
        update_flow.QMessageBox,
        "question",
        lambda *_args: update_flow.QMessageBox.StandardButton.No,
    )
    window = _Window()
    update = _available_update()
    window._available_update = update
    window._cached_update_version = update.latest_version
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        thread.complete(
            None, UpdateDigestUnavailable("no digest", update.release_url)
        )
        assert window._update_ready_installer is None
        assert window.controls.update_state == UPDATE_AVAILABLE
    finally:
        window.close()


# --- ready / install lifecycle -----------------------------------------


def test_successful_download_becomes_ready_without_installing(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    asked = []
    monkeypatch.setattr(
        update_flow,
        "_ask_install_now",
        lambda _w, version: (asked.append(version) or False),
    )
    window = _Window()
    update = _available_update()
    window._available_update = update
    installer = tmp_path / "MarkdownViewerSetup.exe"
    installer.write_bytes(b"installer")
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        thread.complete(installer, None)

        assert asked == ["1.26.0"]
        assert launches == [] and quits == []
        assert window._update_ready_installer == installer
        assert window._update_ready_digest == _DIGEST
        assert window.controls.update_state == UPDATE_READY
        assert "已下載並驗證" in window.controls.update_button.toolTip()
    finally:
        window.close()


def test_later_keeps_file_for_this_session_and_installs_on_next_click(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    window = _Window()
    window._cached_update_version = "1.26.0"
    installer = _ready(window, tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(update_flow, "_ask_install_now", lambda *_a: False)
        update_flow.prompt_install_ready(window)
        assert launches == []
        assert window._update_ready_installer == installer
        assert installer.exists()
        assert window.controls.update_state == UPDATE_READY

        # A later click on the toolbar entry point installs the kept file
        # without re-downloading.
        monkeypatch.setattr(update_flow, "_ask_install_now", lambda *_a: True)
        update_flow.on_update_button_clicked(window)
        assert len(launches) == 1
        assert launches[0][0] == str(installer)
        assert quits == [1]
    finally:
        window.close()


def test_install_flushes_office_snapshot_before_saving(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    window = _Window()
    installer = _ready(window, tmp_path, monkeypatch)
    try:
        update_flow.install_ready_update(window)
        # The last Office keystroke is snapshotted before anything can quit.
        assert window.snapshot_calls == ["安裝更新"]
        assert window.confirm_calls == 1
        assert launches[0][0] == str(installer)
        assert quits == [1]
    finally:
        window.close()


def test_install_aborts_when_user_cancels_saving(qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    window = _Window()
    window._cached_update_version = "1.26.0"
    installer = _ready(window, tmp_path, monkeypatch)
    window.confirm_result = False
    try:
        update_flow.install_ready_update(window)
        assert launches == [] and quits == []
        # Nothing is discarded: the user can save and retry.
        assert window._update_ready_installer == installer
        assert window.controls.update_state == UPDATE_READY
    finally:
        window.close()


def test_install_refuses_a_missing_or_tampered_file(qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    warnings = []
    monkeypatch.setattr(
        update_flow.QMessageBox, "warning", lambda *a: warnings.append(a)
    )
    window = _Window()
    window._cached_update_version = "1.26.0"
    _ready(window, tmp_path, monkeypatch, valid=False)
    try:
        update_flow.install_ready_update(window)
        assert launches == [] and quits == []
        assert len(warnings) == 1
        assert window._update_ready_installer is None
        assert window.controls.update_state == UPDATE_AVAILABLE
    finally:
        window.close()


def test_launch_failure_keeps_app_usable_and_retryable(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch, started=False)
    warnings = []
    monkeypatch.setattr(
        update_flow.QMessageBox, "warning", lambda *a: warnings.append(a)
    )
    window = _Window()
    window._cached_update_version = "1.26.0"
    installer = _ready(window, tmp_path, monkeypatch)
    try:
        update_flow.install_ready_update(window)
        assert len(launches) == 1
        assert quits == []                      # the app stays alive
        assert len(warnings) == 1
        assert window._update_ready_installer == installer
        assert window.controls.update_state == UPDATE_READY

        _patch_launcher(monkeypatch, started=True)
        update_flow.install_ready_update(window)
        assert window.controls.update_state != UPDATE_ERROR
    finally:
        window.close()


# --- worker/window lifetime --------------------------------------------


def test_check_and_download_workers_are_mutually_exclusive(
    qapp, tmp_path, monkeypatch
):
    settings = _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
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


def test_closing_cancels_download_and_never_installs(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    monkeypatch.setattr(update_flow, "cleanup_job_dir", lambda *_a: None)
    window = _Window()
    update = _available_update()
    window._available_update = update
    installer = tmp_path / "MarkdownViewerSetup.exe"
    installer.write_bytes(b"installer")
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        event = QEvent(QEvent.Type.Close)

        deferred = update_flow.defer_close_until_updates_finish(window, event)
        assert deferred is True
        assert thread.cancel_event.is_set() is True
        assert window._update_close_pending is True
        assert window._update_progress is None

        # A worker callback that lands during shutdown must not install.
        # Only the result signal is emitted here: letting the fake worker also
        # report finished() would resolve the deferred close and post a real
        # QApplication.quit() into the shared test event loop.
        thread.running = False
        thread.finished_download.emit(installer, None)
        assert launches == [] and quits == []
    finally:
        window._update_close_pending = False
        window._update_close_deferred = False
        window.close()
        # Destroy the widget so the bounded close-fallback timer, which is
        # parented to it, cannot fire during a later test.
        window.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_closing_also_cancels_a_running_check(qapp, tmp_path, monkeypatch):
    """A slow check must not hold a hidden, closing window open either."""
    _settings_factory(tmp_path, monkeypatch)
    _FakeCheckThread.instances.clear()
    monkeypatch.setattr(update_flow, "UpdateCheckThread", _FakeCheckThread)
    window = _Window()
    try:
        update_flow.check_for_updates(window, manual=False)
        check = _FakeCheckThread.instances[-1]
        assert check.cancel_event.is_set() is False

        event = QEvent(QEvent.Type.Close)
        assert update_flow.defer_close_until_updates_finish(window, event) is True
        assert check.cancel_event.is_set() is True
    finally:
        window._update_close_pending = False
        window._update_close_deferred = False
        window._update_check_thread = None


def test_retry_during_worker_teardown_reports_instead_of_blocking(
    qapp, tmp_path, monkeypatch
):
    """A retry pressed mid-cancel must never wait on the worker in the GUI."""
    _settings_factory(tmp_path, monkeypatch)
    _patch_download_stack(monkeypatch)
    window = _Window()
    update = _available_update()
    window._available_update = update
    try:
        update_flow.download_update(window, update)
        thread = _FakeDownloadThread.instances[-1]
        update_flow.cancel_update_download(window)
        thread.running = True          # still unwinding

        def _must_not_block(_ms):
            raise AssertionError("download_update must not wait on the worker")

        thread.wait = _must_not_block
        started = time.monotonic()
        update_flow.download_update(window, update)
        assert time.monotonic() - started < 0.5
        assert len(_FakeDownloadThread.instances) == 1
        assert "請稍候再按一次重試" in window.statusBar().message
    finally:
        window._update_download_thread = None


def test_close_without_running_worker_is_not_deferred(
    qapp, tmp_path, monkeypatch
):
    _settings_factory(tmp_path, monkeypatch)
    window = _Window()
    try:
        event = QEvent(QEvent.Type.Close)
        assert update_flow.defer_close_until_updates_finish(window, event) is False
        assert window._update_close_pending is False
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

    def slow_check(*_args, **_kwargs):
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


def test_download_worker_throttles_progress_and_flags_verifying(qapp, tmp_path):
    """The production worker's callback: throttled signals, explicit verifying.

    The worker object is exercised without ``start()``: a real QThread left
    behind in the shared test QApplication was observed wedging the Qt event
    loop in unrelated tests later in the session. End-to-end streaming and
    cancellation are covered against a fake transport in tests/test_updater.py.
    """
    update = _available_update()
    worker = update_flow.UpdateDownloadThread(update, None, dest_dir=tmp_path)
    seen = []
    worker.progress_changed.connect(lambda d, t: seen.append((d, t)))
    verifying = []
    worker.verifying.connect(lambda: verifying.append(1))

    worker._on_progress(0, 1000)          # first callback always reports
    for step in range(1, 20):
        worker._on_progress(step * 10, 1000)   # a burst inside one interval
    assert seen == [(0, 1000)]
    assert verifying == []

    worker._last_emit -= update_flow.PROGRESS_INTERVAL_MS / 1000.0
    worker._on_progress(500, 1000)
    assert seen[-1] == (500, 1000)
    assert verifying == []                 # half way is not "verifying"

    # Reaching the advertised length always emits, throttle or not, and then
    # announces the hashing stage so the UI never shows a bare 100%.
    worker._on_progress(1000, 1000)
    assert seen[-1] == (1000, 1000)
    assert verifying == [1]

    assert worker.cancel_event.is_set() is False
    worker.cancel()
    assert worker.cancel_event.is_set() is True
    assert worker.isRunning() is False


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
        window._update_close_pending = False
        window.close()
