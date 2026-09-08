"""Update check, streaming download and install flow delegated from MainWindow.

The download is a *job*: one worker thread, one cancel event, one private temp
directory and one monotonically increasing request id. Every result that comes
back from a worker is matched against the window's current request id, so a
retry or a cancel can never be overwritten by the job it replaced.
"""

from pathlib import Path
import threading
import time
import weakref

from PySide6.QtCore import (
    QEvent,
    QObject,
    QProcess,
    QSettings,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
from shiboken6 import isValid

from .toolbar_utilities import (
    UPDATE_AVAILABLE,
    UPDATE_CANCELLED,
    UPDATE_CHECKING,
    UPDATE_DOWNLOADING,
    UPDATE_ERROR,
    UPDATE_IDLE,
    UPDATE_LAUNCHING,
    UPDATE_READY,
    UPDATE_VERIFYING,
)
from .updater import (
    UpdateCancelled,
    UpdateDigestUnavailable,
    UpdateInfo,
    check_for_update,
    cleanup_job_dir,
    download_installer,
    has_verifiable_digest,
    verify_installer,
)
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"

#: Progress signals are throttled to this interval so a fast local transfer
#: cannot flood the GUI thread with repaint work.
PROGRESS_INTERVAL_MS = 150


# --- small helpers ------------------------------------------------------


def _set_update_state(window, state: str, *, version: str = "") -> None:
    setter = getattr(window, "_set_update_state", None)
    if callable(setter):
        setter(state, version=version)


def _update_version(window) -> str:
    update = getattr(window, "_available_update", None)
    if update is not None:
        return update.latest_version
    return str(getattr(window, "_cached_update_version", "") or "")


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


def _restore_idle_state(window) -> None:
    """Return to a resting state that still reflects a pending update."""
    if getattr(window, "_update_ready_installer", None):
        _set_update_state(window, UPDATE_READY, version=_update_version(window))
        return
    _restore_available_or_error(window)


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


def _closing(window) -> bool:
    return bool(getattr(window, "_update_close_pending", False))


def _next_request_id(window) -> int:
    request_id = int(getattr(window, "_update_request_id", 0)) + 1
    window._update_request_id = request_id
    return request_id


def _is_current_request(window, request_id) -> bool:
    """Reject progress/results belonging to a cancelled or superseded job."""
    if request_id is None:
        return True
    return int(getattr(window, "_update_request_id", 0)) == int(request_id)


# --- deferred close -----------------------------------------------------


def _dispatch_check_result(
    window_ref, request, update, error, manual: bool
) -> None:
    window = _live_window(window_ref)
    if window is not None:
        on_update_check_done(
            window, update, error, manual, request=request
        )


def _dispatch_download_result(window_ref, request_id, path, error) -> None:
    window = _live_window(window_ref)
    if window is not None:
        on_update_download_done(window, path, error, request=request_id)


def _dispatch_download_progress(window_ref, request_id, downloaded, total):
    window = _live_window(window_ref)
    if window is not None:
        on_update_download_progress(
            window, downloaded, total, request=request_id
        )


def _dispatch_download_verifying(window_ref, request_id):
    window = _live_window(window_ref)
    if window is not None:
        on_update_download_verifying(window, request=request_id)


def _resume_deferred_close(window_ref) -> None:
    window = _live_window(window_ref)
    if window is None or not getattr(window, "_update_close_deferred", False):
        # The close already completed (or was never deferred). Never let a
        # late worker signal or the bounded fallback timer quit the app on its
        # own long after the user's close was resolved.
        return
    if _update_operation_in_progress(window):
        return
    window._update_close_pending = False
    window._update_close_deferred = False
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
    """Hide and retain a closing window until its updater workers finish.

    The download is cancelled first so the wait is bounded: a slow server must
    never keep an invisible window (and the process) alive indefinitely. The
    QThread itself is never destroyed while running.
    """
    window._update_close_pending = True
    cancel_update_download(window, quiet=True)
    check = getattr(window, "_update_check_thread", None)
    canceller = getattr(check, "cancel", None)
    if callable(canceller):
        canceller()

    threads = [
        thread
        for thread in (
            getattr(window, "_update_check_thread", None),
            getattr(window, "_update_download_thread", None),
        )
        if _thread_is_running(thread)
    ]
    if not threads:
        window._update_close_pending = False
        return False

    event.ignore()
    if getattr(window, "_update_close_deferred", False):
        return True
    window._update_close_deferred = True
    window.hide()
    _close_progress(window)
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
    # No watchdog timer here on purpose: a stray singleShot aimed at a window
    # that is about to be destroyed was observed wedging the Qt event loop
    # seconds later in a completely unrelated part of the app.
    #
    # What bounds this wait is the cancellation above. Once bytes are flowing,
    # cancelling closes the socket and the worker unwinds in milliseconds. The
    # residual worst case is a cancel that lands while a connect is still
    # outstanding: that is capped by updater.CHECK_TIMEOUT (15 s) and
    # updater.DOWNLOAD_TIMEOUT (20 s), so 20 s is the honest upper bound -- not
    # the 5 s an earlier draft claimed. The one leg neither timeout covers is
    # the OS DNS lookup inside getaddrinfo. The QThread is never destroyed
    # while running; it lives until its finished signal fires.
    return True


# --- workers ------------------------------------------------------------


class UpdateCheckThread(QThread):
    finished_check = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self):
        try:
            self.finished_check.emit(
                check_for_update(cancel=self.cancel_event), None
            )
        except Exception as exc:
            self.finished_check.emit(None, exc)


