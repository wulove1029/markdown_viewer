"""Update check and installer download flow delegated from MainWindow."""

import time
import weakref

from PySide6.QtCore import QProcess, QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
from shiboken6 import isValid

from .toolbar_utilities import (
    UPDATE_AVAILABLE,
    UPDATE_CHECKING,
    UPDATE_DOWNLOADING,
    UPDATE_ERROR,
    UPDATE_IDLE,
)
from .updater import UpdateInfo, check_for_update, download_installer
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


def _set_update_state(window, state: str, *, version: str = "") -> None:
    setter = getattr(window, "_set_update_state", None)
    if callable(setter):
        setter(state, version=version)


def _remember_available_update(
    window, update: UpdateInfo, *, update_ui: bool = True
) -> None:
    window._available_update = update
    window._cached_update_version = update.latest_version
    QSettings(_ORG, _APP).setValue(
        "available_update_version", update.latest_version
    )
    if update_ui:
        _set_update_state(
            window, UPDATE_AVAILABLE, version=update.latest_version
        )


def _clear_available_update(window) -> None:
    window._available_update = None
    window._cached_update_version = ""
    QSettings(_ORG, _APP).remove("available_update_version")


def _restore_available_or_error(window) -> None:
    version = str(getattr(window, "_cached_update_version", "") or "")
    if version:
        _set_update_state(window, UPDATE_AVAILABLE, version=version)
    else:
        _set_update_state(window, UPDATE_ERROR)


def _thread_is_running(thread) -> bool:
    if thread is None:
        return False
    try:
        return bool(thread.isRunning())
    except RuntimeError:
        return False


def _update_operation_in_progress(window) -> bool:
    return _thread_is_running(
        getattr(window, "_update_check_thread", None)
    ) or _thread_is_running(getattr(window, "_update_download_thread", None))


def _live_window(window_ref):
    window = window_ref()
    return window if window is not None and isValid(window) else None


def _dispatch_check_result(
    window_ref, request, update, error, manual: bool
) -> None:
    window = _live_window(window_ref)
    if window is not None:
        on_update_check_done(
            window, update, error, manual, request=request
        )


def _dispatch_download_result(window_ref, request, path, error) -> None:
    window = _live_window(window_ref)
    if window is not None:
        on_update_download_done(window, path, error, request=request)


def _resume_deferred_close(window_ref) -> None:
    window = _live_window(window_ref)
    if window is None or _update_operation_in_progress(window):
        return
    window._update_close_pending = False
    QTimer.singleShot(
        0, window, lambda: _finish_deferred_close(window_ref)
    )


def _finish_deferred_close(window_ref) -> None:
    window = _live_window(window_ref)
    if window is None:
        return
    app = QApplication.instance()
    window.close()
    other_visible_windows = any(
        widget is not window
        and widget.isVisible()
        and widget.inherits("QMainWindow")
        for widget in app.topLevelWidgets()
    ) if app is not None else False
    if (
        app is not None
        and app.quitOnLastWindowClosed()
        and not other_visible_windows
    ):
        app.quit()


def defer_close_until_updates_finish(window, event) -> bool:
    """Hide and retain a closing window until its updater workers finish."""
    threads = [
        thread
        for thread in (
            getattr(window, "_update_check_thread", None),
            getattr(window, "_update_download_thread", None),
        )
        if _thread_is_running(thread)
    ]
    if not threads:
        return False

    event.ignore()
    if getattr(window, "_update_close_pending", False):
        return True
    window._update_close_pending = True
    window.hide()
    progress = getattr(window, "_update_progress", None)
    if progress is not None:
        progress.hide()
    window_ref = weakref.ref(window)
    for thread in threads:
        thread.finished.connect(
            lambda window_ref=window_ref: _resume_deferred_close(window_ref)
        )
    # A worker can finish between the isRunning() snapshot and signal hookup.
    if not _update_operation_in_progress(window):
        QTimer.singleShot(
            0, window, lambda: _finish_deferred_close(window_ref)
        )
    return True


class UpdateCheckThread(QThread):
    finished_check = Signal(object, object)

    def run(self):
        try:
            self.finished_check.emit(check_for_update(), None)
        except Exception as exc:
            self.finished_check.emit(None, exc)


