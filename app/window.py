"""Main application window with toolbar, side panel, and renderer workspace."""

from pathlib import Path

from PyQt6.QtCore import QProcess, QSettings, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .left_panel import LeftPanel
from .renderer import RendererView
from .theme import (
    HIT_TARGET,
    PANEL_WIDTH,
    TOOLBAR_HEIGHT,
    ThemeName,
    app_stylesheet,
    get_theme,
    svg_icon,
    toolbar_stylesheet,
)
from .updater import UpdateInfo, check_for_update, download_installer
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


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

        settings = QSettings(_ORG, _APP)
        self._theme_name: ThemeName = settings.value("theme", "light") or "light"
        if self._theme_name != "dark":
            self._theme_name = "light"
        self._theme = get_theme(self._theme_name)
        self._current_file: Path | None = None

        self.setWindowTitle("Markdown Viewer")
        self._restore_geometry()
        self._sidebar_open = True
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._setup_menu()

        self._panel = LeftPanel(
            on_file_selected=self._open_file,
            on_anchor_clicked=self._scroll_to_anchor,
            theme=self._theme,
        )
        self._renderer = RendererView(
            on_headings_ready=self._panel.toc.update_headings
        )
        self._renderer.active_anchor_changed.connect(
            self._panel.toc.set_active_anchor
        )
        self._panel.close_btn.clicked.connect(self._toggle_sidebar)

        self._search_bar = self._build_search_bar()
        self._search_bar.hide()

        renderer_wrap = QWidget()
        renderer_wrap.setObjectName("rendererWorkspace")
        renderer_layout = QVBoxLayout(renderer_wrap)
        renderer_layout.setContentsMargins(0, 0, 0, 0)
        renderer_layout.setSpacing(0)
        renderer_layout.addWidget(self._search_bar)
        renderer_layout.addWidget(self._renderer)

        self._restore_btn = QPushButton(self._renderer)
        self._restore_btn.setFixedSize(HIT_TARGET, HIT_TARGET)
        self._restore_btn.setIconSize(QSize(20, 20))
        self._restore_btn.setToolTip("展開側邊欄")
        self._restore_btn.setAccessibleName("展開側邊欄")
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.clicked.connect(self._toggle_sidebar)
        self._restore_btn.move(8, 8)
        self._restore_btn.hide()

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._panel)
        self._splitter.addWidget(renderer_wrap)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([PANEL_WIDTH, 960])
        self._splitter.setHandleWidth(4)

        self._toolbar = self._build_toolbar()
        self._reload_btn.setEnabled(False)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._toolbar)
        root_layout.addWidget(self._splitter, stretch=1)
        self.setCentralWidget(root)
        self.setAcceptDrops(True)

        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(
            self._panel_open_file
        )
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._toggle_search
        )
        QShortcut(QKeySequence("Escape"), self).activated.connect(
            self._close_search
        )

        self._apply_theme()
        QTimer.singleShot(2000, self._check_updates_silent)

    def _setup_menu(self):
        help_menu = self.menuBar().addMenu("&Help")
        check_update_action = QAction(f"檢查更新... (v{VERSION})", self)
        check_update_action.triggered.connect(lambda: self._check_for_updates(manual=True))
        help_menu.addAction(check_update_action)

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setObjectName("topToolbar")
        toolbar.setFixedHeight(TOOLBAR_HEIGHT)

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 0, 10, 0)
        layout.setSpacing(4)

        self._sidebar_btn = self._toolbar_button(
            "panel-left", "收合側邊欄", self._toggle_sidebar
        )
        self._open_btn = self._toolbar_button(
            "folder-open", "開啟 Markdown 文件", self._panel_open_file
        )
        self._search_btn = self._toolbar_button(
            "search", "搜尋目前文件", self._toggle_search
        )
        self._reload_btn = self._toolbar_button(
            "refresh", "重新載入文件", self._reload_current
        )
        self._theme_btn = self._toolbar_button(
            "moon", "切換深色模式", self._toggle_theme
        )
        self._update_btn = self._toolbar_button(
            "download", "檢查更新", lambda: self._check_for_updates(manual=True)
        )

        title_wrap = QWidget()
        title_layout = QVBoxLayout(title_wrap)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(0)

        self._toolbar_title = QLabel("Markdown Viewer")
        self._toolbar_title.setObjectName("toolbarTitle")
        self._toolbar_subtitle = QLabel("尚未載入文件")
        self._toolbar_subtitle.setObjectName("toolbarSubtitle")

        title_layout.addStretch()
        title_layout.addWidget(self._toolbar_title)
        title_layout.addWidget(self._toolbar_subtitle)
        title_layout.addStretch()

        layout.addWidget(self._sidebar_btn)
        layout.addWidget(self._open_btn)
        layout.addWidget(self._search_btn)
        layout.addWidget(self._reload_btn)
        layout.addWidget(title_wrap, stretch=1)
        layout.addWidget(self._theme_btn)
        layout.addWidget(self._update_btn)
        return toolbar

    def _toolbar_button(self, icon_name: str, tooltip: str, callback) -> QPushButton:
        button = QPushButton()
        button.setProperty("iconName", icon_name)
        button.setFixedSize(HIT_TARGET, HIT_TARGET)
        button.setIconSize(QSize(20, 20))
        button.setIcon(svg_icon(icon_name, self._theme.text_muted))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _apply_theme(self):
        self._theme = get_theme(self._theme_name)
        self.setStyleSheet(app_stylesheet(self._theme))
        self._toolbar.setStyleSheet(
            toolbar_stylesheet(self._theme)
            + f"""
QLabel#toolbarTitle {{
    background: transparent;
    color: {self._theme.text};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#toolbarSubtitle {{
    background: transparent;
    color: {self._theme.text_muted};
    font-size: 12px;
}}
"""
        )
        self._search_bar.setStyleSheet(self._search_style())
        self._panel.apply_theme(self._theme)
        self._splitter.setStyleSheet(
            f"""
QSplitter::handle {{
    background: {self._theme.border};
}}
QSplitter::handle:hover {{
    background: {self._theme.surface_hover};
}}
"""
        )
        self._restore_btn.setStyleSheet(
            toolbar_stylesheet(self._theme)
            + f"""
QPushButton {{
    background: {self._theme.surface};
    border: 1px solid {self._theme.border};
    border-radius: 8px;
}}
QPushButton:hover {{
    background: {self._theme.surface_hover};
    border-color: {self._theme.accent};
}}
QPushButton:pressed {{
    background: {self._theme.surface_active};
}}
"""
        )
        self._refresh_icons()
        self._renderer.set_theme(self._theme_name)

    def _refresh_icons(self):
        icon_color = self._theme.text_muted
        disabled_color = self._theme.text_subtle
        for button in (
            self._sidebar_btn,
            self._open_btn,
            self._search_btn,
            self._reload_btn,
            self._update_btn,
        ):
            icon_name = button.property("iconName")
            color = icon_color if button.isEnabled() else disabled_color
            button.setIcon(svg_icon(icon_name, color, 20))

        theme_icon = "sun" if self._theme_name == "dark" else "moon"
        theme_tip = "切換淺色模式" if self._theme_name == "dark" else "切換深色模式"
        self._theme_btn.setProperty("iconName", theme_icon)
        self._theme_btn.setToolTip(theme_tip)
        self._theme_btn.setAccessibleName(theme_tip)
        self._theme_btn.setIcon(svg_icon(theme_icon, icon_color, 20))

        restore_icon = "panel-left"
        self._restore_btn.setIcon(svg_icon(restore_icon, icon_color, 20))

        self._search_prev_btn.setIcon(svg_icon("chevron-left", icon_color, 18))
        self._search_next_btn.setIcon(svg_icon("chevron-right", icon_color, 18))
        self._search_close_btn.setIcon(svg_icon("x", icon_color, 18))

    def _toggle_theme(self):
        self._theme_name = "light" if self._theme_name == "dark" else "dark"
        QSettings(_ORG, _APP).setValue("theme", self._theme_name)
        self._apply_theme()

    def _search_style(self) -> str:
        return f"""
QWidget#searchBar {{
    background: {self._theme.window};
    border-bottom: 1px solid {self._theme.border};
}}
QWidget#searchBar QWidget {{
    background: transparent;
}}
QWidget#searchBar QLineEdit {{
    background: {self._theme.surface};
    border: 1px solid {self._theme.border};
    border-radius: 6px;
    color: {self._theme.text};
    min-height: 32px;
    padding: 4px 10px;
    selection-background-color: {self._theme.accent_soft};
    selection-color: {self._theme.text};
}}
QWidget#searchBar QLineEdit:hover {{
    border-color: {self._theme.accent};
}}
QWidget#searchBar QLineEdit:focus {{
    border-color: {self._theme.accent};
    background: {self._theme.surface};
}}
QWidget#searchBar QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {self._theme.text_muted};
    min-width: 36px;
    min-height: 36px;
    padding: 0;
}}
QWidget#searchBar QPushButton:hover {{
    background: {self._theme.surface_hover};
    border-color: {self._theme.surface_hover};
    color: {self._theme.text};
}}
QWidget#searchBar QPushButton:focus {{
    border-color: {self._theme.accent};
}}
QWidget#searchBar QPushButton:pressed {{
    background: {self._theme.surface_active};
    border-color: {self._theme.accent};
}}
QWidget#searchBar QLabel {{
    background: transparent;
    color: {self._theme.text_muted};
    font-size: 12px;
    padding: 0 4px;
}}
"""

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("searchBar")
        bar.setFixedHeight(44)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 8, 4)
        layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜尋目前文件")
        self._search_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)

        self._search_count = QLabel("")

        self._search_prev_btn = self._search_button(
            "上一個結果 (Shift+Enter)", self._search_prev
        )
        self._search_next_btn = self._search_button(
            "下一個結果 (Enter)", self._search_next
        )
        self._search_close_btn = self._search_button(
            "關閉搜尋 (Esc)", self._close_search
        )

        self._search_prev_btn.setIcon(
            svg_icon("chevron-left", self._theme.text_muted, 18)
        )
        self._search_next_btn.setIcon(
            svg_icon("chevron-right", self._theme.text_muted, 18)
        )
        self._search_close_btn.setIcon(svg_icon("x", self._theme.text_muted, 18))

        layout.addWidget(self._search_input)
        layout.addWidget(self._search_count)
        layout.addWidget(self._search_prev_btn)
        layout.addWidget(self._search_next_btn)
        layout.addWidget(self._search_close_btn)
        return bar

    def _search_button(self, tooltip: str, callback) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(36, 36)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

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
        self._renderer.find_text("")
        self._renderer.setFocus()

    def _on_search_text_changed(self, text: str):
        if not text:
            self._search_count.setText("")
            self._renderer.find_text("")
            return

        self._search_count.setText("正在搜尋...")
        self._renderer.find_text(
            text,
            lambda found, needle=text: self._on_search_result(needle, found),
        )

    def _on_search_result(self, needle: str, result):
        if needle != self._search_input.text():
            return
        found = (
            result.numberOfMatches() > 0
            if hasattr(result, "numberOfMatches")
            else bool(result)
        )
        self._search_count.setText("" if found else "找不到結果")

    def _search_next(self):
        self._renderer.find_next(self._search_input.text())

    def _search_prev(self):
        self._renderer.find_prev(self._search_input.text())

    def _toggle_sidebar(self):
        self._renderer.page().runJavaScript("window.scrollY", self._do_toggle)

    def _do_toggle(self, scroll_y: float):
        scroll_y = int(scroll_y or 0)
        self._sidebar_open = not self._sidebar_open
        width = max(self._splitter.width(), PANEL_WIDTH)

        if self._sidebar_open:
            self._panel.show()
            self._splitter.setSizes([PANEL_WIDTH, max(width - PANEL_WIDTH, 1)])
            self._restore_btn.hide()
            self._sidebar_btn.setToolTip("收合側邊欄")
            self._sidebar_btn.setAccessibleName("收合側邊欄")
        else:
            self._panel.hide()
            self._splitter.setSizes([0, width])
            self._restore_btn.show()
            self._restore_btn.raise_()
            self._sidebar_btn.setToolTip("展開側邊欄")
            self._sidebar_btn.setAccessibleName("展開側邊欄")

        QTimer.singleShot(
            50,
            lambda: self._renderer.page().runJavaScript(
                f"window.scrollTo(0, {scroll_y})"
            ),
        )

    def _open_file(self, filepath: str):
        path = Path(filepath)
        self._current_file = path
        self.setWindowTitle(f"{path.name} - Markdown Viewer")
        self._toolbar_title.setText(path.name)
        self._toolbar_subtitle.setText(str(path.parent))
        self._renderer.load_file(path)
        self._panel.file_browser.navigate_to(path.parent)
        self._panel.recent.add(str(path))
        self._reload_btn.setEnabled(True)
        self._refresh_icons()

    def open_path(self, filepath: str):
        self._open_file(filepath)

    def _panel_open_file(self):
        self._panel.open_file_dialog()

    def _reload_current(self):
        if not self._current_file:
            return
        self._renderer.reload_current()
        self.statusBar().showMessage("已重新載入文件", 3000)

    def _scroll_to_anchor(self, anchor: str):
        self._renderer.scroll_to(anchor)

    def _check_updates_silent(self):
        self._check_for_updates(manual=False)

    def _check_for_updates(self, manual: bool):
        if self._update_check_thread and self._update_check_thread.isRunning():
            return

        if manual:
            self.statusBar().showMessage("正在檢查更新...")

        self._update_check_thread = UpdateCheckThread(self)
        self._update_check_thread.finished_check.connect(
            lambda update, error, is_manual=manual: self._on_update_check_done(
                update, error, is_manual
            )
        )
        self._update_check_thread.start()

    def _on_update_check_done(self, update, error, manual: bool):
        self.statusBar().clearMessage()

        if error:
            if manual:
                QMessageBox.warning(self, "更新檢查失敗", str(error))
            return

        if not update.has_update:
            if manual:
                QMessageBox.information(
                    self,
                    "目前已是最新版本",
                    f"Markdown Viewer 已是最新版本。\n目前版本：{VERSION}",
                )
            return

        answer = QMessageBox.question(
            self,
            "有可用更新",
            f"版本 {update.latest_version} 已可下載。\n\n"
            "是否要立即下載並安裝？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(update)

    def _download_update(self, update: UpdateInfo):
        if self._update_download_thread and self._update_download_thread.isRunning():
            return

        self._update_progress = QProgressDialog("正在下載更新...", None, 0, 0, self)
        self._update_progress.setWindowTitle("Markdown Viewer 更新")
        self._update_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._update_progress.setMinimumDuration(0)
        self._update_progress.show()

        self._update_download_thread = UpdateDownloadThread(update, self)
        self._update_download_thread.finished_download.connect(
            self._on_update_download_done
        )
        self._update_download_thread.start()

    def _on_update_download_done(self, installer_path, error):
        if self._update_progress:
            self._update_progress.close()
            self._update_progress = None

        if error:
            QMessageBox.warning(self, "更新下載失敗", str(error))
            return

        result = QProcess.startDetached(str(installer_path))
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            QMessageBox.warning(self, "更新失敗", "無法啟動安裝程式。")
            return

        QApplication.quit()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            if any(
                u.toLocalFile().lower().endswith(".md")
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".md"):
                self._open_file(local)
                break

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