class UpdateDownloadThread(QThread):
    """Streams one installer, reporting throttled progress via signals."""

    finished_download = Signal(object, object)
    progress_changed = Signal(object, object)
    verifying = Signal()

    def __init__(self, update: UpdateInfo, parent=None, *, dest_dir=None):
        super().__init__(parent)
        self._update = update
        self._dest_dir = dest_dir
        self.cancel_event = threading.Event()
        self._last_emit = 0.0

    def cancel(self) -> None:
        self.cancel_event.set()

    def _on_progress(self, downloaded, total) -> None:
        now = time.monotonic()
        complete = total is not None and downloaded >= total
        if (
            downloaded
            and not complete
            and (now - self._last_emit) * 1000.0 < PROGRESS_INTERVAL_MS
        ):
            return
        self._last_emit = now
        self.progress_changed.emit(downloaded, total)
        if complete:
            # Hashing a large file is not instant; say so rather than parking
            # the bar at 100% while the transfer is still unverified.
            self.verifying.emit()

    def run(self):
        try:
            path = download_installer(
                self._update,
                progress=self._on_progress,
                cancel=self.cancel_event,
                dest_dir=self._dest_dir,
            )
            self.finished_download.emit(path, None)
        except Exception as exc:
            self.finished_download.emit(None, exc)


# --- check flow ---------------------------------------------------------


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
    if _closing(window):
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
    if not has_verifiable_digest(update):
        _prompt_manual_download(window, update)
        return
    answer = QMessageBox.question(
        window,
        "有可用更新",
        f"版本 {update.latest_version} 已可下載。\n\n"
        "要現在下載嗎？下載期間可以繼續閱讀與編輯，\n"
        "下載並驗證完成後再由你決定何時安裝。",
    )
    if answer == QMessageBox.StandardButton.Yes:
        download_update(window, update)


def _prompt_manual_download(window, update: UpdateInfo, message: str = "") -> None:
    """No trustworthy digest: send the user to the official release page."""
    release_url = str(getattr(update, "release_url", "") or "")
    detail = message or (
        "這個版本沒有提供可驗證的 SHA-256 雜湊，\n"
        "為了安全起見不會在應用程式內自動下載安裝。"
    )
    text = f"{detail}\n\n要開啟官方 Release 頁面手動下載嗎？"
    if not release_url:
        QMessageBox.warning(window, "無法驗證更新", detail)
        _restore_idle_state(window)
        return
    answer = QMessageBox.question(window, "無法驗證更新", text)
    if answer == QMessageBox.StandardButton.Yes:
        QDesktopServices.openUrl(QUrl(release_url))
    _restore_idle_state(window)


# --- download flow ------------------------------------------------------