class UpdateDownloadThread(QThread):
    finished_download = Signal(object, object)

    def __init__(self, update: UpdateInfo, parent=None):
        super().__init__(parent)
        self._update = update

    def run(self):
        try:
            self.finished_download.emit(download_installer(self._update), None)
        except Exception as exc:
            self.finished_download.emit(None, exc)


def update_check_enabled() -> bool:
    value = QSettings(_ORG, _APP).value("update_check_enabled", True)
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("0", "false", "no", "off")


def check_updates_silent(window):
    # Privacy/perf: honour the opt-out and only phone home once a day.
    if not update_check_enabled():
        return
    if _update_operation_in_progress(window):
        return

    settings = QSettings(_ORG, _APP)
    try:
        last = float(settings.value("last_update_check", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    now = time.time()
    if now - last < 86400:
        return
    settings.setValue("last_update_check", now)
    check_for_updates(window, manual=False)


def check_for_updates(window, manual: bool):
    if _update_operation_in_progress(window):
        return

    _set_update_state(window, UPDATE_CHECKING)
    if manual:
        window.statusBar().showMessage("正在檢查更新...")

    thread = UpdateCheckThread(QApplication.instance() or window)
    window._update_check_thread = thread
    window_ref = weakref.ref(window)

    def dispatch_check_result(update, error, *, request=thread, is_manual=manual):
        _dispatch_check_result(
            window_ref, request, update, error, is_manual
        )

    thread.finished_check.connect(
        dispatch_check_result
    )
    thread.finished.connect(thread.deleteLater)
    thread.start()


def on_update_check_done(
    window, update, error, manual: bool, *, request=None
):
    if request is not None and window._update_check_thread is not request:
        return
    window._update_check_thread = None
    window.statusBar().clearMessage()
    if getattr(window, "_update_close_pending", False):
        if error:
            return
        if update.has_update:
            _remember_available_update(window, update, update_ui=False)
        else:
            _clear_available_update(window)
        return

    if error:
        _restore_available_or_error(window)
        if manual:
            QMessageBox.warning(window, "更新檢查失敗", str(error))
        return

    if not update.has_update:
        _clear_available_update(window)
        _set_update_state(window, UPDATE_IDLE)
        if manual:
            QMessageBox.information(
                window,
                "目前已是最新版本",
                f"Markdown Viewer 已是最新版本。\n目前版本：{VERSION}",
            )
        return

    _remember_available_update(window, update)
    if manual:
        prompt_for_update(window, update)


def prompt_for_update(window, update: UpdateInfo):
    answer = QMessageBox.question(
        window,
        "有可用更新",
        f"版本 {update.latest_version} 已可下載。\n\n"
        "是否要立即下載並安裝？",
    )
    if answer == QMessageBox.StandardButton.Yes:
        download_update(window, update)


def download_update(window, update: UpdateInfo):
    if _update_operation_in_progress(window):
        return

    _set_update_state(window, UPDATE_DOWNLOADING)
    window._update_progress = QProgressDialog("正在下載更新...", None, 0, 0, window)
    window._update_progress.setWindowTitle("Markdown Viewer 更新")
    window._update_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    window._update_progress.setMinimumDuration(0)
    window._update_progress.show()

    thread = UpdateDownloadThread(update, QApplication.instance() or window)
    window._update_download_thread = thread
    window_ref = weakref.ref(window)

    def dispatch_download_result(installer_path, error, *, request=thread):
        _dispatch_download_result(
            window_ref, request, installer_path, error
        )

    thread.finished_download.connect(dispatch_download_result)
    thread.finished.connect(thread.deleteLater)
    thread.start()


def on_update_download_done(window, installer_path, error, *, request=None):
    if request is not None and window._update_download_thread is not request:
        return
    window._update_download_thread = None
    if window._update_progress:
        window._update_progress.close()
        window._update_progress = None

    if error:
        if getattr(window, "_update_close_pending", False):
            return
        _restore_available_or_error(window)
        QMessageBox.warning(window, "更新下載失敗", str(error))
        return

    result = QProcess.startDetached(str(installer_path))
    started = result[0] if isinstance(result, tuple) else bool(result)
    if not started:
        if getattr(window, "_update_close_pending", False):
            return
        _restore_available_or_error(window)
        QMessageBox.warning(window, "更新失敗", "無法啟動安裝程式。")
        return

    if request is not None and request.isRunning():
        request.finished.connect(QApplication.quit)
    else:
        QApplication.quit()
