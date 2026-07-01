"""Mermaid diagram workspace dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, QMimeData, QRectF, QSize, QTimer, QUrl, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .flowchart_canvas import FlowchartCanvas
from .flowchart_mermaid import (
    parse_flowchart,
    render_flowchart,
    visual_copy_from_source,
)
from .gantt_editor import GanttEditor
from .gantt_mermaid import parse_gantt, render_gantt
from .mermaid_format import format_mermaid_source
from .mermaid_render import build_preview_html
from .mermaid_templates import (
    SNIPPETS,
    TEMPLATES,
    default_template,
    snippet_by_id,
    template_by_id,
)
from .theme import (
    Theme,
    ThemeName,
    app_stylesheet,
    get_theme,
    svg_icon,
    toolbar_stylesheet,
)


def _looks_like_flowchart(source: str) -> bool:
    for line in source.splitlines():
        text = line.strip().lower()
        if not text or text.startswith("%%"):
            continue
        return text.startswith("flowchart ") or text.startswith("graph ")
    return False


class MermaidWorkspaceDialog(QDialog):
    """A split Mermaid source editor and live preview."""

    def __init__(
        self,
        source: str | None = None,
        theme_name: ThemeName = "light",
        parent=None,
        *,
        commit_label: str | None = None,
    ):
        super().__init__(parent)
        self._theme_name: ThemeName = "dark" if theme_name == "dark" else "light"
        self._theme = get_theme(self._theme_name)
        self._preview_theme_mode = "auto"
        self._last_svg = ""
        self._status_attempts = 0
        self._commit_label = commit_label
        self._syncing = False
        self._preview_visible = True
        self._preview_sizes = [520, 620]

        self.setWindowTitle("Mermaid 繪圖工作區")
        self.resize(1180, 760)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinMaxButtonsHint
        )

        self._template_combo = QComboBox()
        self._template_combo.setMinimumWidth(230)
        for template in TEMPLATES:
            self._template_combo.addItem(
                f"{template.group}：{template.name}", template.id
            )
        self._template_combo.activated.connect(self._apply_selected_template)

        self._snippet_combo = QComboBox()
        self._snippet_combo.setMinimumWidth(190)
        for snippet in SNIPPETS:
            self._snippet_combo.addItem(snippet.name, snippet.id)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("自動主題", "auto")
        self._theme_combo.addItem("淺色", "light")
        self._theme_combo.addItem("深色", "dark")
        self._theme_combo.activated.connect(self._on_preview_theme_changed)

        self._copy_source_btn = self._button(
            "copy", "複製 Mermaid 原始碼", self._copy_source
        )
        self._insert_snippet_btn = self._button(
            "workflow", "插入片段", self._insert_selected_snippet
        )
        self._format_btn = self._button("pencil", "格式化代碼", self._format_source)
        self._copy_svg_btn = self._button("copy", "複製 SVG 圖片", self._copy_svg)
        self._copy_png_btn = self._button("image", "複製 PNG 圖片", self._copy_png)
        self._export_svg_btn = self._button(
            "file-down", "匯出 SVG...", self._export_svg
        )
        self._export_png_btn = self._button(
            "file-down", "匯出 PNG...", self._export_png
        )
        self._toggle_preview_btn = self._button(
            "eye", "隱藏預覽", self._toggle_preview
        )

        toolbar = QWidget()
        toolbar.setObjectName("mermaidToolbar")
        toolbar.setFixedHeight(92)
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(4)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(6)
        controls_layout.addWidget(QLabel("範本"))
        controls_layout.addWidget(self._template_combo, stretch=3)
        controls_layout.addWidget(QLabel("片段"))
        controls_layout.addWidget(self._snippet_combo, stretch=2)
        controls_layout.addWidget(self._insert_snippet_btn)
        controls_layout.addWidget(self._format_btn)
        controls_layout.addWidget(QLabel("主題"))
        controls_layout.addWidget(self._theme_combo, stretch=1)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(6)
        actions_layout.addStretch()
        actions_layout.addWidget(self._copy_source_btn)
        actions_layout.addWidget(self._copy_svg_btn)
        actions_layout.addWidget(self._copy_png_btn)
        actions_layout.addWidget(self._export_svg_btn)
        actions_layout.addWidget(self._export_png_btn)
        actions_layout.addWidget(self._toggle_preview_btn)
        actions_layout.addStretch()

        toolbar_layout.addLayout(controls_layout)
        toolbar_layout.addLayout(actions_layout)

        self._source_editor = QPlainTextEdit()
        self._source_editor.setObjectName("mermaidSource")
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self._source_editor.setFont(font)
        self._source_editor.setTabStopDistance(
            QFontMetricsF(font).horizontalAdvance(" ") * 4
        )
        self._source_editor.textChanged.connect(self._on_source_changed)

        self._canvas = FlowchartCanvas()
        self._canvas.graph_changed.connect(self._on_canvas_graph_changed)
        self._canvas.visual_copy_requested.connect(self._create_visual_copy)
        self._gantt_editor = GanttEditor()
        self._gantt_editor.graph_changed.connect(self._on_gantt_chart_changed)
        self._visual_stack = QStackedWidget()
        self._visual_stack.addWidget(self._canvas)
        self._visual_stack.addWidget(self._gantt_editor)
        self._editor_tabs = QTabWidget()
        source_tab = QWidget()
        source_layout = QVBoxLayout(source_tab)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(self._source_editor)
        self._editor_tabs.addTab(source_tab, "原始碼")
        self._editor_tabs.addTab(self._visual_stack, "視覺化編輯")

        self._preview = QWebEngineView()
        self._preview.setObjectName("mermaidPreview")
        self._preview.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self._preview.page().loadFinished.connect(self._on_preview_loaded)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 6, 12)
        left_layout.addWidget(self._section_label("Mermaid 編輯器"))
        left_layout.addWidget(self._editor_tabs, stretch=1)

        self._preview_panel = QWidget()
        right_layout = QVBoxLayout(self._preview_panel)
        right_layout.setContentsMargins(6, 12, 12, 12)
        right_layout.addWidget(self._section_label("即時預覽"))
        right_layout.addWidget(self._preview, stretch=1)

        self._splitter = QSplitter()
        self._splitter.addWidget(left)
        self._splitter.addWidget(self._preview_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes(self._preview_sizes)

        self._status = QLabel("就緒")
        self._status.setObjectName("mermaidStatus")
        self._status.setWordWrap(True)

        self._button_box = self._build_button_box(commit_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(self._splitter, stretch=1)
        layout.addWidget(self._status)
        layout.addWidget(self._button_box)

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(250)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._render_preview)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(100)
        self._status_timer.timeout.connect(self._poll_render_status)

        self.apply_theme(self._theme)
        self._set_svg_actions_enabled(False)
        self.set_source(source if source is not None else default_template().source)

    def source(self) -> str:
        return self._source_editor.toPlainText()

    def set_source(self, source: str):
        self._syncing = True
        self._source_editor.setPlainText(source)
        self._syncing = False
        self._source_editor.document().setModified(False)
        self._render_preview()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._theme_name = theme.name
        self.setStyleSheet(
            app_stylesheet(theme)
            + toolbar_stylesheet(theme)
            + f"""