class _HideOnCloseFilter(QObject):
    """Let the user tuck the progress window away without cancelling.

    QProgressDialog maps both the title-bar close button and Esc onto
    ``cancel()``. The download must survive those gestures: only the explicit
    cancel button stops the job.
    """

    def eventFilter(self, obj, event):  # noqa: N802 (Qt signature)
        kind = event.type()
        if kind == QEvent.Type.Close:
            event.ignore()
            obj.hide()
            return True
        if (
            kind == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            obj.hide()
            return True
        return False


def _close_progress(window) -> None:
    progress = getattr(window, "_update_progress", None)
    window._update_progress = None
    window._update_progress_filter = None
    if progress is None:
        return
    try:
        progress.close()
    except RuntimeError:
        pass


def _create_progress(window, request_id: int):
    progress = QProgressDialog("正在下載更新…", "取消下載", 0, 0, window)
    progress.setWindowTitle("Markdown Viewer 更新")
    # Non-modal: reading, editing and tab switching stay available while the
    # installer downloads in the background.
    progress.setWindowModality(Qt.WindowModality.NonModal)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setMinimumDuration(0)
    window_ref = weakref.ref(window)

    def _on_cancel(*, request=request_id):
        target = _live_window(window_ref)
        if target is not None and _is_current_request(target, request):
            cancel_update_download(target)

    canceled = getattr(progress, "canceled", None)
    if canceled is not None:
        canceled.connect(_on_cancel)
    installer = getattr(progress, "installEventFilter", None)
    if callable(installer):
        window._update_progress_filter = _HideOnCloseFilter(window)
        installer(window._update_progress_filter)
    progress.show()
    return progress


def show_update_progress(window) -> bool:
    """Re-show a progress window the user hid. Returns whether one exists."""
    progress = getattr(window, "_update_progress", None)
    if progress is None:
        return False
    try:
        progress.show()
        progress.raise_()
        progress.activateWindow()
    except (RuntimeError, AttributeError):
        return False
    return True


def download_update(window, update: UpdateInfo, *, dest_dir=None):
    existing = getattr(window, "_update_download_thread", None)
    event = getattr(existing, "cancel_event", None)
    if _thread_is_running(existing) and event is not None and event.is_set():
        # A retry pressed in the moment between cancelling and the worker
        # actually unwinding. Never block the GUI thread waiting for it: say so
        # and let the user click again once the old job has let go.
        status = getattr(window, "statusBar", None)
        if callable(status):
            status().showMessage(
                "正在收尾上一次下載，請稍候再按一次重試。", 4000
            )
        return
    if _update_operation_in_progress(window):
        # Already busy: surface the existing job instead of starting a second.
        show_update_progress(window)
        return
    if _closing(window):
        return
    if not has_verifiable_digest(update):
        _prompt_manual_download(window, update)
        return

    # A retry always gets a brand-new job: new id, new cancel event, new temp
    # directory. No Range resume, so a half file can never be mixed with a new
    # release's bytes.
    _discard_ready_installer(window)
    request_id = _next_request_id(window)
    window._update_download_request = request_id
    _set_update_state(
        window, UPDATE_DOWNLOADING, version=update.latest_version
    )
    window._update_progress = _create_progress(window, request_id)

    thread = UpdateDownloadThread(
        update, QApplication.instance() or window, dest_dir=dest_dir
    )
    window._update_download_thread = thread
    window._update_cancel_event = thread.cancel_event
    window._update_downloading_info = update
    window_ref = weakref.ref(window)

    def dispatch_download_result(installer_path, error, *, request=request_id):
        _dispatch_download_result(
            window_ref, request, installer_path, error
        )

    def dispatch_progress(downloaded, total, *, request=request_id):
        _dispatch_download_progress(window_ref, request, downloaded, total)

    def dispatch_verifying(*, request=request_id):
        _dispatch_download_verifying(window_ref, request)

    def release_thread(*, worker=thread):
        target = _live_window(window_ref)
        if target is not None and getattr(
            target, "_update_download_thread", None
        ) is worker:
            target._update_download_thread = None
            target._update_cancel_event = None

    thread.finished_download.connect(dispatch_download_result)
    thread.progress_changed.connect(dispatch_progress)
    thread.verifying.connect(dispatch_verifying)
    thread.finished.connect(release_thread)
    thread.finished.connect(thread.deleteLater)
    thread.start()


def on_update_button_clicked(window) -> None:
    """Single entry point for the toolbar button and the Help-menu action."""
    state = ""
    controls = getattr(window, "_toolbar_utilities", None)
    if controls is not None:
        state = controls.update_state
    if state == UPDATE_CHECKING or state == UPDATE_LAUNCHING:
        return
    if getattr(window, "_update_ready_installer", None):
        prompt_install_ready(window)
        return
    if state in (UPDATE_DOWNLOADING, UPDATE_VERIFYING):
        # The progress window is hideable; bring it back instead of starting a
        # second download.
        if not show_update_progress(window):
            _restore_idle_state(window)
        return
    if state == UPDATE_CANCELLED:
        retry_update_download(window)
        return
    update = getattr(window, "_available_update", None)
    if update is not None:
        prompt_for_update(window, update)
        return
    # Prefer the window's own entry point so MainWindow keeps one seam.
    check = getattr(window, "_check_for_updates", None)
    if callable(check):
        check(manual=True)
        return
    check_for_updates(window, manual=True)


def retry_update_download(window):
    update = getattr(window, "_available_update", None) or getattr(
        window, "_update_downloading_info", None
    )
    if update is None:
        check_for_updates(window, manual=True)
        return
    download_update(window, update)


def cancel_update_download(window, *, quiet: bool = False):
    """Stop the running job now; the worker unwinds and cleans up its own dir.

    The request id is bumped immediately so any in-flight progress or result
    signal from the abandoned worker is dropped, which is what makes the UI
    respond instantly even though the thread takes a moment to exit.
    """
    thread = getattr(window, "_update_download_thread", None)
    event = getattr(window, "_update_cancel_event", None)
    if thread is None and event is None:
        return False
    _next_request_id(window)
    if event is not None:
        event.set()
    canceller = getattr(thread, "cancel", None)
    if callable(canceller):
        canceller()
    _close_progress(window)
    if not quiet:
        version = _update_version(window)
        _set_update_state(window, UPDATE_CANCELLED, version=version)
        status = getattr(window, "statusBar", None)
        if callable(status):
            status().showMessage("已取消更新下載。", 4000)
    return True


def on_update_download_progress(window, downloaded, total, *, request=None):
    if not _is_current_request(window, request) or _closing(window):
        return
    progress = getattr(window, "_update_progress", None)
    if progress is None:
        return
    try:
        if total:
            progress.setMaximum(int(total))
            progress.setValue(int(min(downloaded, total)))
            progress.setLabelText(
                "正在下載更新… "
                f"{_human_size(downloaded)} / {_human_size(total)}"
                f"（{int(downloaded * 100 / total)}%）"
            )
        else:
            # No trustworthy length: keep the bar indeterminate and report the
            # byte count only. Never imply a percentage we cannot compute.
            progress.setMaximum(0)
            progress.setLabelText(
                f"正在下載更新… 已下載 {_human_size(downloaded)}"
            )
    except RuntimeError:
        window._update_progress = None


def on_update_download_verifying(window, *, request=None):
    if not _is_current_request(window, request) or _closing(window):
        return
    _set_update_state(window, UPDATE_VERIFYING, version=_update_version(window))
    progress = getattr(window, "_update_progress", None)
    if progress is None:
        return
    try:
        progress.setMaximum(0)
        progress.setLabelText("正在驗證下載內容…")
    except RuntimeError:
        window._update_progress = None


def _human_size(value) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def on_update_download_done(window, installer_path, error, *, request=None):
    if not _is_current_request(window, request):
        # A cancelled or superseded job: drop its bytes, keep its temp dir out
        # of the way, and leave the current state untouched.
        if installer_path:
            cleanup_job_dir(installer_path)
        return
    _close_progress(window)
    update = getattr(window, "_update_downloading_info", None)

    if error:
        if _closing(window):
            return
        if isinstance(error, UpdateCancelled):
            _set_update_state(
                window, UPDATE_CANCELLED, version=_update_version(window)
            )
            return
        if isinstance(error, UpdateDigestUnavailable):
            _prompt_manual_download(window, update or UpdateInfo(
                True, VERSION, _update_version(window),
                getattr(error, "release_url", "") or "",
            ), str(error))
            return
        _restore_available_or_error(window)
        QMessageBox.warning(window, "更新下載失敗", str(error))
        return

    window._update_ready_installer = Path(installer_path)
    window._update_ready_digest = (
        getattr(update, "asset_digest", None) if update is not None else None
    )
    _set_update_state(window, UPDATE_READY, version=_update_version(window))

    if _closing(window):
        # A callback that lands while the window is going away must never
        # launch an installer behind the user's back.
        return
    prompt_install_ready(window)


# --- install lifecycle --------------------------------------------------


def _discard_ready_installer(window) -> None:
    path = getattr(window, "_update_ready_installer", None)
    window._update_ready_installer = None
    window._update_ready_digest = None
    if path:
        cleanup_job_dir(path)


def _ask_install_now(window, version: str) -> bool:
    """Modal choice between installing now and keeping the file for later."""
    box = QMessageBox(window)
    box.setWindowTitle("更新已準備安裝")
    box.setText(
        f"版本 {version} 已下載並通過完整性驗證。\n\n"
        "安裝程式會關閉 Markdown Viewer。要現在安裝嗎？"
    )
    install = box.addButton("立即安裝", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("稍後", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is install


def prompt_install_ready(window) -> None:
    path = getattr(window, "_update_ready_installer", None)
    if not path:
        return
    version = _update_version(window)
    if _ask_install_now(window, version):
        install_ready_update(window)
        return
    # "Later": keep the verified file for this session so a second click does
    # not re-download. It is deliberately not persisted across restarts.
    _set_update_state(window, UPDATE_READY, version=version)
    status = getattr(window, "statusBar", None)
    if callable(status):
        status().showMessage("更新已就緒，可隨時從更新按鈕安裝。", 5000)


def install_ready_update(window) -> None:
    if _closing(window):
        return
    path = getattr(window, "_update_ready_installer", None)
    digest = getattr(window, "_update_ready_digest", None)
    if not path or not verify_installer(path, digest):
        _discard_ready_installer(window)
        _restore_available_or_error(window)
        QMessageBox.warning(
            window,
            "更新失敗",
            "已下載的安裝檔已遺失或無法通過驗證，請重新下載。",
        )
        return

    _set_update_state(window, UPDATE_LAUNCHING, version=_update_version(window))

    def _after_snapshot() -> None:
        confirm = getattr(window, "_confirm_close_all_edits", None)
        if confirm is None:
            confirm = getattr(window, "_confirm_discard_edits", None)
        if callable(confirm) and not confirm():
            # The user backed out of saving: do not install, do not quit.
            _set_update_state(
                window, UPDATE_READY, version=_update_version(window)
            )
            return
        _launch_installer(window)

    snapshot = getattr(window, "_request_live_wysiwyg_snapshot", None)
    if callable(snapshot):
        # Office/WYSIWYG editing keeps its last keystrokes in the web view;
        # flush them to Python before anything can terminate the process.
        if not snapshot(_after_snapshot, purpose="安裝更新"):
            _set_update_state(
                window, UPDATE_READY, version=_update_version(window)
            )
        return
    _after_snapshot()


def _launch_installer(window) -> None:
    path = getattr(window, "_update_ready_installer", None)
    digest = getattr(window, "_update_ready_digest", None)
    # Re-verify: saving may have taken a while, and the file lives in a
    # world-writable temp tree.
    if not path or not verify_installer(path, digest):
        _discard_ready_installer(window)
        _restore_available_or_error(window)
        QMessageBox.warning(
            window,
            "更新失敗",
            "已下載的安裝檔已遺失或無法通過驗證，請重新下載。",
        )
        return
    if _closing(window):
        return

    result = QProcess.startDetached(str(path))
    started = result[0] if isinstance(result, tuple) else bool(result)
    if not started:
        # Keep the app usable and the verified file around so the user can
        # simply try again. A successful startDetached is not proof that the
        # installer ran or that UAC was granted either.
        _set_update_state(window, UPDATE_READY, version=_update_version(window))
        QMessageBox.warning(
            window, "更新失敗", "無法啟動安裝程式，請稍後再試。"
        )
        return
    QApplication.quit()
