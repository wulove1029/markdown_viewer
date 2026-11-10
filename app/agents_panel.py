"""'AI Agents' dock: the Watchdog dashboard plus the status-bar badge."""

from __future__ import annotations

import math

import shiboken6
from PySide6.QtCore import QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QTextOption
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .agent_watchdog import DEFAULT_START_BAT, AgentWatcher
from .agent_watchdog_state import is_loopback_url, panel_url
from .theme import CONTROL_RADIUS, HIT_TARGET, LIGHT, Theme

# The page is frozen once the view really is hidden (see _on_visibility).
_FREEZE_RETRIES = 20
_FREEZE_RETRY_MS = 50


def badge_kind(watcher: AgentWatcher | None) -> str:
    """'off', 'stuck', 'waiting' or 'idle' for the badge and toolbar button."""
    if watcher is None or watcher.state.unavailable:
        return "off"
    waiting, stuck = watcher.state._counts()
    if stuck:
        return "stuck"
    return "waiting" if waiting else "idle"


def badge_colors(theme: Theme, kind: str) -> tuple[str, str, str]:
    """(background, foreground, border) for a badge kind on the given theme."""
    dark = theme.name == "dark"
    if kind == "stuck":
        return (("#4a2420", theme.danger, "#7a3a34") if dark
                else ("#fde8e4", theme.danger, "#f1b0a6"))
    if kind == "waiting":
        return (("#46380f", theme.warning, "#6e5a1c") if dark
                else ("#fff1cc", theme.warning, "#efcf80"))
    return theme.surface_alt, theme.text_subtle, theme.border


class AgentBadge(QLabel):
    """Clickable pill in the status bar; colours follow state and theme."""

    def __init__(self, parent: QWidget | None = None, theme: Theme = LIGHT) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clicked_callback = None
        self._theme = theme
        self._kind = "off"
        self.kind = "off"

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.clicked_callback is not None:
            self.clicked_callback()
        super().mousePressEvent(event)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._restyle()

    def _restyle(self) -> None:
        bg, fg, border = badge_colors(self._theme, self._kind)
        weight = "700" if self._kind in ("stuck", "waiting") else "500"
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border: 1px solid {border};"
            f" border-radius: 11px; padding: 2px 10px; font-weight: {weight}; }}"
        )
        font = self.font()
        font.setStrikeOut(self._kind == "off")
        self.setFont(font)

    def refresh(self, watcher: AgentWatcher | None) -> None:
        self._kind = self.kind = badge_kind(watcher)
        if watcher is None:
            self.setText("🤖 未啟動")
            self.setToolTip("AI Agent 監看未啟用")
        else:
            self.setText(watcher.state.badge_text())
            self.setToolTip(watcher.state.tooltip())
        self._restyle()


class AgentToolbarButton(QPushButton):
    """Toolbar 'AI' button with a small count dot at the top right."""

    def __init__(self, theme: Theme = LIGHT, parent: QWidget | None = None) -> None:
        super().__init__("AI", parent)
        self.setObjectName("agentToolbarButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(HIT_TARGET, HIT_TARGET)
        self.setToolTip("AI Agents 面板")
        self.setAccessibleName("AI Agents 面板")
        self._theme = theme
        self.count = 0
        self.kind = "off"
        self._restyle()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._restyle()
        self.update()

    def _restyle(self) -> None:
        t = self._theme
        self.setStyleSheet(
            f"QPushButton#agentToolbarButton {{ background: transparent;"
            f" color: {t.text_muted}; border: none; border-radius: {CONTROL_RADIUS}px;"
            f" font-weight: 700; font-size: 13px; }}"
            f"QPushButton#agentToolbarButton:hover {{ background: {t.surface_hover}; }}"
        )

    def refresh(self, watcher: AgentWatcher | None) -> None:
        self.kind = badge_kind(watcher)
        self.count = 0
        if watcher is not None and not watcher.state.unavailable:
            waiting, stuck = watcher.state._counts()
            self.count = waiting + stuck
        if watcher is not None:
            self.setToolTip(watcher.state.tooltip())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.count <= 0:
            return
        bg, fg, _border = badge_colors(self._theme, self.kind)
        color = QColor(fg)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = 16
        rect = QRect(self.width() - size - 4, 4, size, size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(rect)
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self._theme.surface))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                         str(self.count) if self.count < 10 else "9+")
        painter.end()


