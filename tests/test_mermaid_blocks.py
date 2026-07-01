"""Tests for Mermaid fenced-block source editing."""

import pytest

from app.mermaid_blocks import (
    find_mermaid_blocks,
    insert_mermaid_block,
    replace_mermaid_block,
)


def test_find_single_mermaid_block_with_heading_label():
    md = "# Architecture\n\n```mermaid\ngraph TD\n  A --> B\n```\n"
    blocks = find_mermaid_blocks(md)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.label == "Architecture - Diagram 1"
    assert block.start_line == 2
    assert block.end_line == 5
    assert block.source == "graph TD\n  A --> B"


def test_find_multiple_blocks_and_ignore_other_code():
    md = (
        "```python\nprint(1)\n```\n\n"
        "```mermaid\ngraph LR\nA-->B\n```\n\n"
        "~~~MERMAID\nsequenceDiagram\nA->>B: Hi\n~~~\n"
    )
    blocks = find_mermaid_blocks(md)
    assert [b.id for b in blocks] == ["mermaid-1-5", "mermaid-2-10"]
    assert blocks[0].source == "graph LR\nA-->B"
    assert blocks[1].source == "sequenceDiagram\nA->>B: Hi"


def test_unclosed_block_is_ignored():
    md = "before\n```mermaid\ngraph TD\nA-->B\n"
    assert find_mermaid_blocks(md) == []


def test_replace_only_selected_mermaid_block():
    md = (
        "before\n\n"
        "```mermaid\ngraph TD\nA-->B\n```\n\n"
        "middle\n\n"
        "```mermaid\ngraph LR\nX-->Y\n```\n"
    )
    blocks = find_mermaid_blocks(md)
    out = replace_mermaid_block(md, blocks[1].id, "sequenceDiagram\nX->>Y: ok")
    assert "graph TD\nA-->B" in out
    assert "sequenceDiagram\nX->>Y: ok" in out
    assert "graph LR\nX-->Y" not in out


def test_replace_preserves_crlf_style():
    md = "```mermaid\r\ngraph TD\r\nA-->B\r\n```\r\n"
    block = find_mermaid_blocks(md)[0]
    out = replace_mermaid_block(md, block.id, "graph LR\nX-->Y")
    assert "graph LR\r\nX-->Y\r\n" in out
    assert "\nX-->Y\n" not in out


def test_replace_unknown_block_raises():
    with pytest.raises(ValueError):
        replace_mermaid_block("plain", "missing", "graph TD")


def test_insert_mermaid_block_at_cursor_position():
    md = "# Title\n\nBody"
    out = insert_mermaid_block(md, "graph TD\nA-->B", position=len("# Title\n\n"))
    assert out.startswith("# Title\n\n```mermaid\n")
    assert "graph TD\nA-->B\n```" in out
    assert out.endswith("Body")


def test_insert_mermaid_block_at_end_of_empty_document():
    out = insert_mermaid_block("", "graph TD\nA-->B")
    assert out == "```mermaid\ngraph TD\nA-->B\n```\n"
