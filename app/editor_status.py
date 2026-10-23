"""Compact, theme-aware status strip for the text editor.

The widget is intentionally independent from ``MainWindow`` and
``EditorView``.  Its owner supplies one-based cursor coordinates and the
current document metadata through :meth:`EditorStatus.set_document_state`.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent, QTextCursor, QTextDocument
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget
from shiboken6 import isValid

from .smart_writing import fence_state_after_line
from .theme import LIGHT, Theme

_HAN_RANGES = (
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F), # supplementary CJK extensions/compatibility forms
)
_WORD_CONNECTORS = {"'", "’", "-"}
_ASCII_WORD_RE = re.compile(r"(?<![A-Za-z0-9])(?<![A-Za-z0-9]['-])[A-Za-z0-9]")
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<label>(?:\\.|[^\]])*)\]\((?:<[^>]*>|[^)]+)\)"
)
_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[(?P<label>(?:\\.|[^\]])*)\]\((?:<[^>]*>|[^)]+)\)"
)
_MARKDOWN_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MARKDOWN_URL_RE = re.compile(r"(?:https?|file)://[^\s<>()]+", re.IGNORECASE)
_MARKDOWN_HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")


@lru_cache(maxsize=4096)
def _is_han(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _HAN_RANGES)


@lru_cache(maxsize=4096)
def _is_latin_or_digit(character: str) -> bool:
    if character.isdigit():
        return True
    if not character.isalpha():
        return False
    return "LATIN" in unicodedata.name(character, "")


def count_writing_units(text: str) -> int:
    """Return a Word-like count suitable for mixed Chinese/English notes.

    Each Han ideograph counts as one unit.  A contiguous Latin-letter or
    digit word counts as one unit; an apostrophe or hyphen may join two parts
    of that word.  Whitespace, punctuation, emoji, and Markdown punctuation
    do not add to the count.

    Examples: ``你好 world`` is three units and ``note-taking 2026`` is two.
    This is deliberately a writing statistic, not a linguistic tokenizer.
    """

    # Source/code blocks commonly contain only ASCII. Scan those in the regex
    # engine without Python-level Unicode classification for every character.
    # Match starts only: repeated connector groups can allocate a large regex
    # backtracking stack for a single long hyphenated word.
    if text.isascii():
        return sum(1 for _ in _ASCII_WORD_RE.finditer(text))

    count = 0
    inside_latin_word = False
    for index, character in enumerate(text):
        if _is_han(character):
            count += 1
            inside_latin_word = False
            continue
        if _is_latin_or_digit(character):
            if not inside_latin_word:
                count += 1
            inside_latin_word = True
            continue
        if (
            character in _WORD_CONNECTORS
            and inside_latin_word
            and index + 1 < len(text)
            and _is_latin_or_digit(text[index + 1])
        ):
            continue
        inside_latin_word = False
    return count


def _visible_inline_markdown_text(text: str) -> str:
    """Drop destinations and tags that are not visible as reading text."""

    visible = _MARKDOWN_IMAGE_RE.sub(lambda match: match.group("label"), text)
    visible = _MARKDOWN_LINK_RE.sub(lambda match: match.group("label"), visible)
    visible = _MARKDOWN_WIKILINK_RE.sub(
        lambda match: match.group(2) or match.group(1), visible
    )
    visible = _MARKDOWN_URL_RE.sub(" URL ", visible)
    return _MARKDOWN_HTML_RE.sub(" ", visible)


def _visible_markdown_text(text: str) -> str:
    """Count code literally and retain multiline prose-link semantics."""
    result: list[str] = []
    prose: list[str] = []
    state = 0
    for line in text.split("\n"):
        following = fence_state_after_line(line, state)
        if state or following:
            if prose:
                result.append(_visible_inline_markdown_text("\n".join(prose)))
                prose.clear()
            result.append(line)
        else:
            prose.append(line)
        state = following
    if prose:
        result.append(_visible_inline_markdown_text("\n".join(prose)))
    return "\n".join(result)


_OPEN_MULTILINE_MARKUP_RE = re.compile(
    r"\[(?:\\.|[^\]])*$|\[(?:\\.|[^\]])*\]\([^)]*$|<[/A-Za-z][^>]*$"
)


def _may_cross_block(text: str) -> bool:
    """Conservatively identify constructs that can consume later blocks.

    Complete same-line links are removed before checking for unfinished
    markup. Check each substitution stage because an image label can itself
    contain link syntax. False positives only select the exact fallback.
    """
    if "[" not in text and "<" not in text:
        return False
    stages = (
        (_MARKDOWN_IMAGE_RE, lambda match: match.group("label")),
        (_MARKDOWN_LINK_RE, lambda match: match.group("label")),
        (_MARKDOWN_WIKILINK_RE, lambda match: match.group(2) or match.group(1)),
        (_MARKDOWN_URL_RE, lambda _match: " URL "),
        (_MARKDOWN_HTML_RE, lambda _match: " "),
    )
    for pattern, replacement in stages:
        remainder = pattern.sub("", text)
        if _OPEN_MULTILINE_MARKUP_RE.search(remainder):
            return True
        text = pattern.sub(replacement, text)
    return False


@dataclass(frozen=True)
class _CountedBlock:
    text: str
    before: int
    after: int
    units: int
    cross_block: bool


class DocumentWritingCounter(QObject):
    """Exact document statistics with UTF-16 dirty-range invalidation.

    Ordinary edits recount only touched QTextBlocks. Fence state propagates
    until it agrees with the retained suffix. Incomplete/multiline markup
    selects an exact full-prose fallback instead of silently changing the
    existing link-label semantics. No QTextBlock user data or formatting is
    modified, so the syntax highlighter and Undo stack remain independent.
    """

    ready_changed = Signal()

    def __init__(
        self, document: QTextDocument, *, markdown: bool, background: bool = False
    ):
        super().__init__(document)
        self._document = document
        self._markdown = bool(markdown)
        self._background = bool(background)
        self._ready = False
        self._blocks: list[_CountedBlock] = []
        self._units = 0
        self._cross_block_count = 0
        self._fallback_count: int | None = None
        self.blocks_recounted = 0
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.timeout.connect(self._build_slice)
        self._pending_block = document.begin()
        if self._background:
            self._restart_background(immediate=True)
        else:
            self._rebuild()
        document.contentsChange.connect(self._contents_changed)

    @classmethod
    def for_document(
        cls, document: QTextDocument, *, markdown: bool, background: bool = False
    ):
        counters = getattr(document, "_writing_counters", None)
        if counters is None:
            counters = {}
            document._writing_counters = counters
        if markdown not in counters:
            counters[markdown] = cls(document, markdown=markdown, background=background)
        return counters[markdown]

    @property
    def ready(self) -> bool:
        return self._ready

    def _restart_background(self, *, immediate: bool = False) -> None:
        self._ready = False
        self._blocks = []
        self._units = self._cross_block_count = 0
        self._fallback_count = None
        self._pending_block = self._document.begin()
        self.ready_changed.emit()
        if immediate:
            self._build_slice()
        else:
            self._build_timer.start(0)

    def _build_slice(self) -> None:
        deadline = time.perf_counter() + 0.006
        state = self._blocks[-1].after if self._blocks else 0
        while self._pending_block.isValid():
            entry = self._count_block(self._pending_block.text(), state)
            self._blocks.append(entry)
            self._units += entry.units
            self._cross_block_count += int(entry.cross_block)
            state = entry.after
            self._pending_block = self._pending_block.next()
            if time.perf_counter() >= deadline:
                self._build_timer.start(0)
                return
        self._ready = True
        self.ready_changed.emit()

    def _count_block(self, text: str, before: int) -> _CountedBlock:
        self.blocks_recounted += 1
        after = fence_state_after_line(text, before) if self._markdown else 0
        prose = self._markdown and not before and not after
        visible = _visible_inline_markdown_text(text) if prose else text
        return _CountedBlock(
            text, before, after, count_writing_units(visible),
            prose and _may_cross_block(text),
        )

    def _rebuild(self) -> None:
        self._blocks = []
        self._units = self._cross_block_count = 0
        self._fallback_count = None
        block = self._document.begin()
        state = 0
        while block.isValid():
            entry = self._count_block(block.text(), state)
            self._blocks.append(entry)
            self._units += entry.units
            self._cross_block_count += int(entry.cross_block)
            state = entry.after
            block = block.next()
        self._ready = True

    def _contents_changed(self, position: int, removed: int, added: int) -> None:
        if not removed and not added:
            return
        if not self._ready:
            self._restart_background()
            return
        document = self._document
        last = max(0, document.characterCount() - 1)
        start = document.findBlock(min(position, last)).blockNumber()
        stop = document.findBlock(min(position + added, last)).blockNumber() + 1
        old_stop = stop - (document.blockCount() - len(self._blocks))
        if start < 0 or old_stop < start or old_stop > len(self._blocks):
            if self._background:
                self._restart_background()
            else:
                self._rebuild()
            return
        if self._background and stop - start > 128:
            self._restart_background()
            return
        old_entries = self._blocks[start:old_stop]
        state = self._blocks[start - 1].after if start else 0
        replacements: list[_CountedBlock] = []
        block = document.findBlockByNumber(start)
        for _ in range(start, stop):
            entry = self._count_block(block.text(), state)
            replacements.append(entry)
            state = entry.after
            block = block.next()
        self._blocks[start:old_stop] = replacements
        self._units += sum(item.units for item in replacements) - sum(
            item.units for item in old_entries
        )
        self._cross_block_count += sum(int(item.cross_block) for item in replacements) - sum(
            int(item.cross_block) for item in old_entries
        )
        index = stop
        if (
            self._background
            and len(self._blocks) - index > 128
            and self._blocks[index].before != state
        ):
            self._restart_background()
            return
        while index < len(self._blocks) and self._blocks[index].before != state:
            previous = self._blocks[index]
            entry = self._count_block(previous.text, state)
            self._blocks[index] = entry
            self._units += entry.units - previous.units
            self._cross_block_count += int(entry.cross_block) - int(previous.cross_block)
            state = entry.after
            index += 1
        self._fallback_count = None

    @property
    def writing_units(self) -> int:
        if not self._ready:
            raise RuntimeError("Writing statistics are still being built")
        if not self._cross_block_count:
            return self._units
        if self._fallback_count is None:
            text = "\n".join(block.text for block in self._blocks)
            self._fallback_count = count_writing_units(_visible_markdown_text(text))
        return self._fallback_count

    @property
    def uses_full_fallback(self) -> bool:
        return self._cross_block_count > 0


def _document_kind_label(document_kind: str) -> tuple[str, str]:
    normalized = str(document_kind or "").strip().casefold()
    if normalized in {"markdown", "md"}:
        return "Markdown", "MD"
    if normalized in {"text", "txt", "plain", "plain_text", "plaintext"}:
        return "純文字", "TXT"
    raise ValueError(f"Unsupported document kind: {document_kind!r}")


def _encoding_label(encoding: str) -> str:
    normalized = str(encoding or "utf-8").strip().replace("_", "-")
    return normalized.upper() or "UTF-8"


def _newline_label(newline: str) -> str:
    normalized = str(newline or "\n")
    if normalized == "\r\n" or normalized.strip().upper() == "CRLF":
        return "CRLF"
    if normalized == "\n" or normalized.strip().upper() == "LF":
        return "LF"
    # The editor currently normalizes source files to LF or CRLF.  Treat a
    # lone CR as CRLF for a useful status rather than exposing control text.
    if "\r" in normalized:
        return "CRLF"
    return "LF"


class EditorStatus(QWidget):
    """One-line editor metadata that gracefully compacts in narrow panes."""

    def __init__(self, theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self.setObjectName("editorStatus")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setFixedHeight(29)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(0)
        self.label = QLabel(self)
        self.label.setObjectName("editorStatusLabel")
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.label)

        self._line = 1
        self._column = 1
        self._writing_units = 0
        self._statistics_pending = False
        self._counter: DocumentWritingCounter | None = None
        self._selection_document: QTextDocument | None = None
        self._selection_key = None
        self._selection_units: int | None = None
        self._selection_pending = False
        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.timeout.connect(self._count_selection_slice)
        self._kind_label = "Markdown"
        self._kind_compact = "MD"
        self._encoding = "UTF-8"
        self._newline = "LF"
        self._full_text = ""
        self._compact_text = ""
        self._minimal_text = ""

        self.apply_theme(theme)
        self._rebuild_texts()

    @property
    def full_status_text(self) -> str:
        """The unabbreviated status, also exposed through the tooltip."""

        return self._full_text

    @property
    def writing_unit_count(self) -> int:
        return self._writing_units

    @property
    def selection_writing_unit_count(self) -> int | None:
        """Exact selected source units, or None when absent/still counting."""
        return self._selection_units

    def clear_selection(self) -> None:
        self._selection_timer.stop()
        self._selection_document = None
        self._selection_key = None
        self._selection_units = None
        self._selection_pending = False
        self._rebuild_texts()

    def set_selection(self, cursor: QTextCursor) -> None:
        """Count selected source text without a synchronous full-range copy.

        This uses the same mixed-language unit contract as
        count_writing_units; selected Markdown/code is counted literally.
        Each GUI turn reads bounded UTF-16 fragments and stale revisions are
        discarded. Neither the live cursor nor the document is modified.
        """
        if not cursor.hasSelection():
            if self._selection_key is not None:
                self.clear_selection()
            return
        document = cursor.document()
        key = (document.revision(), cursor.selectionStart(), cursor.selectionEnd())
        if document is self._selection_document and key == self._selection_key:
            return
        self._selection_document = document
        self._selection_key = key
        self._selection_position = key[1]
        self._selection_end = key[2]
        self._selection_total = 0
        self._selection_tail = ""
        self._selection_units = None
        self._selection_pending = True
        self._selection_timer.start(0)
        self._rebuild_texts()

    def _count_selection_slice(self) -> None:
        document = self._selection_document
        if (document is None or not isValid(document)
                or document.revision() != self._selection_key[0]):
            self.clear_selection()
            return
        deadline = time.perf_counter() + 0.004
        cursor = QTextCursor(document)
        while self._selection_position < self._selection_end:
            stop = min(self._selection_position + 4096, self._selection_end)
            if stop < self._selection_end:
                # A QChar position may bisect a supplementary Han/emoji pair.
                if 0xD800 <= ord(document.characterAt(stop - 1)) <= 0xDBFF:
                    stop -= 1
            cursor.setPosition(self._selection_position)
            cursor.setPosition(stop, QTextCursor.MoveMode.KeepAnchor)
            fragment = cursor.selectedText()
            # Two trailing characters retain Latin-word/connector context.
            # Subtracting their own count avoids counting a split word twice.
            joined = self._selection_tail + fragment
            self._selection_total += (
                count_writing_units(joined) - count_writing_units(self._selection_tail)
            )
            self._selection_tail = joined[-2:]
            self._selection_position = stop
            if time.perf_counter() >= deadline:
                self._selection_timer.start(0)
                return
        self._selection_units = self._selection_total
        self._selection_pending = False
        self._rebuild_texts()

    def set_document_state(
        self,
        *,
        line: int,
        column: int,
        text: str = "",
        document: QTextDocument | None = None,
        document_kind: str,
        encoding: str,
        newline: str,
    ) -> None:
        """Replace all displayed state.

        ``line`` and ``column`` are one-based.  ``document_kind`` accepts the
        application's ``"markdown"`` and ``"text"`` values (plus common
        ``md``/``txt`` aliases).  ``newline`` accepts either the actual newline
        sequence or the display names ``LF``/``CRLF``.
        """

        self._line = max(1, int(line))
        self._column = max(1, int(column))
        if self._selection_document is not None and document is not self._selection_document:
            self.clear_selection()
        self._kind_label, self._kind_compact = _document_kind_label(document_kind)
        if document is not None:
            counter = DocumentWritingCounter.for_document(
                document, markdown=self._kind_compact == "MD", background=True
            )
            self._set_counter(counter)
            self._statistics_pending = not counter.ready
            if counter.ready:
                self._writing_units = counter.writing_units
        else:
            self._set_counter(None)
            self._statistics_pending = False
            count_text = str(text or "")
            if self._kind_compact == "MD":
                count_text = _visible_markdown_text(count_text)
            self._writing_units = count_writing_units(count_text)
        self._encoding = _encoding_label(encoding)
        self._newline = _newline_label(newline)
        self._rebuild_texts()

    def _set_counter(self, counter: DocumentWritingCounter | None) -> None:
        if counter is self._counter:
            return
        if self._counter is not None:
            try:
                self._counter.ready_changed.disconnect(self._counter_ready_changed)
            except RuntimeError:
                pass
        self._counter = counter
        if counter is not None:
            counter.ready_changed.connect(self._counter_ready_changed)

    def _counter_ready_changed(self) -> None:
        if self._counter is None:
            return
        self._statistics_pending = not self._counter.ready
        if self._counter.ready:
            self._writing_units = self._counter.writing_units
        self._rebuild_texts()

    def set_cursor_position(self, *, line: int, column: int) -> None:
        """Update only the cursor coordinates without recounting the document."""

        self._line = max(1, int(line))
        self._column = max(1, int(column))
        self._rebuild_texts()

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"""
QWidget#editorStatus {{
    background: {theme.surface_alt};
    border: none;
    border-top: 1px solid {theme.border};
}}
QLabel#editorStatusLabel {{
    background: transparent;
    border: none;
    color: {theme.text_muted};
    font-size: 11px;
}}
"""
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_visible_text()

    def _rebuild_texts(self) -> None:
        units = "統計中…" if self._statistics_pending else f"{self._writing_units} 字"
        if self._selection_pending:
            units = f"選取統計中…／{units}"
        elif self._selection_units is not None:
            units = f"選取 {self._selection_units} 字／{units}"
        self._full_text = (
            f"第 {self._line} 行，第 {self._column} 欄｜"
            f"{units}｜{self._kind_label}｜"
            f"{self._encoding}｜{self._newline}"
        )
        self._compact_text = (
            f"{self._line}:{self._column}｜{units}｜"
            f"{self._kind_compact}｜{self._encoding}｜{self._newline}"
        )
        self._minimal_text = (
            f"{self._line}:{self._column}｜{units}｜"
            f"{self._kind_compact}"
        )
        accessible = f"編輯狀態：{self._full_text}"
        self.setAccessibleName(accessible)
        self.label.setAccessibleName(accessible)
        tooltip = self._full_text
        if self._selection_key is not None:
            tooltip += "\n選取字數依來源文字計算：中文字逐字計數，英文及數字連續詞計一字。"
        self.setToolTip(tooltip)
        self.label.setToolTip(tooltip)
        self._fit_visible_text()

    def _fit_visible_text(self) -> None:
        available = max(0, self.label.contentsRect().width())
        metrics = self.label.fontMetrics()
        for candidate in (
            self._full_text,
            self._compact_text,
            self._minimal_text,
        ):
            if metrics.horizontalAdvance(candidate) <= available:
                self.label.setText(candidate)
                return
        self.label.setText(
            metrics.elidedText(
                self._minimal_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )
