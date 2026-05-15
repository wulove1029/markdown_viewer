"""Main application window — Obsidian-style layout."""

from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QSplitter, QPushButton, QLineEdit, QLabel,
                              QMessageBox, QProgressDialog)
from PyQt6.QtCore import Qt, QSettings, QTimer, QThread, QProcess, pyqtSignal
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent, QShortcut, QKeySequence

from .ribbon import Ribbon
from .left_panel import LeftPanel
from .renderer import RendererView
from .updater import UpdateInfo, check_for_update, download_installer
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"

_SEARCH_STYLE = """
QWidget#searchBar {
    background: #eeecea;
    border-bottom: 1px solid #d5d5d0;
}
QLineEdit {
    background: #fff;
    border: 1px solid #c8c8c4;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 13px;
    color: #333;
    min-width: 200px;
}
QLineEdit:focus { border-color: #7b6cd4; }
QPushButton {
    background: transparent;
    border: none;
    color: #666;
    font-size: 13px;
    padding: 2px 8px;
    border-radius: 4px;
}
QPushButton:hover { background: #d5d3f0; color: #5a4faf; }
QLabel { font-size: 12px; color: #888; padding: 0 4px; }
"""

_FLOAT_STYLE = """
QPushButton {
    background: #e8e8e4;
    border: 1px solid #d5d5d0;
    border-radius: 4px;
    color: #666;
    font-size: 16px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
    padding: 0;
}
QPushButton:hover { background: #d5d3f0; color: #5a4faf; }
"""


class UpdateCheckThread(QThread):
    finished_check = pyqtSignal(object, object)

    def run(self):
        try:
            self.finished_check.emit(check_for_update(), None)
        except Exception as exc:
            self.finished_check.emit(None, exc)


class UpdateDownloadThread(QThread):
    finished_download = pyqtSignal(object, object)

    def __init__(self, update: UpdateInfo, parent=None):
        super().__init__(parent)
        self._update = update

    def run(self):
        try:
            self.finished_download.emit(download_installer(self._update), None)
        except Exception as exc:
            self.finished_download.emit(None, exc)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Markdown Viewer")
        self.setStyleSheet("QMainWindow { background: #f5f5f2; }")
        self._restore_geometry()
        self._sidebar_open = True
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._setup_menu()

        # ── widgets ───────────────────────────────────────────────
        self._panel = LeftPanel(
            on_file_selected=self._open_file,
            on_anchor_clicked=self._scroll_to_anchor,
        )
        self._renderer = RendererView(
            on_headings_ready=self._panel.toc.update_headings
        )
        self._renderer.active_anchor_changed.connect(
            self._panel.toc.set_active_anchor
        )
        self._ribbon = Ribbon(
            on_tab_changed=self._on_ribbon_tab,
            on_toggle_sidebar=self._toggle_sidebar,
        )
        self._panel.close_btn.clicked.connect(self._toggle_sidebar)

        # ── search bar ────────────────────────────────────────────
        self._search_bar = self._build_search_bar()
        self._search_bar.hide()

        # ── renderer wrap: [search_bar][renderer] ─────────────────
        renderer_wrap = QWidget()
        rv = QVBoxLayout(renderer_wrap)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._search_bar)
        rv.addWidget(self._renderer)

        # ── splitter: panel | renderer_wrap ───────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._panel)
        self._splitter.addWidget(renderer_wrap)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([240, 960])
        self._splitter.setHandleWidth(3)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: #d5d5d0; }"
        )

        # ── float btn (渲染區左上角，收合時顯示) ──────────────────
        self._float_btn = QPushButton("◫")
        self._float_btn.setToolTip("展開側邊欄")
        self._float_btn.setStyleSheet(_FLOAT_STYLE)
        self._float_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._float_btn.clicked.connect(self._toggle_sidebar)
        self._float_btn.setParent(self._renderer)
        self._float_btn.move(8, 8)
        self._float_btn.hide()

        # ── root layout: [ribbon][splitter] ───────────────────────
        root = QWidget()
        rh = QHBoxLayout(root)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(0)
        rh.addWidget(self._ribbon)
        rh.addWidget(self._splitter, stretch=1)

        self.setCentralWidget(root)
        self.setAcceptDrops(True)

        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(
            self._panel.open_file_dialog
        )
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._toggle_search
        )
        QShortcut(QKeySequence("Escape"), self).activated.connect(
            self._close_search
        )
        QTimer.singleShot(2000, self._check_updates_silent)

    def _setup_menu(self):
        help_menu = self.menuBar().addMenu("&Help")
        check_update_action = QAction(f"Check for Updates... (v{VERSION})", self)
        check_update_action.triggered.connect(lambda: self._check_for_updates(manual=True))
        help_menu.addAction(check_update_action)

    # ── search bar ────────────────────────────────────────────────

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("searchBar")
        bar.setFixedHeight(36)
        bar.setStyleSheet(_SEARCH_STYLE)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜尋關鍵字…")
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)

        self._search_count = QLabel("")

        btn_prev = QPushButton("▲")
        btn_prev.setToolTip("上一個 (Shift+Enter)")
        btn_prev.setFixedWidth(28)
        btn_prev.clicked.connect(self._search_prev)

        btn_next = QPushButton("▼")
        btn_next.setToolTip("下一個 (Enter)")
        btn_next.setFixedWidth(28)
        btn_next.clicked.connect(self._search_next)

        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(28)
        btn_close.setToolTip("關閉 (Esc)")
        btn_close.clicked.connect(self._close_search)

        hl.addWidget(self._search_input)
        hl.addWidget(self._search_count)
        hl.addWidget(btn_prev)
        hl.addWidget(btn_next)
        hl.addWidget(btn_close)
        return bar

    def _toggle_search(self):
        if self._search_bar.isHidden():
            self._search_bar.show()
            self._search_input.setFocus()
            self._search_input.selectAll()
        else:
            self._close_search()

    def _close_search(self):
        self._search_bar.hide()
        self._search_input.clear()
        self._renderer.page().findText("")  # 清除高亮
        self._renderer.setFocus()

    def _on_search_text_changed(self, text: str):
        self._renderer.find_text(text)
        self._search_count.setText("")

    def _search_next(self):
        self._renderer.find_next(self._search_input.text())

    def _search_prev(self):
        self._renderer.find_prev(self._search_input.text())

    # ── sidebar toggle ────────────────────────────────────────────

    def _toggle_sidebar(self):
        # 先取得捲動位置，resize 後再還原（QWebEngineView resize 會重置捲動）
        self._renderer.page().runJavaScript("window.scrollY", self._do_toggle)

    def _do_toggle(self, scroll_y: float):
        scroll_y = int(scroll_y or 0)
        self._sidebar_open = not self._sidebar_open
        if self._sidebar_open:
            self._ribbon.show()
            self._splitter.setSizes([240, self._splitter.width() - 240])
            self._panel.show()
            self._float_btn.hide()
        else:
            self._ribbon.hide()
            self._panel.hide()
            self._splitter.setSizes([0, self._splitter.width()])
            self._float_btn.show()
            self._float_btn.raise_()
        QTimer.singleShot(50, lambda: self._renderer.page().runJavaScript(
            f"window.scrollTo(0, {scroll_y})"
        ))

    # ── ribbon ────────────────────────────────────────────────────

    def _on_ribbon_tab(self, index: int):
        if not self._sidebar_open:
            self._toggle_sidebar()
        self._panel.switch_to(index)

    # ── file opening ──────────────────────────────────────────────

    def _open_file(self, filepath: str):
        path = Path(filepath)
        self.setWindowTitle(f"{path.name} — Markdown Viewer")
        self._renderer.load_file(path)
        self._panel.file_browser.navigate_to(path.parent)
        self._panel.recent.add(filepath)

    def open_path(self, filepath: str):
        self._open_file(filepath)

    # ── TOC ───────────────────────────────────────────────────────

    def _scroll_to_anchor(self, anchor: str):
        self._renderer.scroll_to(anchor)

    # ── updates ──────────────────────────────────────────────────

    def _check_updates_silent(self):
        self._check_for_updates(manual=False)

    def _check_for_updates(self, manual: bool):
        if self._update_check_thread and self._update_check_thread.isRunning():
            return

        if manual:
            self.statusBar().showMessage("Checking for updates...")

        self._update_check_thread = UpdateCheckThread(self)
        self._update_check_thread.finished_check.connect(
            lambda update, error, is_manual=manual: self._on_update_check_done(update, error, is_manual)
        )
        self._update_check_thread.start()

    def _on_update_check_done(self, update, error, manual: bool):
        self.statusBar().clearMessage()

        if error:
            if manual:
                QMessageBox.warning(self, "Update Check Failed", str(error))
            return

        if not update.has_update:
            if manual:
                QMessageBox.information(
                    self,
                    "No Update Available",
                    f"Markdown Viewer is already up to date.\nCurrent version: {VERSION}",
                )
            return

        answer = QMessageBox.question(
            self,
            "Update Available",
            f"Version {update.latest_version} is available.\n\n"
            "Download and install it now?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(update)

    def _download_update(self, update: UpdateInfo):
        if self._update_download_thread and self._update_download_thread.isRunning():
            return

        self._update_progress = QProgressDialog("Downloading update...", None, 0, 0, self)
        self._update_progress.setWindowTitle("Markdown Viewer Update")
        self._update_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._update_progress.setMinimumDuration(0)
        self._update_progress.show()

        self._update_download_thread = UpdateDownloadThread(update, self)
        self._update_download_thread.finished_download.connect(self._on_update_download_done)
        self._update_download_thread.start()

    def _on_update_download_done(self, installer_path, error):
        if self._update_progress:
            self._update_progress.close()
            self._update_progress = None

        if error:
            QMessageBox.warning(self, "Update Download Failed", str(error))
            return

        if not QProcess.startDetached(str(installer_path)):
            QMessageBox.warning(self, "Update Failed", "Unable to start the installer.")
            return

        QApplication.quit()

    # ── drag & drop ───────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            if any(u.toLocalFile().lower().endswith(".md")
                   for u in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".md"):
                self._open_file(local)
                break

    # ── geometry ──────────────────────────────────────────────────

    def _restore_geometry(self):
        settings = QSettings(_ORG, _APP)
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1200, 750)

    def closeEvent(self, event):
        QSettings(_ORG, _APP).setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
