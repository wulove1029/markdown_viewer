"""Tests for the editor's Markdown syntax highlighter."""

from PyQt6.QtGui import QTextDocument

from app.md_highlighter import MarkdownHighlighter
from app.theme import DARK, LIGHT


SAMPLE = """# Heading

Some **bold** and *italic* and `code` and ~~strike~~.

> a quote
- a list item
1. ordered

[link](https://example.com) and https://bare.example

```python
print("fenced")
x = 1
```

---
"""


def _collect_formats(doc):
    """Return the set of distinct foreground colors applied across all blocks."""
    colors = set()
    block = doc.firstBlock()
    while block.isValid():
        for fmt_range in block.layout().formats():
            colors.add(fmt_range.format.foreground().color().name())
        block = block.next()
    return colors


def test_highlighter_constructs_and_runs(qapp):
    doc = QTextDocument()
    doc.setPlainText(SAMPLE)
    hl = MarkdownHighlighter(doc, LIGHT)
    hl.rehighlight()  # must not raise
    assert hl.document() is doc


def test_highlighter_applies_some_formats(qapp):
    doc = QTextDocument()
    hl = MarkdownHighlighter(doc, LIGHT)
    doc.setPlainText(SAMPLE)
    hl.rehighlight()
    colors = _collect_formats(doc)
    # At least heading/link/code coloring should have produced >1 distinct color.
    assert len(colors) >= 2


def test_theme_switch_rehighlights(qapp):
    doc = QTextDocument()
    doc.setPlainText("# Heading\n`code`")
    hl = MarkdownHighlighter(doc, LIGHT)
    hl.rehighlight()
    light_colors = _collect_formats(doc)
    hl.set_theme(DARK)
    dark_colors = _collect_formats(doc)
    assert light_colors and dark_colors
    assert light_colors != dark_colors  # colors track the theme


def test_fenced_block_state(qapp):
    doc = QTextDocument()
    doc.setPlainText("```\nplain *not italic*\n```")
    hl = MarkdownHighlighter(doc, LIGHT)
    hl.rehighlight()  # block-state handling must not raise
    assert doc.blockCount() == 3
