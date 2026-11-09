"""Source-writing outline navigation, section folding, and heading level edits."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin
from PySide6.QtCore import QObject, QRunnable, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import background_jobs
from .worker_relay import WorkerRelay


@dataclass(frozen=True)
class WritingHeading:
    line: int
    content_end_line: int
    section_end_line: int
    level: int
    title: str
    setext: bool


def extract_writing_headings(text: str) -> list[WritingHeading]:
    """Use renderer-compatible source maps; fenced/quoted/HTML content is excluded."""
    tokens = MarkdownIt("commonmark").use(front_matter_plugin).parse(text)
    headings: list[WritingHeading] = []
    line_count = text.count("\n") + 1
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0 or token.map is None:
            continue
        inline = tokens[index + 1]
        title = "".join(
            child.content if child.type != "softbreak" else " "
            for child in (inline.children or [])
            if child.type in {"text", "code_inline", "softbreak", "image"}
        )
        headings.append(WritingHeading(
            line=token.map[0], content_end_line=token.map[1], section_end_line=line_count,
            level=int(token.tag[1:]), title=title or "（未命名章節）",
            setext=token.markup in {"=", "-"},
        ))
    stack: list[int] = []
    for index, heading in enumerate(headings):
        while stack and headings[stack[-1]].level >= heading.level:
            previous = stack.pop()
            headings[previous] = replace(headings[previous], section_end_line=heading.line)
        stack.append(index)
    return headings


class _OutlineTask(QRunnable):
    """Parses headings off the GUI thread.

    ``report(generation, revision, headings)`` is the controller's
    ``WorkerRelay.emit``; the task owns no Qt object for its pool thread to
    release.
    """

    def __init__(self, text: str, generation: int, revision: int, report):
        super().__init__()
        self.text = text
        self.generation = generation
        self.revision = revision
        self.report = report

    def run(self):
        self.report(self.generation, self.revision, extract_writing_headings(self.text))


class WritingOutlineController(QObject):
    """One bounded background parse; folding never changes source text or Undo."""

    headings_changed = Signal(object)

    def __init__(self, editor, parent=None):
        super().__init__(parent or editor)
        self.editor = editor
        self.headings: list[WritingHeading] = []
        self._document = editor.document()
        self._revision = -1
        self._generation = 0
        self._active = True
        self._running = False
        self._task = None
        self._relay = WorkerRelay(self, self._receive_headings)
        self._changing_visibility = False
        self._folded: list[QTextCursor] = []
        self._hidden_blocks = []
        self._heading_cursors: list[QTextCursor] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._start_refresh)
        editor.textChanged.connect(self._schedule_refresh)
        editor.buffer_document_changed.connect(self._bind_document)
        editor.cursorPositionChanged.connect(self._reveal_cursor)
        self._timer.start(0)

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        self._generation += 1
        self._timer.stop()
        self.unfold_all()
        self.editor.textChanged.disconnect(self._schedule_refresh)
        self.editor.buffer_document_changed.disconnect(self._bind_document)
        self.editor.cursorPositionChanged.disconnect(self._reveal_cursor)

    def _bind_document(self, document) -> None:
        self.unfold_all()
        self._document = document
        self.headings = []
        self._heading_cursors = []
        self._revision = -1
        self.headings_changed.emit([])
        self._schedule_refresh()

    def _schedule_refresh(self, *_args) -> None:
        if self._active and not self._changing_visibility:
            self._generation += 1
            self._timer.start()

    def _start_refresh(self) -> None:
        if not self._active or self._running:
            return
        self._running = True
        self._task = _OutlineTask(
            self._document.toPlainText(), self._generation, self._document.revision(),
            self._relay.emit,
        )
        background_jobs.pool().start(self._task, background_jobs.BACKGROUND)

    def _receive_headings(self, generation: int, revision: int, headings) -> None:
        self._running = False
        self._task = None
        if not self._active:
            return
        if generation == self._generation and revision == self._document.revision():
            self._publish(headings, revision)
        elif not self._timer.isActive():
            self._timer.start()

    def refresh_now(self) -> None:
        """Refresh synchronously for an explicit command requiring current source maps."""
        self._timer.stop()
        self._generation += 1
        self._publish(
            extract_writing_headings(self._document.toPlainText()), self._document.revision()
        )

    def _publish(self, headings, revision: int) -> None:
        self.headings = list(headings)
        self._revision = revision
        self._heading_cursors = [
            QTextCursor(self._document.findBlockByNumber(heading.line))
            for heading in self.headings
        ]
        valid_lines = {heading.line for heading in self.headings}
        self._folded = [cursor for cursor in self._folded if cursor.blockNumber() in valid_lines]
        self._apply_folds()
        self.headings_changed.emit(self.headings)

    def _resolve(self, index: int) -> WritingHeading | None:
        if not self._active or self.editor.document() is not self._document:
            return None
        if not 0 <= index < len(self.headings):
            return None
        line = self._heading_cursors[index].blockNumber()
        if self._revision != self._document.revision():
            self.refresh_now()
        return next((heading for heading in self.headings if heading.line == line), None)

    def _apply_folds(self) -> None:
        folded_lines = {cursor.blockNumber() for cursor in self._folded}
        ranges = [(heading.content_end_line, heading.section_end_line)
                  for heading in self.headings if heading.line in folded_lines]
        if not ranges and not self._hidden_blocks:
            return
        ranges.sort()
        self._changing_visibility = True
        try:
            block = self._document.begin()
            changed = False
            self._hidden_blocks = []
            interval = 0
            while block.isValid():
                line = block.blockNumber()
                while interval < len(ranges) and ranges[interval][1] <= line:
                    interval += 1
                visible = interval == len(ranges) or line < ranges[interval][0]
                if block.isVisible() != visible:
                    block.setVisible(visible)
                    block.setLineCount(1 if visible else 0)
                    changed = True
                if not visible:
                    self._hidden_blocks.append(block)
                block = block.next()
            if changed:
                self._document.markContentsDirty(0, self._document.characterCount())
                self.editor.viewport().update()
        finally:
            self._changing_visibility = False

    def unfold_all(self) -> None:
        self._folded.clear()
        self._apply_folds()

    def is_folded(self, heading: WritingHeading) -> bool:
        return any(cursor.blockNumber() == heading.line for cursor in self._folded)

    def toggle_fold(self, index: int) -> None:
        heading = self._resolve(index)
        if heading is None:
            return
        if self.is_folded(heading):
            self._folded = [
                cursor for cursor in self._folded if cursor.blockNumber() != heading.line
            ]
        else:
            current_line = self.editor.textCursor().blockNumber()
            if heading.content_end_line <= current_line < heading.section_end_line:
                self.editor.setTextCursor(QTextCursor(self._document.findBlockByNumber(heading.line)))
            self._folded.append(QTextCursor(self._document.findBlockByNumber(heading.line)))
        self._apply_folds()
        self.headings_changed.emit(self.headings)

    def _reveal_cursor(self) -> None:
        if (
            self._changing_visibility or not self._active
            or self.editor.document() is not self._document
        ):
            return
        line = self.editor.textCursor().blockNumber()
        collapsed = {heading.line for heading in self.headings
                     if heading.content_end_line <= line < heading.section_end_line}
        kept = [cursor for cursor in self._folded if cursor.blockNumber() not in collapsed]
        if len(kept) != len(self._folded):
            self._folded = kept
            self._apply_folds()
            self.headings_changed.emit(self.headings)

    def jump_to_heading(self, index: int) -> None:
        heading = self._resolve(index)
        if heading is None:
            return
        self.editor.setTextCursor(QTextCursor(self._document.findBlockByNumber(heading.line)))
        self.editor.ensureCursorVisible()
        self.editor.setFocus()

    def change_heading_level(self, index: int, delta: int) -> bool:
        heading = self._resolve(index)
        if heading is None or self.editor.is_composing() or self.editor.isReadOnly():
            return False
        level = max(1, min(6, heading.level + int(delta)))
        if level == heading.level:
            return False
        self.unfold_all()
        block = self._document.findBlockByNumber(heading.line)
        cursor = QTextCursor(block)
        cursor.beginEditBlock()
        if heading.setext:
            end = self._document.findBlockByNumber(heading.content_end_line - 1)
            cursor.setPosition(end.position() + end.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            original = cursor.selectedText().replace("\u2029", "\n").splitlines()[:-1]
            cursor.insertText("#" * level + " " + " ".join(line.strip() for line in original))
        else:
            prefix = re.match(r"^( {0,3})(#{1,6})", block.text())
            if prefix is None:
                cursor.endEditBlock()
                return False
            cursor.setPosition(block.position() + len(prefix.group(1)))
            cursor.setPosition(cursor.position() + heading.level, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText("#" * level)
        cursor.endEditBlock()
        self.refresh_now()
        return True


class WritingOutlineDialog(QDialog):
    """Modeless source outline with keyboard-accessible writing actions."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("寫作大綱")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(420, 600)
        self.controller = WritingOutlineController(editor, self)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("跳轉章節、折疊內容，或調整標題階層。"))
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("寫作章節大綱")
        self.tree.itemActivated.connect(lambda *_: self.controller.jump_to_heading(self._index()))
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        for label, callback in (
            ("跳轉", lambda: self.controller.jump_to_heading(self._index())),
            ("折疊／展開", lambda: self.controller.toggle_fold(self._index())),
            ("升一階", lambda: self.controller.change_heading_level(self._index(), -1)),
            ("降一階", lambda: self.controller.change_heading_level(self._index(), 1)),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        expand = QPushButton("展開所有章節")
        expand.clicked.connect(self.controller.unfold_all)
        layout.addWidget(expand)
        self.controller.headings_changed.connect(self._show_headings)

    def _index(self) -> int:
        item = self.tree.currentItem()
        return int(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else -1

    def _show_headings(self, headings) -> None:
        selected = self._index()
        self.tree.clear()
        stack = []
        for index, heading in enumerate(headings):
            while stack and stack[-1][0] >= heading.level:
                stack.pop()
            prefix = "▸ " if self.controller.is_folded(heading) else ""
            item = QTreeWidgetItem([f"{prefix}{heading.title}"])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setToolTip(0, f"第 {heading.line + 1} 行 · H{heading.level}")
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            stack.append((heading.level, item))
            if index == selected:
                self.tree.setCurrentItem(item)
        self.tree.expandAll()

    def closeEvent(self, event):
        self.controller.close()
        super().closeEvent(event)
