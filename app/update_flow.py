"""Update check and installer download flow delegated from MainWindow."""

import time

from PySide6.QtCore import QProcess, QSettings, QThread, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from .updater import UpdateInfo, check_for_update, download_installer
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


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
    if window._update_check_thread and window._update_check_thread.isRunning():
        return

    if manual:
        window.statusBar().showMessage("正在檢查更新...")

    window._update_check_thread = UpdateCheckThread(window)
    window._update_check_thread.finished_check.connect(
        lambda update, error, is_manual=manual: on_update_check_done(
            window, update, error, is_manual
        )
    )
    window._update_check_thread.start()


def on_update_check_done(window, update, error, manual: bool):
    window.statusBar().clearMessage()

    if error:
        if manual:
            QMessageBox.warning(window, "更新檢查失敗", str(error))
        return

    if not update.has_update:
        if manual:
            QMessageBox.information(
                window,
                "目前已是最新版本",
                f"Markdown Viewer 已是最新版本。\n目前版本：{VERSION}",
            )
        return

    answer = QMessageBox.question(
        window,
        "有可用更新",
        f"版本 {update.latest_version} 已可下載。\n\n"
        "是否要立即下載並安裝？",
    )
    if answer == QMessageBox.StandardButton.Yes:
        download_update(window, update)


def download_update(window, update: UpdateInfo):
    if window._update_download_thread and window._update_download_thread.isRunning():
        return

    window._update_progress = QProgressDialog("正在下載更新...", None, 0, 0, window)
    window._update_progress.setWindowTitle("Markdown Viewer 更新")
    window._update_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    window._update_progress.setMinimumDuration(0)
    window._update_progress.show()

    window._update_download_thread = UpdateDownloadThread(update, window)
    window._update_download_thread.finished_download.connect(
        lambda installer_path, error: on_update_download_done(
            window, installer_path, error
        )
    )
    window._update_download_thread.start()


def on_update_download_done(window, installer_path, error):
    if window._update_progress:
        window._update_progress.close()
        window._update_progress = None

    if error:
        QMessageBox.warning(window, "更新下載失敗", str(error))
        return

    result = QProcess.startDetached(str(installer_path))
    started = result[0] if isinstance(result, tuple) else bool(result)
    if not started:
        QMessageBox.warning(window, "更新失敗", "無法啟動安裝程式。")
        return

    QApplication.quit()