class _DashboardPage(QWebEnginePage):
    """Only loopback http may load here; other links go to the system browser."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
        if url.isValid() and not url.isEmpty():
            scheme = url.scheme()
            if scheme == "about" and url.toString() == "about:blank":
                return True
            if scheme == "http" and is_loopback_url(url.toString()):
                return True
            clicked = (nav_type
                       == QWebEnginePage.NavigationType.NavigationTypeLinkClicked)
            if clicked and scheme in ("http", "https", "mailto"):
                # Decide only; the OS launch runs after Chromium's callback.
                target = QUrl(url)
                QTimer.singleShot(0, lambda: QDesktopServices.openUrl(target))
        return False


class _InfoText(QTextEdit):
    """The help page's text: looks like a word-wrapped label, but never asks
    for more width than it is given.

    Part of the text comes from outside the application (what the Watchdog
    reported about a failed start), so it is shown as plain text only, and a
    long run without spaces is broken wherever the line ends: a label would
    make that run its minimum width and push the dock and the main window
    apart.  Text that does not fit the height scrolls.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("agentInfoText")
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.document().setDocumentMargin(0)
        self.viewport().setAutoFillBackground(False)
        # The application style sheet draws every text box as an input field.
        self.setStyleSheet(
            "QTextEdit#agentInfoText, QTextEdit#agentInfoText:hover,"
            " QTextEdit#agentInfoText:focus { background: transparent;"
            " border: none; padding: 0; min-height: 0; }")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._measured: tuple[int, int] | None = None  # (width, text height)

    def setText(self, text: str) -> None:  # noqa: N802
        self.setPlainText(text)  # never interpreted as rich text
        self._measured = None
        self.updateGeometry()

    def text(self) -> str:
        return self.toPlainText()

    def textFormat(self) -> Qt.TextFormat:  # noqa: N802
        return Qt.TextFormat.PlainText

    def _text_height(self) -> int:
        width = max(self.width(), 1)
        if self._measured is None or self._measured[0] != width:
            document = self.document().clone()
            document.setTextWidth(width)
            self._measured = (width, math.ceil(document.size().height()))
            document.deleteLater()
        return self._measured[1]

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self._text_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # A few lines stay visible; the rest scrolls.
        return QSize(0, min(self._text_height(), 3 * self.fontMetrics().lineSpacing()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # the height the text needs follows the width


class AgentsDock(QDockWidget):
    def __init__(self, parent: QWidget | None = None, theme: Theme = LIGHT) -> None:
        super().__init__("AI Agents", parent)
        self.setObjectName("agentsDock")
        self._watcher: AgentWatcher | None = None
        self._theme = theme
        self._loaded_url: str | None = None
        self._loaded_declared = False  # _loaded_url is a declared panel page
        self._page_loaded = False  # a page was ever loaded (even if replaced)
        self._ever_shown = False
        self._shown = False  # last visibilityChanged value

        # Own off-the-record profile (no storage name): nothing touches disk,
        # and it is never the default profile nor a _DocumentPage.
        self._profile = QWebEngineProfile(self)
        self._shut_down = False
        self._view = QWebEngineView()
        self._page = _DashboardPage(self._profile, self._view)
        self._view.setPage(self._page)

        self._info = QWidget()
        layout = QVBoxLayout(self._info)
        self._info_label = _InfoText()
        self._redetect = QPushButton("重新偵測")
        self._redetect.clicked.connect(self._on_redetect)
        self._diagnostics = ""
        self._copy_diagnostics = QPushButton("複製診斷資訊")
        self._copy_diagnostics.clicked.connect(self._on_copy_diagnostics)
        self._copy_diagnostics.setVisible(False)
        layout.addWidget(self._info_label)
        layout.addWidget(self._redetect)
        layout.addWidget(self._copy_diagnostics)
        layout.addStretch(1)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._info)
        self._stack.addWidget(self._view)
        self.setWidget(self._stack)
        # Freezing is deferred: see _on_visibility().
        self._freeze_timer = QTimer(self)
        self._freeze_timer.setSingleShot(True)
        self._freeze_timer.timeout.connect(self._freeze_if_hidden)
        self._freeze_retries = 0
        self.visibilityChanged.connect(self._on_visibility)

    def set_watcher(self, watcher: AgentWatcher | None) -> None:
        self._watcher = watcher
        if watcher is not None:
            watcher.changed.connect(self.refresh)
        if self.isVisible():
            self.refresh()

    def apply_theme(self, theme: Theme) -> None:
        """Reload a declared panel page with the new scheme; "/" never reloads."""
        self._theme = theme
        # While hidden the page is frozen and refresh() skips a scheme-only
        # change; the next show loads the page with the current scheme.
        if self._loaded_declared and self._shown:
            self.refresh()

    # --- state ------------------------------------------------------------

    def _on_redetect(self) -> None:
        if self._watcher is not None:
            self._watcher.redetect()
        self.refresh()

    def _on_copy_diagnostics(self) -> None:
        if self._diagnostics:
            QApplication.clipboard().setText(self._diagnostics)

    def _on_visibility(self, visible: bool) -> None:
        self._shown = visible
        if visible:
            self._freeze_timer.stop()  # a pending freeze is void now
            self._ever_shown = True
            self._set_lifecycle(QWebEnginePage.LifecycleState.Active)
            self.refresh()
        else:
            # visibilityChanged(False) is emitted while the dock is being
            # hidden, before its children are: the view, and so the page, still
            # counts as visible, and Qt refuses to freeze a visible page (the
            # request is dropped without an error). Freeze on the next turn of
            # the event loop instead; showing again cancels it.
            self._freeze_retries = 0
            self._freeze_timer.start(0)

    def _freeze_if_hidden(self) -> None:
        if self._shown or self._shut_down or not self._page_loaded:
            return
        try:
            still_visible = self._page.isVisible()
        except RuntimeError:
            return
        if still_visible and self._freeze_retries < _FREEZE_RETRIES:
            self._freeze_retries += 1
            self._freeze_timer.start(_FREEZE_RETRY_MS)
            return
        self._set_lifecycle(QWebEnginePage.LifecycleState.Frozen)

    def _set_lifecycle(self, state: QWebEnginePage.LifecycleState) -> None:
        if not self._page_loaded and state != QWebEnginePage.LifecycleState.Active:
            return  # nothing loaded yet: keep it lazy
        try:
            self._page.setLifecycleState(state)
        except RuntimeError:
            pass

    def refresh(self) -> None:
        """Show the dashboard when the Watchdog answers, else the help page."""
        if not self._ever_shown:
            return  # first show loads; never before
        watcher = self._watcher
        if watcher is None or not watcher.available:
            self._show_info()
            return
        path = getattr(watcher, "panel_path", None)
        dark = self._theme.name == "dark"
        url = panel_url(watcher.base_url, path, dark)
        # Hidden and only the scheme differs: leave the frozen page alone.
        scheme_only = (not self._shown and path is not None and self._loaded_url
                       == panel_url(watcher.base_url, path, not dark))
        if self._loaded_url != url and not scheme_only:
            self._loaded_url = url
            self._loaded_declared = path is not None
            self._page_loaded = True
            self._view.load(QUrl(url))
        self._stack.setCurrentWidget(self._view)

    def _show_info(self) -> None:
        watcher = self._watcher
        enabled = watcher is not None and watcher.config.enabled
        bat = watcher.config.start_bat if watcher is not None else DEFAULT_START_BAT
        active = enabled or watcher is None
        note = getattr(watcher, "autostart_message", "") if watcher is not None else ""
        # The copy shipped with the application: nothing for the user to
        # install, register or locate, so the text must not send them there.
        uses_bundled = bool(getattr(watcher, "uses_bundled", False))
        has_bundle = bool(getattr(watcher, "bundled_available", False))
        diagnostics = ""
        if active and uses_bundled and note:
            text = f"Agent 監看尚未就緒\n\n{note}\n"
            diagnostics = str(getattr(watcher, "autostart_diagnostics", "") or "")
            if diagnostics:
                text += f"\n診斷資訊（回報問題時請一併附上）：\n{diagnostics}\n"
        elif active:
            text = "AI_Agent_Watchdog 未啟動\n\n"
            if bat:
                text += f"也可自行執行下列啟動檔，再按「重新偵測」：\n{bat}\n\n"
            elif not has_bundle:
                text += ("尚未指定 AI_Agent_Watchdog 的 start.bat。\n"
                         "請自行啟動 Watchdog 後按「重新偵測」，"
                         "或在設定值 watchdog/start_bat 記錄 start.bat 路徑。\n\n")
            if note:
                text += f"自動啟動：{note}\n"
            elif watcher is not None and getattr(watcher.config, "autostart", False):
                if has_bundle:
                    text += ("自動啟動：已開啟（優先使用已登記的 Watchdog，"
                             "否則啟動本程式內建的 Agent 監看）。\n")
                else:
                    text += "自動啟動：已開啟（依 Watchdog 登記的 install.json）。\n"
            else:
                text += "本程式不會自動啟動 Watchdog。\n"
        else:
            text = "Agent 監看已停用\n\n請在「檢視」選單勾選「啟用 Agent 監看」。"
        self._diagnostics = diagnostics
        self._copy_diagnostics.setVisible(bool(diagnostics))
        self._info_label.setText(text)
        self._redetect.setVisible(enabled)
        if self._loaded_url is not None:
            # The dashboard must not keep running behind the help page: stop
            # it and replace it with a blank document.
            self._view.stop()
            self._view.load(QUrl("about:blank"))
        self._loaded_url = None
        self._loaded_declared = False
        self._stack.setCurrentWidget(self._info)

    def shutdown(self) -> None:
        """Delete the page before the profile (idempotent; aboutToQuit-safe)."""
        if self._shut_down:
            return
        self._shut_down = True
        self._freeze_timer.stop()
        try:
            self._view.stop()
            self._view.setPage(None)
            if shiboken6.isValid(self._page):
                shiboken6.delete(self._page)
        except RuntimeError:
            pass

    @property
    def profile(self) -> QWebEngineProfile:
        return self._profile

    @property
    def view(self) -> QWebEngineView:
        return self._view
