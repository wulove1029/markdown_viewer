"""Compact, theme-aware status strip for the text editor.

The widget is intentionally independent from ``MainWindow`` and
``EditorView``.  Its owner supplies one-based cursor coordinates and the
current document metadata through :meth:`EditorStatus.set_document_state`.
"""

from __future__ import annotations

import re
import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from .theme import LIGHT, Theme


_HAN_RANGES = (
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F), # supplementary CJK extensions/compatibility forms
)
_WORD_CONNECTORS = {"'", "’", "-"}
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<label>(?:\\.|[^\]])*)\]\((?:<[^>]*>|[^)]+)\)"
)
_MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[(?P<label>(?:\\.|[^\]])*)\]\((?:<[^>]*>|[^)]+)\)"
)
_MARKDOWN_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MARKDOWN_URL_RE = re.compile(r"(?:https?|file)://[^\s<>()]+", re.IGNORECASE)
_MARKDOWN_HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")


def _is_han(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _HAN_RANGES)


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


def _visible_markdown_text(text: str) -> str:
    """Drop destinations and tags that are not visible as reading text."""

    visible = _MARKDOWN_IMAGE_RE.sub(lambda match: match.group("label"), text)
    visible = _MARKDOWN_LINK_RE.sub(lambda match: match.group("label"), visible)
    visible = _MARKDOWN_WIKILINK_RE.sub(
        lambda match: match.group(2) or match.group(1), visible
    )
    visible = _MARKDOWN_URL_RE.sub(" URL ", visible)
    return _MARKDOWN_HTML_RE.sub(" ", visible)


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

    def set_document_state(
        self,
        *,
        line: int,
        column: int,
        text: str,
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
        self._kind_label, self._kind_compact = _document_kind_label(document_kind)
        count_text = str(text or "")
        if self._kind_compact == "MD":
            count_text = _visible_markdown_text(count_text)
        self._writing_units = count_writing_units(count_text)
        self._encoding = _encoding_label(encoding)
        self._newline = _newline_label(newline)
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
        self._full_text = (
            f"第 {self._line} 行，第 {self._column} 欄｜"
            f"{self._writing_units} 字｜{self._kind_label}｜"
            f"{self._encoding}｜{self._newline}"
        )
        self._compact_text = (
            f"{self._line}:{self._column}｜{self._writing_units} 字｜"
            f"{self._kind_compact}｜{self._encoding}｜{self._newline}"
        )
        self._minimal_text = (
            f"{self._line}:{self._column}｜{self._writing_units} 字｜"
            f"{self._kind_compact}"
        )
        accessible = f"編輯狀態：{self._full_text}"
        self.setAccessibleName(accessible)
        self.label.setAccessibleName(accessible)
        self.setToolTip(self._full_text)
        self.label.setToolTip(self._full_text)
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
