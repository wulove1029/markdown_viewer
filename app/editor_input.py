"""Pure edit planning for Word-like Markdown source input.

This module deliberately does not decide whether the current document is
Markdown, plain text, or inside a fenced code block.  The widget/controller
must make that decision and pass ``enabled=`` explicitly:

* smart Tab is normally enabled for Markdown and disabled for ``.txt`` so a
  plain-text editor keeps its native Tab behaviour;
* automatic pairs are conservative: enable them only for Markdown source
  outside fenced code.  ``.txt`` and fenced code should pass ``False``.

Every handled operation is represented by one :class:`TextEdit`, allowing the
Qt layer to apply it as a single undo step.  Positions are Python code-point
indexes; the Qt boundary is responsible for UTF-16 conversion.
"""

from __future__ import annotations

from .format_actions import TextEdit


INDENT = " " * 4
OPEN_TO_CLOSE: dict[str, str] = {
    "(": ")",
    "[": "]",
    "{": "}",
    '"': '"',
    "'": "'",
    "`": "`",
}
CLOSING_CHARS = frozenset(OPEN_TO_CLOSE.values())


def _normalized_range(text: str, start: int, end: int) -> tuple[int, int]:
    lower, upper = sorted((int(start), int(end)))
    lower = max(0, min(len(text), lower))
    upper = max(lower, min(len(text), upper))
    return lower, upper


def _touched_line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Return complete lines touched by a selection, excluding newlines.

    A selection ending exactly at the beginning of the following line does
    not include that line.  This matches the line-oriented formatting helpers
    used elsewhere in the editor.
    """

    start, end = _normalized_range(text, start, end)
    if end > start and text[end - 1 : end] == "\n":
        end -= 1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return line_start, line_end


def _outdent_prefix(line: str) -> int:
    """Number of leading characters removed by one Shift+Tab operation."""

    if line.startswith("\t"):
        return 1
    return min(len(line) - len(line.lstrip(" ")), len(INDENT))


def tab_edit(
    text: str,
    selection_start: int,
    selection_end: int,
    *,
    reverse: bool,
    enabled: bool,
) -> TextEdit | None:
    """Plan one line-oriented Tab or Shift+Tab edit.

    ``enabled=False`` returns ``None`` so the caller can delegate to the
    widget's native behaviour (the required policy for plain-text files).
    Selected lines are changed in one replacement.  Empty lines inside a
    multi-line selection remain empty to avoid adding trailing whitespace;
    pressing Tab on the current empty line still inserts four spaces.
    """

    if not enabled:
        return None

    start, end = _normalized_range(text, selection_start, selection_end)
    line_start, line_end = _touched_line_span(text, start, end)
    original = text[line_start:line_end]
    lines = original.split("\n")
    has_selection = start != end
    multi_line = len(lines) > 1

    if reverse:
        removed = [_outdent_prefix(line) for line in lines]
        if not any(removed):
            return None
        changed = [line[count:] for line, count in zip(lines, removed)]
    else:
        removed = [0] * len(lines)
        changed = [
            line if multi_line and not line.strip() else INDENT + line
            for line in lines
        ]

    replacement = "\n".join(changed)
    if has_selection:
        # Keeping complete transformed lines selected makes repeated Tab /
        # Shift+Tab deterministic and mirrors other line-formatting actions.
        new_start = line_start
        new_end = line_start + len(replacement)
    else:
        removed_before_caret = removed[0]
        if reverse:
            caret = max(line_start, start - removed_before_caret)
        else:
            caret = start + len(INDENT)
        new_start = new_end = caret

    return TextEdit(
        line_start,
        line_end,
        replacement,
        new_start,
        new_end,
    )


def auto_pair_edit(
    text: str,
    selection_start: int,
    selection_end: int,
    typed: str,
    *,
    enabled: bool,
) -> TextEdit | None:
    """Plan automatic pairing, selection wrapping, or closing-char skip.

    Opening characters insert their matching closer.  Symmetric quote and
    backtick characters act as openers unless the same character is already
    immediately ahead, in which case the edit only advances the caret.
    Ordinary closing characters are otherwise left to native input.

    For conservative prose behaviour, a single quote typed after an
    alphanumeric character is not auto-paired; this avoids turning common
    apostrophes into ``''``.  Selection wrapping remains available.
    """

    if not enabled or len(typed) != 1:
        return None
    start, end = _normalized_range(text, selection_start, selection_end)
    has_selection = start != end

    if not has_selection and start < len(text) and text[start] == typed:
        if typed in CLOSING_CHARS:
            # A zero-length replacement intentionally changes only the
            # requested caret coordinates; no document undo item is needed.
            return TextEdit(start, start, "", start + 1, start + 1)

    closing = OPEN_TO_CLOSE.get(typed)
    if closing is None:
        return None

    if (
        typed == "'"
        and not has_selection
        and start > 0
        and text[start - 1].isalnum()
    ):
        return None

    selected = text[start:end]
    replacement = typed + selected + closing
    if has_selection:
        return TextEdit(
            start,
            end,
            replacement,
            start + 1,
            start + 1 + len(selected),
        )
    return TextEdit(start, start, replacement, start + 1, start + 1)


def backspace_pair_edit(
    text: str,
    cursor_position: int,
    *,
    enabled: bool,
) -> TextEdit | None:
    """Delete both characters when Backspace is pressed in an empty pair."""

    if not enabled:
        return None
    position = max(0, min(int(cursor_position), len(text)))
    if position == 0 or position >= len(text):
        return None
    opening = text[position - 1]
    if OPEN_TO_CLOSE.get(opening) != text[position]:
        return None
    return TextEdit(
        position - 1,
        position + 1,
        "",
        position - 1,
        position - 1,
    )