QWidget#mermaidToolbar {{
    background: {theme.surface};
    border-bottom: 1px solid {theme.border};
    min-height: 92px;
    max-height: 92px;
}}
QWidget#mermaidToolbar QPushButton {{
    min-width: 38px;
    max-width: 38px;
    min-height: 38px;
    max-height: 38px;
    padding: 0;
}}
QLabel#mermaidSection {{
    color: {theme.text_muted};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#mermaidStatus {{
    background: {theme.surface};
    border-top: 1px solid {theme.border};
    color: {theme.text_muted};
    padding: 8px 12px;
    min-height: 24px;
}}
QGraphicsView#flowchartCanvasView {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
}}
QLabel#flowchartCanvasMessage {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text_muted};
    padding: 10px 12px;
}}
QLabel#flowchartCanvasInfoBar {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text_muted};
    padding: 8px 12px;
    font-size: 12px;
}}
QWidget#flowchartProperties {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    border-radius: 6px;
}}
QWidget#ganttProperties {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    border-radius: 6px;
}}
QLabel#flowchartPropertiesTitle {{
    color: {theme.text};
    font-size: 13px;
    font-weight: 600;
}}
QLabel#flowchartPropertiesEmpty {{
    color: {theme.text_muted};
    padding-top: 6px;
}}
QPlainTextEdit#mermaidSource {{
    border-radius: 6px;
    padding: 10px 12px;
    line-height: 1.5;
}}
"""
        )
        self._canvas.apply_theme(theme)
        self._refresh_button_icons()
        self._render_preview()

    def _button(self, icon_name: str, text: str, slot) -> QPushButton:
        button = QPushButton()
        button.setProperty("iconName", icon_name)
        button.setFixedSize(38, 38)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(text)
        button.setAccessibleName(text)
        button.clicked.connect(slot)
        return button

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mermaidSection")
        return label

    def _build_button_box(self, commit_label: str | None) -> QDialogButtonBox:
        if commit_label:
            box = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            box.button(QDialogButtonBox.StandardButton.Ok).setText(commit_label)
            box.accepted.connect(self.accept)
            box.rejected.connect(self.reject)
            return box
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        return box

    def _refresh_button_icons(self):
        color = self._theme.text_muted
        disabled = self._theme.text_subtle
        for button in (
            self._copy_source_btn,
            self._insert_snippet_btn,
            self._format_btn,
            self._copy_svg_btn,
            self._copy_png_btn,
            self._export_svg_btn,
            self._export_png_btn,
            self._toggle_preview_btn,
        ):
            icon_name = button.property("iconName")
            button.setIcon(svg_icon(icon_name, color if button.isEnabled() else disabled, 18))

    def _set_svg_actions_enabled(self, enabled: bool):
        for button in (
            self._copy_svg_btn,
            self._copy_png_btn,
            self._export_svg_btn,
            self._export_png_btn,
        ):
            button.setEnabled(enabled)
        self._refresh_button_icons()

    def _apply_selected_template(self):
        template = template_by_id(self._template_combo.currentData())
        if template is None:
            return
        if self._source_editor.document().isModified():
            answer = QMessageBox.question(
                self,
                "替換原始碼",
                "確定要用此範本替換目前的 Mermaid 原始碼嗎？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.set_source(template.source)

    def _on_source_changed(self):
        if not self._syncing:
            self._preview_timer.start()

    def _render_preview(self, sync_canvas: bool = True):
        self._status_timer.stop()
        self._last_svg = ""
        self._set_svg_actions_enabled(False)
        self._status.setText("Rendering...")
        if sync_canvas and not self._syncing:
            self._sync_canvas_from_source()
        html = build_preview_html(self.source(), self._effective_preview_theme())
        self._preview.setHtml(html, QUrl.fromLocalFile(str(Path.cwd()) + "/"))

    def _sync_canvas_from_source(self):
        source = self.source()
        flowchart_result = parse_flowchart(source)
        if flowchart_result.supported:
            self._canvas.set_graph(flowchart_result.require_graph())
            self._visual_stack.setCurrentWidget(self._canvas)
            return

        gantt_result = parse_gantt(source)
        if gantt_result.supported:
            self._gantt_editor.set_chart(gantt_result.require_chart())
            self._visual_stack.setCurrentWidget(self._gantt_editor)
            return

        self._visual_stack.setCurrentWidget(self._canvas)
        self._canvas.set_unsupported(
            "視覺化模式目前僅支援簡易流程圖 (flowchart TD/LR)。\n"
            f"{flowchart_result.reason}\n\nGantt 圖表可視覺化編輯；其他 Mermaid "
            "圖表請使用原始碼模式。",
            can_create_copy=_looks_like_flowchart(source),
        )

    def _on_canvas_graph_changed(self, graph):
        if self._syncing:
            return
        text = render_flowchart(graph)
        if text == self.source():
            return
        self._syncing = True
        try:
            cursor_pos = self._source_editor.textCursor().position()
            self._source_editor.setPlainText(text)
            cursor = self._source_editor.textCursor()
            cursor.setPosition(max(0, min(len(text), cursor_pos)))
            self._source_editor.setTextCursor(cursor)
            self._source_editor.document().setModified(True)
        finally:
            self._syncing = False
        self._render_preview(sync_canvas=False)

    def _on_gantt_chart_changed(self, chart):
        if self._syncing:
            return
        text = render_gantt(chart)
        if text == self.source():
            return
        self._syncing = True
        try:
            cursor_pos = self._source_editor.textCursor().position()
            self._source_editor.setPlainText(text)
            cursor = self._source_editor.textCursor()
            cursor.setPosition(max(0, min(len(text), cursor_pos)))
            self._source_editor.setTextCursor(cursor)
            self._source_editor.document().setModified(True)
        finally:
            self._syncing = False
        self._render_preview(sync_canvas=False)

    def _create_visual_copy(self):
        answer = QMessageBox.question(
            self,
            "建立視覺化複本",
            "是否根據此代碼中支援的節點與連線建立簡化版的視覺化複本？\n\n注意：在您點擊「更新 Markdown」或「插入圖表」之前，原文件內容不會被修改。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = visual_copy_from_source(self.source())
        if not result.supported:
            QMessageBox.warning(
                self,
                "建立視覺化複本",
                result.reason or "此 Mermaid 原始碼無法被轉換。",
            )
            return
        graph = result.require_graph()
        text = render_flowchart(graph)
        self._syncing = True
        try:
            self._source_editor.setPlainText(text)
            self._source_editor.document().setModified(True)
        finally:
            self._syncing = False
        self._canvas.set_graph(graph)
        self._visual_stack.setCurrentWidget(self._canvas)
        self._editor_tabs.setCurrentWidget(self._visual_stack)
        self._status.setText("已建立視覺化複本。更新 Markdown 前請先確認內容。")
        self._render_preview(sync_canvas=False)

    def _toggle_preview(self):
        if self._preview_panel.isVisible():
            self._preview_sizes = self._splitter.sizes()
            total = max(1, sum(self._preview_sizes))
            self._preview_panel.hide()
            self._splitter.setSizes([total, 0])
            self._toggle_preview_btn.setToolTip("顯示預覽")
            self._toggle_preview_btn.setAccessibleName("顯示預覽")
            return
        self._preview_panel.show()
        self._splitter.setSizes(self._preview_sizes or [520, 620])
        self._toggle_preview_btn.setToolTip("隱藏預覽")
        self._toggle_preview_btn.setAccessibleName("隱藏預覽")

    def _effective_preview_theme(self) -> ThemeName:
        mode = self._preview_theme_mode
        if mode == "dark":
            return "dark"
        if mode == "light":
            return "light"
        return self._theme_name

    def _on_preview_theme_changed(self):
        self._preview_theme_mode = str(self._theme_combo.currentData() or "auto")
        self._render_preview()

    def _insert_selected_snippet(self):
        snippet = snippet_by_id(str(self._snippet_combo.currentData() or ""))
        if snippet is None:
            return
        cursor = self._source_editor.textCursor()
        if cursor.position() > 0:
            cursor.insertText("\n")
        cursor.insertText(snippet.source)
        if not snippet.source.endswith("\n"):
            cursor.insertText("\n")
        self._source_editor.setTextCursor(cursor)
        self._source_editor.setFocus()

    def _format_source(self):
        cursor_pos = self._source_editor.textCursor().position()
        formatted = format_mermaid_source(self.source())
        self._source_editor.setPlainText(formatted)
        cursor = self._source_editor.textCursor()
        cursor.setPosition(max(0, min(len(formatted), cursor_pos)))
        self._source_editor.setTextCursor(cursor)
        self._source_editor.document().setModified(True)

    def _on_preview_loaded(self, ok: bool):
        if not ok:
            self._status.setText("預覽載入失敗。")
            return
        self._status_attempts = 0
        self._status_timer.start()

    def _poll_render_status(self):
        self._status_attempts += 1
        if self._status_attempts > 120:
            self._status_timer.stop()
            self._status.setText("Mermaid 轉譯逾時。")
            return
        self._preview.page().runJavaScript(
            "window.__mermaidStatus", self._on_render_status
        )

    def _on_render_status(self, status):
        if not isinstance(status, dict) or not status.get("ready"):
            return
        self._status_timer.stop()
        if status.get("ok"):
            self._last_svg = str(status.get("svg") or "")
            self._status.setText("已轉譯")
            self._set_svg_actions_enabled(bool(self._last_svg))
            return
        error = str(status.get("error") or "").strip()
        self._last_svg = ""
        self._set_svg_actions_enabled(False)
        self._status.setText(error if error else "請輸入 Mermaid 原始碼。")

    def _copy_source(self):
        QApplication.clipboard().setText(self.source())
        self._status.setText("已複製 Mermaid 原始碼。")

    def _copy_svg(self):
        if not self._last_svg:
            return
        mime = QMimeData()
        mime.setText(self._last_svg)
        mime.setData("image/svg+xml", QByteArray(self._last_svg.encode("utf-8")))
        QApplication.clipboard().setMimeData(mime)
        self._status.setText("已複製 SVG 圖片。")

    def _copy_png(self):
        image = self._svg_to_image()
        if image is None:
            return
        QApplication.clipboard().setImage(image)
        self._status.setText("已複製 PNG 圖片。")

    def _export_svg(self):
        if not self._last_svg:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出 SVG", "diagram.svg", "SVG 圖片 (*.svg)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self._last_svg, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "匯出失敗", str(exc))
            return
        self._status.setText(f"SVG 圖片已匯出至 {path}。")

    def _export_png(self):
        image = self._svg_to_image()
        if image is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出 PNG", "diagram.png", "PNG 圖片 (*.png)"
        )
        if not path:
            return
        if not image.save(path, "PNG"):
            QMessageBox.warning(self, "匯出失敗", f"無法儲存檔案 {path}。")
            return
        self._status.setText(f"PNG 圖片已匯出至 {path}。")

    def _svg_to_image(self) -> QImage | None:
        if not self._last_svg:
            return None
        data = QByteArray(self._last_svg.encode("utf-8"))
        renderer = QSvgRenderer(data)
        if not renderer.isValid():
            QMessageBox.warning(self, "Image Failed", "The rendered SVG is invalid.")
            return None

        rect = renderer.viewBoxF()
        default_size = renderer.defaultSize()
        width = rect.width() if not rect.isEmpty() else default_size.width()
        height = rect.height() if not rect.isEmpty() else default_size.height()
        width = max(1, min(4096, int(width or 960)))
        height = max(1, min(4096, int(height or 540)))
        scale = 2

        image = QImage(
            width * scale,
            height * scale,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        painter.scale(scale, scale)
        renderer.render(painter, QRectF(0, 0, width, height))
        painter.end()
        return image
