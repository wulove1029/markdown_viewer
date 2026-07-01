"""Find and edit Mermaid fenced code blocks in Markdown text."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MermaidBlock:
    id: str
    label: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    content_start_offset: int
    content_end_offset: int
    source: str


_OPEN_RE = re.compile(r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def find_mermaid_blocks(markdown: str) -> list[MermaidBlock]:
    """Return closed Mermaid fenced blocks in source order."""
    lines = markdown.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    blocks: list[MermaidBlock] = []
    i = 0
    while i < len(lines):
        match = _OPEN_RE.match(lines[i].rstrip("\r\n"))
        if not match or not _is_mermaid_info(match.group("info")):
            i += 1
            continue

        fence = match.group("fence")
        marker = fence[0]
        min_len = len(fence)
        close = _find_close(lines, i + 1, marker, min_len)
        if close is None:
            i += 1
            continue

        content_start = offsets[i + 1] if i + 1 < len(offsets) else len(markdown)
        content_end = offsets[close]
        start = offsets[i]
        end = offsets[close] + len(lines[close])
        source = markdown[content_start:content_end]
        index = len(blocks) + 1
        block_id = f"mermaid-{index}-{i + 1}"
        blocks.append(
            MermaidBlock(
                id=block_id,
                label=_label_for_block(lines, i, index),
                start_line=i,
                end_line=close,
                start_offset=start,
                end_offset=end,
                content_start_offset=content_start,
                content_end_offset=content_end,
                source=source.rstrip("\r\n"),
            )
        )
        i = close + 1
    return blocks


def replace_mermaid_block(markdown: str, block_id: str, source: str) -> str:
    """Replace the source of one Mermaid block while preserving its fences."""
    block = next((b for b in find_mermaid_blocks(markdown) if b.id == block_id), None)
    if block is None:
        raise ValueError(f"Mermaid block not found: {block_id}")
    newline = _preferred_newline(markdown)
    normalized = _normalize_source(source, newline)
    return (
        markdown[: block.content_start_offset]
        + normalized
        + markdown[block.content_end_offset :]
    )


def insert_mermaid_block(
    markdown: str, source: str, position: int | None = None
) -> str:
    """Insert a new Mermaid fenced block at *position* or at the end."""
    newline = _preferred_newline(markdown)
    normalized = _normalize_source(source, newline)
    block = f"```mermaid{newline}{normalized}```"

    if position is None:
        position = len(markdown)
    position = max(0, min(len(markdown), int(position)))
    before = markdown[:position]
    after = markdown[position:]

    prefix = ""
    if before and not before.endswith(newline):
        prefix = newline + newline
    elif before and not before.endswith(newline * 2):
        prefix = newline

    suffix = ""
    if after and not after.startswith(newline):
        suffix = newline
    elif not after:
        suffix = newline

    return before + prefix + block + suffix + after


def _line_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)
    return offsets


def _is_mermaid_info(info: str) -> bool:
    parts = info.strip().split()
    return bool(parts and parts[0].lower() == "mermaid")


def _find_close(
    lines: list[str], start: int, marker: str, min_len: int
) -> int | None:
    close_re = re.compile(rf"^[ \t]{{0,3}}{re.escape(marker)}{{{min_len},}}[ \t]*$")
    for idx in range(start, len(lines)):
        if close_re.match(lines[idx].rstrip("\r\n")):
            return idx
    return None


def _label_for_block(lines: list[str], block_line: int, index: int) -> str:
    for idx in range(block_line - 1, -1, -1):
        text = lines[idx].strip()
        if not text:
            continue
        match = _HEADING_RE.match(text)
        if match:
            heading = match.group(2).strip()
            return f"{heading} - Diagram {index}"
        if text.startswith("```") or text.startswith("~~~"):
            break
    return f"Diagram {index}"


def _preferred_newline(markdown: str) -> str:
    return "\r\n" if "\r\n" in markdown else "\n"


def _normalize_source(source: str, newline: str) -> str:
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if normalized:
        normalized = normalized.replace("\n", newline) + newline
    return normalized
