"""Mermaid diagram workspace dialog."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, QMimeData, QRectF, QSize, QTimer, QUrl
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
    QVBoxLayout,
    QWidget,
)

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

        self.setWindowTitle("Mermaid Workspace")
        self.resize(1180, 760)

        self._template_combo = QComboBox()
        self._template_combo.setMinimumWidth(230)
        for template in TEMPLATES:
            self._template_combo.addItem(
                f"{template.group}: {template.name}", template.id
            )
        self._template_combo.activated.connect(self._apply_selected_template)

        self._snippet_combo = QComboBox()
        self._snippet_combo.setMinimumWidth(190)
        for snippet in SNIPPETS:
            self._snippet_combo.addItem(snippet.name, snippet.id)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem("Auto theme", "auto")
        self._theme_combo.addItem("Light", "light")
        self._theme_combo.addItem("Dark", "dark")
        self._theme_combo.activated.connect(self._on_preview_theme_changed)

        self._copy_source_btn = self._button(
            "copy", "Copy Mermaid", self._copy_source
        )
        self._insert_snippet_btn = self._button(
            "workflow", "Insert Snippet", self._insert_selected_snippet
        )
        self._format_btn = self._button("pencil", "Format", self._format_source)
        self._copy_svg_btn = self._button("copy", "Copy SVG", self._copy_svg)
        self._copy_png_btn = self._button("image", "Copy PNG", self._copy_png)
        self._export_svg_btn = self._button(
            "file-down", "Export SVG", self._export_svg
        )
        self._export_png_btn = self._button(
            "file-down", "Export PNG", self._export_png
        )

        toolbar = QWidget()
        toolbar.setObjectName("mermaidToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 0, 8, 0)
        toolbar_layout.setSpacing(6)
        toolbar_layout.addWidget(QLabel("Template"))
        toolbar_layout.addWidget(self._template_combo)
        toolbar_layout.addWidget(QLabel("Snippet"))
        toolbar_layout.addWidget(self._snippet_combo)
        toolbar_layout.addWidget(self._insert_snippet_btn)
        toolbar_layout.addWidget(self._format_btn)
        toolbar_layout.addWidget(QLabel("Theme"))
        toolbar_layout.addWidget(self._theme_combo)
        toolbar_layout.addSpacing(8)
        toolbar_layout.addWidget(self._copy_source_btn)
        toolbar_layout.addWidget(self._copy_svg_btn)
        toolbar_layout.addWidget(self._copy_png_btn)
        toolbar_layout.addWidget(self._export_svg_btn)
        toolbar_layout.addWidget(self._export_png_btn)
        toolbar_layout.addStretch()

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

        self._preview = QWebEngineView()
        self._preview.setObjectName("mermaidPreview")
        self._preview.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self._preview.page().loadFinished.connect(self._on_preview_loaded)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 6, 12)
        left_layout.addWidget(self._section_label("Mermaid Source"))
        left_layout.addWidget(self._source_editor)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 12, 12, 12)
        right_layout.addWidget(self._section_label("Live Preview"))
        right_layout.addWidget(self._preview)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 620])

        self._status = QLabel("Ready")
        self._status.setObjectName("mermaidStatus")
        self._status.setWordWrap(True)

        self._button_box = self._build_button_box(commit_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(splitter, stretch=1)
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
        self._source_editor.setPlainText(source)
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
    min-height: 48px;
    max-height: 48px;
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
QPlainTextEdit#mermaidSource {{
    border-radius: 6px;
    padding: 10px 12px;
    line-height: 1.5;
}}
"""
        )
        self._refresh_button_icons()
        self._render_preview()

    def _button(self, icon_name: str, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("iconName", icon_name)
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
                "Replace Source",
                "Replace the current Mermaid source with this template?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.set_source(template.source)

    def _on_source_changed(self):
        self._preview_timer.start()

    def _render_preview(self):
        self._status_timer.stop()
        self._last_svg = ""
        self._set_svg_actions_enabled(False)
        self._status.setText("Rendering...")
        html = build_preview_html(self.source(), self._effective_preview_theme())
        self._preview.setHtml(html, QUrl.fromLocalFile(str(Path.cwd()) + "/"))

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
            self._status.setText("Preview failed to load.")
            return
        self._status_attempts = 0
        self._status_timer.start()

    def _poll_render_status(self):
        self._status_attempts += 1
        if self._status_attempts > 120:
            self._status_timer.stop()
            self._status.setText("Mermaid render timed out.")
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
            self._status.setText("Rendered")
            self._set_svg_actions_enabled(bool(self._last_svg))
            return
        error = str(status.get("error") or "").strip()
        self._last_svg = ""
        self._set_svg_actions_enabled(False)
        self._status.setText(error if error else "Enter Mermaid source.")

    def _copy_source(self):
        QApplication.clipboard().setText(self.source())
        self._status.setText("Mermaid source copied.")

    def _copy_svg(self):
        if not self._last_svg:
            return
        mime = QMimeData()
        mime.setText(self._last_svg)
        mime.setData("image/svg+xml", QByteArray(self._last_svg.encode("utf-8")))
        QApplication.clipboard().setMimeData(mime)
        self._status.setText("SVG copied.")

    def _copy_png(self):
        image = self._svg_to_image()
        if image is None:
            return
        QApplication.clipboard().setImage(image)
        self._status.setText("PNG copied.")

    def _export_svg(self):
        if not self._last_svg:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SVG", "diagram.svg", "SVG images (*.svg)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self._last_svg, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", str(exc))
            return
        self._status.setText(f"SVG exported to {path}.")

    def _export_png(self):
        image = self._svg_to_image()
        if image is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", "diagram.png", "PNG images (*.png)"
        )
        if not path:
            return
        if not image.save(path, "PNG"):
            QMessageBox.warning(self, "Export Failed", f"Could not save {path}.")
            return
        self._status.setText(f"PNG exported to {path}.")

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
