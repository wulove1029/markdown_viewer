"""Plain-text Markdown editor shown when edit mode is active."""

from pathlib import Path

from PySide6.QtCore import QRect, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QCompleter,
    QPlainTextDocumentLayout,
    QPlainTextEdit,
    QWidget,
)

from .attachments import import_attachment_file, markdown_attachment_link
from .editor_overlays import SelectionFormatBar, SlashCommandPopup
from .editor_input import auto_pair_edit, backspace_pair_edit, tab_edit
from .format_actions import (
    active_format_actions,
    apply_format_action,
    apply_text_edit,
)
from .format_commands import filter_commands
from .image_paste import (
    import_image_file,
    is_image_file,
    markdown_image_link,
    save_clipboard_image,
)
from .md_highlighter import MarkdownHighlighter
from .smart_writing import (
    active_slash_query_on_line,
    linkify_paste_edit,
    smart_enter_edit_on_line,
)
from .text_positions import py_to_qt_position, qt_to_py_position
from .theme import LIGHT, Theme
from .wikilink_completion import active_query, filter_completions


# QTextCursor.selectedText() encodes line breaks as U+2029, not a newline.
_PARAGRAPH_SEP = chr(0x2029)


class _LineNumberArea(QWidget):
    """Gutter widget painting line numbers for its EditorView parent."""

    def __init__(self, editor: "EditorView"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        self._editor.paint_line_numbers(event)


class EditorView(QPlainTextEdit):
    modified_changed = Signal(bool)
    image_status = Signal(str)  # user-facing status bar message
    translate_requested = Signal(str)  # selected text to translate
    format_action_requested = Signal(str)  # actions needing MainWindow UI
    format_context_changed = Signal(object)  # set[str] for toolbar active state
    resource_inserted = Signal(str)  # Markdown link stored for recent resources

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self.setFont(font)
        self.setTabStopDistance(QFontMetricsF(font).horizontalAdvance(" ") * 4)
        self.document().modificationChanged.connect(self.modified_changed)
        self._highlighter = MarkdownHighlighter(self.document(), LIGHT)
        # QSyntaxHighlighter(document) is parented to that document by Qt.
        # The editor swaps independent QTextDocuments between tabs, so keep
        # the highlighter owned by the widget instead; otherwise replacing the
        # initial document destroys the highlighter with it.
        self._highlighter.setParent(self)
        self._parking_document = QTextDocument()
        self._parking_document.setDocumentLayout(
            QPlainTextDocumentLayout(self._parking_document)
        )
        self._parking_document.setDefaultFont(self.font())
        self._parking_document.setUndoRedoEnabled(True)
        self._theme: Theme = LIGHT
        self._plain_text_mode = False
        self._markdown_services_suspended = False
        self._wikilink_candidates: list[str] = []
        self._document_path: str | None = None
        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.activated[str].connect(self._insert_wikilink_completion)
        self._line_number_area = _LineNumberArea(self)
        self._slash_popup = SlashCommandPopup(self.viewport())
        self._slash_popup.command_activated.connect(self._accept_slash_command)
        self._selection_format_bar = SelectionFormatBar(self.viewport())
        self._selection_format_bar.action_triggered.connect(
            self._apply_selection_format
        )
        self._slash_dismissed_start: int | None = None
        self._selection_toolbar_from_mouse = False
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._line_number_area.update)
        self.cursorPositionChanged.connect(self._editor_context_changed)
        self.textChanged.connect(self._editor_context_changed)
        self.verticalScrollBar().valueChanged.connect(self._hide_editor_overlays)
        self._update_line_number_area_width()
        # Establish one invariant from construction onward: when no real tab
        # buffer is active, QPlainTextEdit owns only the permanent parking
        # document.  Keeping Qt's implicit default document around makes
        # compatibility paths mistake it for a tab-owned buffer, then retain
        # a Python wrapper after Qt destroys it during a document swap.
        self.release_buffer_document()

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        # selectedText() joins wrapped lines with U+2029; restore real newlines
        # so the provider sees the paragraph the user actually highlighted.
        selection = (
            self.textCursor().selectedText().replace(_PARAGRAPH_SEP, "\n").strip()
        )
        if selection:
            menu.addSeparator()
            action = menu.addAction("翻譯選取內容")
            action.triggered.connect(
                lambda _checked=False, text=selection: (
                    self.translate_requested.emit(text)
                )
            )
        menu.exec(event.globalPos())

    def set_content(self, text: str):
        self._completer.popup().hide()
        self._hide_editor_overlays()
        self._slash_dismissed_start = None
        self.setPlainText(text)
        self.document().setModified(False)

    def create_buffer_document(self, text: str) -> QTextDocument:
        """Create an independent plain-text document with its own undo stack."""
        # Per-tab documents are intentionally not parented to EditorView.
        # Their tab-state entry owns them; dropping that entry then releases
        # the document and its undo history instead of accumulating QObject
        # children for every tab ever opened.
        document = QTextDocument()
        document.setDocumentLayout(QPlainTextDocumentLayout(document))
        document.setDefaultFont(self.font())
        document.setUndoRedoEnabled(True)
        document.setPlainText(text)
        document.setModified(False)
        return document

    def use_buffer_document(
        self,
        document: QTextDocument,
        *,
        plain_text_mode: bool,
        document_path: str | Path | None,
    ) -> None:
        """Swap to a per-tab QTextDocument while keeping editor services wired."""
        self._completer.popup().hide()
        self._hide_editor_overlays()
        self._slash_dismissed_start = None
        old_document = self.document()
        if old_document is not document:
            try:
                old_document.modificationChanged.disconnect(self.modified_changed)
            except (RuntimeError, TypeError):
                pass
            self._highlighter.setDocument(None)
            self.setDocument(document)
            document.modificationChanged.connect(self.modified_changed)
        self._plain_text_mode = bool(plain_text_mode)
        if self._plain_text_mode or self._markdown_services_suspended:
            self._highlighter.setDocument(None)
        else:
            self._highlighter.setDocument(document)
            self._highlighter.set_theme(self._theme)
        self.set_document_path(document_path)
        self._update_line_number_area_width()
        self._emit_format_context()

    def release_buffer_document(self) -> None:
        """Detach the active tab document before its final Python reference drops."""

        if self.document() is self._parking_document:
            return
        self._parking_document.clear()
        self._parking_document.setModified(False)
        self.use_buffer_document(
            self._parking_document,
            plain_text_mode=False,
            document_path=None,
        )

    def set_document_path(self, path: str | Path | None) -> None:
        """Tell the editor which file it is editing (for asset placement)."""
        self._document_path = str(path) if path else None

    def document_path(self) -> str | None:
        """Path of the file being edited, or None for an unsaved buffer."""
        return self._document_path

    def set_plain_text_mode(self, enabled: bool) -> None:
        """Plain .txt editing: no Markdown highlight, wikilinks, image links."""
        enabled = bool(enabled)
        if enabled == self._plain_text_mode:
            return
        self._plain_text_mode = enabled
        if enabled or self._markdown_services_suspended:
            self._completer.popup().hide()
            self._hide_editor_overlays()
            self._highlighter.setDocument(None)
        else:
            self._highlighter.setDocument(self.document())
            self._highlighter.set_theme(self._theme)
        self._emit_format_context()

    def set_markdown_services_suspended(self, suspended: bool) -> None:
        """Pause hidden source-editor work while WebEngine owns editing.

        The QTextDocument remains the durable shadow buffer, but syntax
        highlighting, slash completion and format-context calculations do
        not need to run for an editor widget that is not visible.
        """
        suspended = bool(suspended)
        if suspended == self._markdown_services_suspended:
            return
        self._markdown_services_suspended = suspended
        self._completer.popup().hide()
        self._hide_editor_overlays()
        if suspended or self._plain_text_mode:
            self._highlighter.setDocument(None)
            self.format_context_changed.emit(set())
        else:
            self._highlighter.setDocument(self.document())
            self._highlighter.set_theme(self._theme)
            self._emit_format_context()

    # ---------------- line number gutter ----------------
    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_area_width(self, *_args) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
            self._hide_editor_overlays()
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width()

    def resizeEvent(self, event):  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )
        if self._slash_popup.isVisible():
            self._refresh_slash_commands()
        if self._selection_format_bar.isVisible():
            self._selection_format_bar.hide()

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(self._theme.window))
        muted = QColor(self._theme.text_subtle)
        current = QColor(self._theme.text)
        current_block = self.textCursor().blockNumber()
        width = self._line_number_area.width() - 10
        height = self.fontMetrics().height()
        # Stylesheet padding offsets the viewport from the gutter's origin;
        # translate block (viewport) coordinates into gutter coordinates.
        voffset = (
            self.viewport().geometry().top() - self._line_number_area.geometry().top()
        )
        block = self.firstVisibleBlock()
        top = voffset + round(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                number = block.blockNumber()
                painter.setPen(current if number == current_block else muted)
                painter.drawText(
                    0,
                    top,
                    width,
                    height,
                    Qt.AlignmentFlag.AlignRight,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
        painter.end()

    # ---------------- image paste / drag-and-drop ----------------
    def _mime_has_image(self, mime) -> bool:
        """True when *mime* holds raster image data or all-image file URLs."""
        if self._plain_text_mode:
            # Plain text has no image links; let Qt paste/drop literal text.
            return False
        if mime.hasImage():
            return True
        if mime.hasUrls():
            urls = mime.urls()
            return bool(urls) and all(
                u.isLocalFile() and is_image_file(u.toLocalFile()) for u in urls
            )
        return False

    def _insert_image_mime(self, mime) -> None:
        if not self._document_path:
            self.image_status.emit("請先儲存文件才能貼入圖片")
            return
        links: list[str] = []
        created: list[Path] = []
        try:
            if mime.hasImage():
                qimage = mime.imageData()
                if not isinstance(qimage, QImage):
                    qimage = QImage(qimage)
                rel = save_clipboard_image(qimage, self._document_path)
                if rel is None:
                    self.image_status.emit("圖片儲存失敗，請重試")
                    return
                created.append(Path(self._document_path).parent / rel)
                links.append(markdown_image_link(rel))
            elif mime.hasUrls():
                for url in mime.urls():
                    source = Path(url.toLocalFile()).resolve(strict=True)
                    rel = import_image_file(source, self._document_path)
                    target = (Path(self._document_path).parent / rel).resolve()
                    if target != source:
                        created.append(target)
                    links.append(markdown_image_link(rel))
        except (OSError, ValueError) as exc:
            for target in reversed(created):
                try:
                    target.unlink()
                except OSError:
                    pass
            self.image_status.emit(f"圖片匯入失敗：{exc}")
            return
        if links:
            self.insertPlainText("\n".join(links))
            for link in links:
                self.resource_inserted.emit(link)

    def _local_resource_files(self, mime) -> list[str]:
        if self._plain_text_mode or not mime.hasUrls():
            return []
        urls = mime.urls()
        if not urls or not all(url.isLocalFile() for url in urls):
            return []
        paths = [url.toLocalFile() for url in urls]
        return paths if all(Path(path).is_file() for path in paths) else []

    def _insert_resource_files(self, paths: list[str]) -> None:
        if not self._document_path:
            self.image_status.emit("請先儲存文件才能加入附件")
            return
        links: list[str] = []
        created: list[Path] = []
        try:
            for path in paths:
                source = Path(path).resolve(strict=True)
                if is_image_file(path):
                    relative = import_image_file(source, self._document_path)
                    links.append(markdown_image_link(relative))
                else:
                    relative = import_attachment_file(source, self._document_path)
                    links.append(markdown_attachment_link(relative))
                target = (Path(self._document_path).parent / relative).resolve()
                if target != source:
                    created.append(target)
        except (OSError, ValueError) as exc:
            for target in reversed(created):
                try:
                    target.unlink()
                except OSError:
                    pass
            self.image_status.emit(f"附件匯入失敗：{exc}")
            return
        if links:
            self.insertPlainText("\n".join(links))
            for link in links:
                self.resource_inserted.emit(link)

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 (Qt override)
        if self._mime_has_image(source) or self._local_resource_files(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 (Qt override)
        if self._mime_has_image(source):
            self._insert_image_mime(source)
            return
        resource_files = self._local_resource_files(source)
        if resource_files:
            self._insert_resource_files(resource_files)
            return
        if not self._plain_text_mode and source.hasText():
            cursor = self.textCursor()
            text = self.toPlainText()
            edit = linkify_paste_edit(
                text,
                qt_to_py_position(text, cursor.selectionStart()),
                qt_to_py_position(text, cursor.selectionEnd()),
                source.text(),
            )
            if edit is not None:
                apply_text_edit(self, edit)
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):  # noqa: N802 (Qt override)
        if self._mime_has_image(event.mimeData()) or self._local_resource_files(
            event.mimeData()
        ):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802 (Qt override)
        if self._mime_has_image(event.mimeData()) or self._local_resource_files(
            event.mimeData()
        ):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if self._mime_has_image(mime):
            self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
            self._insert_image_mime(mime)
            event.acceptProposedAction()
        elif resource_files := self._local_resource_files(mime):
            self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
            self._insert_resource_files(resource_files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def set_wikilink_candidates(self, candidates) -> None:
        self._wikilink_candidates = list(candidates)
        if self.isVisible() and self.hasFocus():
            self._show_wikilink_completions()

    def _query_before_cursor(self) -> str | None:
        cursor = self.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.StartOfLine,
            QTextCursor.MoveMode.KeepAnchor,
        )
        return active_query(cursor.selectedText())

    def _show_wikilink_completions(self) -> None:
        if self._plain_text_mode:
            return
        query = self._query_before_cursor()
        matches = (
            filter_completions(self._wikilink_candidates, query)
            if query is not None
            else []
        )
        popup = self._completer.popup()
        if not matches:
            popup.hide()
            return

        self._slash_popup.hide()
        self._completion_model.setStringList(matches)
        self._completer.setCompletionPrefix("")
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(260, min(520, popup.sizeHintForColumn(0) + 30)))
        self._completer.complete(rect)

    def _insert_wikilink_completion(self, completion: str) -> None:
        query = self._query_before_cursor()
        if query is None:
            return
        cursor = self.textCursor()
        if query:
            # QTextCursor's PreviousCharacter moves by grapheme cluster,
            # while Python len() counts code points.  Select the typed query
            # by its exact UTF-16 width so combining marks are not overrun.
            query_width = py_to_qt_position(query, len(query))
            cursor.setPosition(
                max(cursor.block().position(), cursor.position() - query_width),
                QTextCursor.MoveMode.KeepAnchor,
            )
        cursor.insertText(f"{completion}]]")
        self.setTextCursor(cursor)
        self._completer.popup().hide()

    # ---------------- cursor-local Markdown commands ----------------
    def _hide_editor_overlays(self, *_args) -> None:
        self._slash_popup.hide()
        self._selection_format_bar.hide()

    def _emit_format_context(self) -> None:
        if self._plain_text_mode or self._markdown_services_suspended:
            self.format_context_changed.emit(set())
            return
        cursor = self.textCursor()
        document = self.document()
        start_block = document.findBlock(cursor.selectionStart())
        end_block = document.findBlock(cursor.selectionEnd())
        # Active state is only a visual hint.  Avoid materialising a large
        # multi-block selection on every cursor signal; commands themselves
        # still use the complete document when the user invokes one.
        if start_block.blockNumber() != end_block.blockNumber():
            self.format_context_changed.emit(set())
            return
        text = start_block.text()
        block_position = start_block.position()
        self.format_context_changed.emit(
            active_format_actions(
                text,
                qt_to_py_position(
                    text, cursor.selectionStart() - block_position
                ),
                qt_to_py_position(
                    text, cursor.selectionEnd() - block_position
                ),
            )
        )

    def _editor_context_changed(self, *_args) -> None:
        if self._markdown_services_suspended:
            return
        self._emit_format_context()
        if not self._selection_toolbar_from_mouse:
            self._selection_format_bar.hide()
        self._refresh_slash_commands()

    def _slash_context(self):
        """Return the current block-local slash query and its Qt range."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return None
        block = cursor.block()
        line = block.text()
        local_qt = cursor.position() - block.position()
        local_py = qt_to_py_position(line, local_qt)
        context = active_slash_query_on_line(
            line,
            local_py,
            in_fenced_code=block.userState() > 0,
        )
        if context is None:
            return None
        return context, block.position(), cursor.position()

    def _apply_block_text_edit(self, line: str, block_position: int, edit) -> None:
        """Apply a block-local TextEdit without copying the full document."""
        updated = line[: edit.start] + edit.replacement + line[edit.end :]
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(
            block_position + py_to_qt_position(line, edit.start)
        )
        cursor.setPosition(
            block_position + py_to_qt_position(line, edit.end),
            QTextCursor.MoveMode.KeepAnchor,
        )
        cursor.insertText(edit.replacement)
        cursor.endEditBlock()
        cursor.setPosition(
            block_position + py_to_qt_position(updated, edit.sel_start)
        )
        cursor.setPosition(
            block_position + py_to_qt_position(updated, edit.sel_end),
            QTextCursor.MoveMode.KeepAnchor,
        )
        self.setTextCursor(cursor)

    def _refresh_slash_commands(self) -> None:
        if self._plain_text_mode or self._completer.popup().isVisible():
            self._slash_popup.hide()
            return
        slash = self._slash_context()
        if slash is None:
            self._slash_dismissed_start = None
            self._slash_popup.hide()
            return
        context, start_qt, _end_qt = slash
        if self._slash_dismissed_start == start_qt:
            self._slash_popup.hide()
            return
        self._selection_format_bar.hide()
        self._slash_popup.set_commands(
            filter_commands("slash", context.query), context.query
        )
        self._slash_popup.show_near(self.cursorRect())

    def _accept_slash_command(self, action: str) -> None:
        slash = self._slash_context()
        if slash is None:
            self._slash_popup.hide()
            return
        _context, start_qt, end_qt = slash
        self._slash_popup.hide()
        self._slash_dismissed_start = start_qt
        cursor = self.textCursor()
        cursor.setPosition(start_qt)
        cursor.setPosition(
            end_qt,
            QTextCursor.MoveMode.KeepAnchor,
        )
        self.setTextCursor(cursor)

        # File-picker actions must keep /query intact when the user cancels.
        # MainWindow inserts the image at the current selection on success.
        if action in {"image", "attachment", "template", "recent_resource"}:
            self.format_action_requested.emit(action)
            self.setFocus()
            self._refresh_slash_commands()
            return

        outer = self.textCursor()
        outer.beginEditBlock()
        outer.insertText("")
        self.setTextCursor(outer)
        apply_format_action(self, action, edit_block=False)
        outer.endEditBlock()
        self._slash_dismissed_start = None
        self.setFocus()
        self._refresh_slash_commands()

    def _apply_selection_format(self, action: str) -> None:
        self._selection_toolbar_from_mouse = False
        self._selection_format_bar.hide()
        apply_format_action(self, action)
        self.setFocus()

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        self._selection_toolbar_from_mouse = False
        self._selection_format_bar.hide()
        self._slash_popup.hide()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt override)
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._plain_text_mode:
            return
        cursor = self.textCursor()
        selected = cursor.selectedText()
        if (
            not selected.strip()
            or _PARAGRAPH_SEP in selected
            or self._completer.popup().isVisible()
            or self._slash_popup.isVisible()
        ):
            self._selection_toolbar_from_mouse = False
            self._selection_format_bar.hide()
            return
        self._selection_toolbar_from_mouse = True
        self._selection_format_bar.show_for_selection(
            self, cursor.selectionStart(), cursor.selectionEnd()
        )

    def focusOutEvent(self, event):  # noqa: N802 (Qt override)
        self._selection_toolbar_from_mouse = False
        self._selection_format_bar.hide()
        self._slash_popup.hide()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        popup = self._completer.popup()
        key = event.key()
        modifiers = event.modifiers()
        self._selection_toolbar_from_mouse = False
        self._selection_format_bar.hide()

        if self._slash_popup.isVisible():
            if (
                key == Qt.Key.Key_Escape
                and modifiers == Qt.KeyboardModifier.NoModifier
            ):
                slash = self._slash_context()
                self._slash_dismissed_start = (
                    slash[1] if slash is not None else None
                )
                self._slash_popup.hide()
                event.accept()
                return
            if modifiers == Qt.KeyboardModifier.NoModifier and key in (
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
            ):
                self._slash_popup.move_selection(
                    -1 if key == Qt.Key.Key_Up else 1
                )
                event.accept()
                return
            if modifiers == Qt.KeyboardModifier.NoModifier and key in (
                Qt.Key.Key_Enter,
                Qt.Key.Key_Return,
                Qt.Key.Key_Tab,
            ):
                if self._slash_popup.activate_current():
                    event.accept()
                    return
        if popup.isVisible() and key == Qt.Key.Key_Escape:
            popup.hide()
            event.accept()
            return
        if popup.isVisible() and key in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
        ):
            event.ignore()
            return

        markdown_mode = not self._plain_text_mode
        cursor = self.textCursor()
        if (
            markdown_mode
            and key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
            and modifiers
            in (
                Qt.KeyboardModifier.NoModifier,
                Qt.KeyboardModifier.ShiftModifier,
            )
        ):
            selection_start = cursor.selectionStart()
            selection_end = cursor.selectionEnd()
            first_block = self.document().findBlock(selection_start)
            last_block = self.document().findBlock(selection_end)
            if (
                selection_end > selection_start
                and last_block.isValid()
                and selection_end == last_block.position()
            ):
                last_block = self.document().findBlock(selection_end - 1)
            range_start = first_block.position()
            range_end = last_block.position() + max(0, last_block.length() - 1)
            block_cursor = QTextCursor(self.document())
            block_cursor.setPosition(range_start)
            block_cursor.setPosition(
                range_end, QTextCursor.MoveMode.KeepAnchor
            )
            text = block_cursor.selectedText().replace(_PARAGRAPH_SEP, "\n")
            edit = tab_edit(
                text,
                qt_to_py_position(text, selection_start - range_start),
                qt_to_py_position(text, selection_end - range_start),
                reverse=(
                    key == Qt.Key.Key_Backtab
                    or bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
                ),
                enabled=True,
            )
            if edit is not None:
                block_cursor.beginEditBlock()
                block_cursor.insertText(edit.replacement)
                block_cursor.endEditBlock()
                start_qt = range_start + py_to_qt_position(
                    edit.replacement, edit.sel_start
                )
                end_qt = range_start + py_to_qt_position(
                    edit.replacement, edit.sel_end
                )
                block_cursor.setPosition(start_qt)
                block_cursor.setPosition(
                    end_qt, QTextCursor.MoveMode.KeepAnchor
                )
                self.setTextCursor(block_cursor)
                event.accept()
                return
            # Shift+Tab on an already unindented Markdown line is a no-op;
            # do not let Qt reinterpret it as focus traversal out of editor.
            if (
                key == Qt.Key.Key_Backtab
                or bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            ):
                event.accept()
                return

        smart_pair_enabled = markdown_mode and cursor.block().userState() <= 0
        if (
            smart_pair_enabled
            and key == Qt.Key.Key_Backspace
            and modifiers == Qt.KeyboardModifier.NoModifier
            and not cursor.hasSelection()
        ):
            position = cursor.position()
            pair_cursor = QTextCursor(self.document())
            pair_cursor.setPosition(max(0, position - 1))
            pair_cursor.setPosition(
                min(self.document().characterCount() - 1, position + 1),
                QTextCursor.MoveMode.KeepAnchor,
            )
            pair_text = pair_cursor.selectedText()
            edit = backspace_pair_edit(pair_text, 1, enabled=True)
            if edit is not None and position > 0 and len(pair_text) == 2:
                pair_cursor.beginEditBlock()
                pair_cursor.removeSelectedText()
                pair_cursor.endEditBlock()
                self.setTextCursor(pair_cursor)
                event.accept()
                return

        typed = event.text()
        pair_modifiers = modifiers & ~Qt.KeyboardModifier.ShiftModifier
        if (
            smart_pair_enabled
            and not pair_modifiers
            and len(typed) == 1
            and typed in "()[]{}\"'`"
        ):
            # At the beginning of a line, keep backticks native so typing
            # ``` or ~~~ fences remains natural. Inline backticks still pair.
            line_prefix = cursor.block().text()[
                : qt_to_py_position(
                    cursor.block().text(),
                    cursor.position() - cursor.block().position(),
                )
            ]
            allow_pair = not (
                typed == "`" and not line_prefix.strip(" `\t")
            )
            if allow_pair:
                selection_start = cursor.selectionStart()
                selection_end = cursor.selectionEnd()
                selected = cursor.selectedText().replace(_PARAGRAPH_SEP, "\n")
                previous = ""
                if not cursor.hasSelection() and cursor.position() > 0:
                    previous_cursor = QTextCursor(cursor)
                    previous_cursor.movePosition(
                        QTextCursor.MoveOperation.PreviousCharacter,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    previous = previous_cursor.selectedText()
                following = ""
                if not cursor.hasSelection():
                    following_cursor = QTextCursor(cursor)
                    following_cursor.movePosition(
                        QTextCursor.MoveOperation.NextCharacter,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    following = following_cursor.selectedText()
                local_text = previous + selected + following
                local_start = len(previous)
                local_end = local_start + len(selected)
                edit = auto_pair_edit(
                    local_text,
                    local_start,
                    local_end,
                    typed,
                    enabled=True,
                )
                if edit is not None:
                    if (
                        not cursor.hasSelection()
                        and not edit.replacement
                        and following == typed
                    ):
                        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
                        self.setTextCursor(cursor)
                    else:
                        replacement = edit.replacement
                        cursor.beginEditBlock()
                        cursor.insertText(replacement)
                        cursor.endEditBlock()
                        start_qt = selection_start + py_to_qt_position(
                            replacement, edit.sel_start - edit.start
                        )
                        end_qt = selection_start + py_to_qt_position(
                            replacement, edit.sel_end - edit.start
                        )
                        cursor.setPosition(start_qt)
                        cursor.setPosition(
                            end_qt, QTextCursor.MoveMode.KeepAnchor
                        )
                        self.setTextCursor(cursor)
                    event.accept()
                    return

        if (
            not self._plain_text_mode
            and key in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
            and modifiers == Qt.KeyboardModifier.NoModifier
            and not self.textCursor().hasSelection()
        ):
            cursor = self.textCursor()
            block = cursor.block()
            line = block.text()
            edit = smart_enter_edit_on_line(
                line,
                qt_to_py_position(
                    line, cursor.position() - block.position()
                ),
                in_fenced_code=block.userState() > 0,
            )
            if edit is not None:
                self._apply_block_text_edit(line, block.position(), edit)
                event.accept()
                return
        super().keyPressEvent(event)
        self._show_wikilink_completions()

    def is_modified(self) -> bool:
        return self.document().isModified()

    def mark_saved(self):
        self.document().setModified(False)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        if not self._plain_text_mode and not self._markdown_services_suspended:
            self._highlighter.set_theme(theme)
        self.setStyleSheet(
            f"""
QPlainTextEdit {{
    background: {theme.window};
    border: none;
    color: {theme.text};
    font-family: "Cascadia Code", "Fira Code", Consolas, monospace;
    font-size: 14px;
    line-height: 1.6;
    padding: 16px 24px 16px 12px;
    selection-background-color: {theme.accent_soft};
    selection-color: {theme.text};
}}
"""
        )
        self._update_line_number_area_width()
        self._line_number_area.update()
        self._slash_popup.apply_theme(theme)
        self._selection_format_bar.apply_theme(theme)
