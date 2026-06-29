"""Lightweight Markdown syntax highlighter for the plain-text editor.

QSyntaxHighlighter colours the editor buffer as the user types: headings,
emphasis, code, links, quotes, lists, and fenced code blocks (tracked across
lines via the block state). Colours are pulled from the active Theme so it
matches light/dark mode.
"""

from __future__ import annotations

import re

from PyQt6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
)

from .theme import LIGHT, Theme

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_QUOTE_RE = re.compile(r"^\s*>")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")

# Inline patterns applied within a normal (non-code) line.
_INLINE_PATTERNS = [
    ("code", re.compile(r"`[^`\n]+`")),
    ("bold", re.compile(r"(\*\*|__)(?=\S)(.+?\S)\1")),
    ("italic", re.compile(r"(?<![\*_\w])([*_])(?=\S)([^*_\n]+?\S)\1(?![\*_\w])")),
    ("strike", re.compile(r"~~(?=\S)(.+?\S)~~")),
    ("link", re.compile(r"!?\[[^\]\n]*\]\([^)\n]*\)")),
    ("autolink", re.compile(r"https?://\S+")),
]


def _format(color: str | None = None, *, bold=False, italic=False, strike=False,
            mono=False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    if color:
        fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if italic:
        fmt.setFontItalic(True)
    if strike:
        fmt.setFontStrikeOut(True)
    if mono:
        fmt.setFontFamilies(["Cascadia Code", "Consolas", "monospace"])
    return fmt


class MarkdownHighlighter(QSyntaxHighlighter):
    def __init__(self, document, theme: Theme = LIGHT):
        super().__init__(document)
        self._formats: dict[str, QTextCharFormat] = {}
        self.set_theme(theme)

    def set_theme(self, theme: Theme):
        t = theme
        self._formats = {
            "heading": _format(t.accent, bold=True),
            "quote": _format(t.text_subtle, italic=True),
            "list": _format(t.warning, bold=True),
            "hr": _format(t.text_subtle),
            "fence": _format(t.success, bold=True, mono=True),
            "fence_body": _format(t.text_muted, mono=True),
            "code": _format(t.success, mono=True),
            "bold": _format(t.text, bold=True),
            "italic": _format(t.text_muted, italic=True),
            "strike": _format(t.text_subtle, strike=True),
            "link": _format(t.accent),
            "autolink": _format(t.accent),
        }
        self.rehighlight()

    def highlightBlock(self, text: str):  # noqa: N802 (Qt override)
        in_fence = self.previousBlockState() == 1
        is_fence_marker = bool(_FENCE_RE.match(text))

        if in_fence:
            if is_fence_marker:
                self.setFormat(0, len(text), self._formats["fence"])
                self.setCurrentBlockState(0)  # closing fence
            else:
                self.setFormat(0, len(text), self._formats["fence_body"])
                self.setCurrentBlockState(1)
            return
        if is_fence_marker:
            self.setFormat(0, len(text), self._formats["fence"])
            self.setCurrentBlockState(1)
            return

        self.setCurrentBlockState(0)

        if _HR_RE.match(text):
            self.setFormat(0, len(text), self._formats["hr"])
            return
        if _HEADING_RE.match(text):
            self.setFormat(0, len(text), self._formats["heading"])
            return
        if _QUOTE_RE.match(text):
            self.setFormat(0, len(text), self._formats["quote"])

        list_match = _LIST_RE.match(text)
        if list_match:
            start = list_match.start(2)
            self.setFormat(start, len(list_match.group(2)), self._formats["list"])

        for name, pattern in _INLINE_PATTERNS:
            for match in pattern.finditer(text):
                self.setFormat(
                    match.start(), match.end() - match.start(), self._formats[name]
                )
