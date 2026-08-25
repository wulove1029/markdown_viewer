"""Pure helpers for Markdown-aware editor conveniences."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .format_actions import TextEdit


_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_TASK_RE = re.compile(r"^([ \t]*)([-+*]) \[([ xX])\] (.*)$")
_ORDERED_RE = re.compile(r"^([ \t]*)(\d+)([.)]) (.*)$")
_BULLET_RE = re.compile(r"^([ \t]*)([-+*]) (.*)$")
_QUOTE_RE = re.compile(r"^([ \t]*> )(.*)$")


def fence_state_after_line(line: str, previous_state: int = 0) -> int:
    """Return the fenced-code state after ``line``.

    ``0`` means normal Markdown.  A positive value encodes the opener's
    delimiter character and length, which lets both the syntax highlighter
    and cursor-local editor features agree on CommonMark-style closers.
    """
    state = max(0, int(previous_state))
    match = _FENCE_RE.match(line)
    if match is None:
        return state

    fence = match.group(2)
    suffix = match.group(3)
    if state == 0:
        # A backtick opener's info string may not contain another backtick.
        if fence[0] == "`" and "`" in suffix:
            return 0
        length = min(len(fence), 1_000_000)
        return length * 2 + (1 if fence[0] == "~" else 0)

    opener_char = "~" if state % 2 else "`"
    opener_length = state // 2
    if (
        fence[0] == opener_char
        and len(fence) >= opener_length
        and not suffix.strip()
    ):
        return 0
    return state


@dataclass(frozen=True)
class SlashQuery:
    start: int
    end: int
    query: str


def is_in_fenced_code(text: str, position: int) -> bool:
    """Whether ``position`` lies after an unmatched Markdown fence opener."""
    line_start = text.rfind("\n", 0, position) + 1
    state = 0
    for line in text[:line_start].splitlines():
        state = fence_state_after_line(line, state)
    return state > 0


def active_slash_query_on_line(
    line: str, position: int, *, in_fenced_code: bool = False
) -> SlashQuery | None:
    """Find a slash query using only the current text block."""
    if in_fenced_code or position < 0 or position > len(line):
        return None
    prefix = line[:position]
    match = re.fullmatch(r"[ \t]*/([^\s/]*)", prefix)
    if match is None:
        return None
    return SlashQuery(0, position, match.group(1))


def active_slash_query(text: str, position: int) -> SlashQuery | None:
    """Find ``/query`` when it is the first non-space text on the line."""
    if position < 0 or position > len(text):
        return None
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    local_position = position - line_start
    # Most keystrokes are not slash commands.  Reject them before the
    # (pure-function compatibility) fence scan.
    candidate = active_slash_query_on_line(line, local_position)
    if candidate is None:
        return None
    local = active_slash_query_on_line(
        line,
        local_position,
        in_fenced_code=is_in_fenced_code(text, position),
    )
    if local is None:
        return None
    # The indentation exists only to make the trigger convenient.  Commands
    # insert canonical Markdown blocks, so consume that whitespace together
    # with /query instead of leaving it as visible content.
    return SlashQuery(line_start, position, local.query)


def smart_enter_edit_on_line(
    line: str, position: int, *, in_fenced_code: bool = False
) -> TextEdit | None:
    """Compute smart Enter from one text block using block-local offsets."""
    if in_fenced_code or position < 0 or position != len(line):
        return None

    task = _TASK_RE.match(line)
    if task:
        indent, bullet, _checked, body = task.groups()
        prefix = f"{indent}{bullet} [ ] "
        marker_start = len(indent)
    else:
        ordered = _ORDERED_RE.match(line)
        if ordered:
            indent, number, delimiter, body = ordered.groups()
            prefix = f"{indent}{int(number) + 1}{delimiter} "
            marker_start = len(indent)
        else:
            bullet_match = _BULLET_RE.match(line)
            if bullet_match:
                indent, bullet, body = bullet_match.groups()
                prefix = f"{indent}{bullet} "
                marker_start = len(indent)
            else:
                quote = _QUOTE_RE.match(line)
                if quote is None:
                    return None
                prefix, body = quote.groups()
                marker_start = len(prefix) - len(prefix.lstrip(" \t"))

    if not body.strip():
        return TextEdit(marker_start, position, "", marker_start, marker_start)
    replacement = "\n" + prefix
    caret = position + len(replacement)
    return TextEdit(position, position, replacement, caret, caret)


def smart_enter_edit(text: str, position: int) -> TextEdit | None:
    """Continue a Markdown list at end-of-line, or exit an empty item."""
    if position < 0 or position > len(text):
        return None
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end == -1:
        line_end = len(text)
    local = smart_enter_edit_on_line(
        text[line_start:line_end],
        position - line_start,
        in_fenced_code=is_in_fenced_code(text, position),
    )
    if local is None:
        return None
    return TextEdit(
        local.start + line_start,
        local.end + line_start,
        local.replacement,
        local.sel_start + line_start,
        local.sel_end + line_start,
    )


def is_web_url(value: str) -> bool:
    candidate = value.strip()
    if not candidate or any(char.isspace() for char in candidate):
        return False
    parsed = urlsplit(candidate)
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def linkify_paste_edit(
    text: str, selection_start: int, selection_end: int, value: str
) -> TextEdit | None:
    """Turn selected single-line text plus a pasted URL into a Markdown link."""
    if not is_web_url(value):
        return None
    start, end = sorted((selection_start, selection_end))
    if start == end or "\n" in text[start:end]:
        return None
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return None
    label = text[start:end]
    url = value.strip()
    replacement = f"[{label}]({url})"
    caret = start + len(replacement)
    return TextEdit(start, end, replacement, caret, caret)
