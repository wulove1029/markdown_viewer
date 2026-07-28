"""Pure text surgery behind the preview's inline block editor.

Deliberately Qt-free so the read / verify / replace rules stay unit-testable
headless. Line indices are **0-based and inclusive on both ends**, matching the
``data-src-start`` / ``data-src-end`` attributes ``md_converter`` stamps onto
every top-level block.

The verify step is the point of the module: the preview holds line numbers from
whenever it was rendered, so a write must prove the lines it is about to
overwrite still say what the user was shown. Anything else and the file moved
underneath us and the edit has to be refused.
"""

from __future__ import annotations


def normalize_newlines(text: str) -> str:
    """Collapse CRLF / CR line endings to LF for comparison and splitting."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_source_lines(text: str, start: int, end: int) -> str | None:
    """Return lines *start*..*end* of *text*, or None when out of range."""
    if start < 0 or end < start:
        return None
    lines = normalize_newlines(text).split("\n")
    if end >= len(lines):
        return None
    return "\n".join(lines[start : end + 1])


def replace_source_lines(
    text: str,
    start: int,
    end: int,
    original: str,
    new: str,
    newline: str = "\n",
) -> str | None:
    """Swap lines *start*..*end* of *text* for *new*, guarding against drift.

    Returns the complete new document text, joined with *newline* so the
    caller can preserve the file's original line endings. Returns None -- and
    signals "refuse this write" -- when the range is out of bounds or the lines
    currently there differ from *original*.

    A *new* that is entirely empty deletes the block rather than leaving a
    stray blank line behind.
    """
    current = extract_source_lines(text, start, end)
    if current is None or current != normalize_newlines(original):
        return None
    lines = normalize_newlines(text).split("\n")
    replacement = normalize_newlines(new)
    new_lines = replacement.split("\n") if replacement != "" else []
    return newline.join(lines[:start] + new_lines + lines[end + 1 :])
