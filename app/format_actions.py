"""Markdown formatting operations for the plain-text editor.

The string-level logic lives in pure functions that take the full document
text plus a selection range and return a :class:`TextEdit` describing a
single replacement.  A thin Qt layer (:func:`apply_format_action`) turns
that into one ``QTextCursor`` edit block so Ctrl+Z reverts the whole
operation in a single undo step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BOLD = "**"
ITALIC = "*"
STRIKE = "~~"
CODE = "`"
MATH = "$"

# ``==x==`` is not enabled in md_converter, but ``<mark>`` is on the safe
# inline-HTML allowlist, so highlighting uses the HTML tag pair.
MARK_OPEN = "<mark>"
MARK_CLOSE = "</mark>"
WIKI_OPEN = "[["
WIKI_CLOSE = "]]"

LINK_PLACEHOLDER_TEXT = "文字"
LINK_PLACEHOLDER_URL = "網址"
LINK_URL_HINT = "url"

TABLE_TEMPLATE = "| 標題1 | 標題2 |\n| --- | --- |\n|  |  |"
MERMAID_TEMPLATE = "flowchart LR\n    A[步驟一] --> B[步驟二]"

_HEADING_RE = re.compile(r"^#{1,6} ")
_ORDERED_RE = re.compile(r"^\d+\. ")
_TASK_RE = re.compile(r"^- \[[ xX]\] ")


@dataclass(frozen=True)
class TextEdit:
    """Replace ``text[start:end]`` with ``replacement``.

    ``sel_start``/``sel_end`` are the selection (or caret, when equal) to
    restore afterwards, expressed in the coordinates of the *new* text.
    """

    start: int
    end: int
    replacement: str
    sel_start: int
    sel_end: int


# ── inline toggles ─────────────────────────────────────────────────────


def _run_left(text: str, pos: int, char: str) -> int:
    """Length of the run of ``char`` ending just before ``pos``."""
    count = 0
    while pos - count - 1 >= 0 and text[pos - count - 1] == char:
        count += 1
    return count


def _run_right(text: str, pos: int, char: str) -> int:
    """Length of the run of ``char`` starting at ``pos``."""
    count = 0
    while pos + count < len(text) and text[pos + count] == char:
        count += 1
    return count


def _star_runs_ok(marker: str, total_left: int, total_right: int) -> bool:
    """Exact-match test for ``*``-family markers.

    A run of stars is ambiguous (``***`` is bold+italic), so italic counts
    as "wrapped" only when the runs on both sides are odd, and bold only
    when both runs hold at least two stars.
    """
    if marker == ITALIC:
        return total_left % 2 == 1 and total_right % 2 == 1
    return total_left >= 2 and total_right >= 2


def toggle_inline(
    text: str, sel_start: int, sel_end: int, marker: str
) -> TextEdit:
    start, end = sorted((sel_start, sel_end))
    m = len(marker)
    char = marker[0]
    star = char == "*"

    if start == end:
        left = _run_left(text, start, char)
        right = _run_right(text, start, char)
        if star:
            empty_pair = _star_runs_ok(marker, left, right)
        else:
            empty_pair = (
                text[max(0, start - m):start] == marker
                and text[start:start + m] == marker
            )
        if empty_pair and left >= m and right >= m:
            return TextEdit(start - m, start + m, "", start - m, start - m)
        return TextEdit(start, start, marker * 2, start + m, start + m)

    # Markdown emphasis does not tolerate spaces just inside the markers,
    # so wrap the trimmed span and leave surrounding whitespace alone.
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return TextEdit(start, start, marker * 2, start + m, start + m)

    seg = text[start:end]
    in_left = _run_right(seg, 0, char)
    in_right = _run_left(seg, len(seg), char)
    out_left = _run_left(text, start, char)
    out_right = _run_right(text, end, char)

    # Selection includes the markers ("**text**" selected).
    if seg.startswith(marker) and seg.endswith(marker) and len(seg) >= 2 * m:
        ok = (
            _star_runs_ok(marker, in_left + out_left, in_right + out_right)
            if star
            else True
        )
        if ok:
            return TextEdit(start, end, seg[m:-m], start, end - 2 * m)

    # Markers sit just outside the selection ("text" selected in "**text**").
    if text[max(0, start - m):start] == marker and text[end:end + m] == marker:
        ok = (
            _star_runs_ok(marker, in_left + out_left, in_right + out_right)
            if star
            else True
        )
        if ok and out_left >= m and out_right >= m:
            return TextEdit(
                start - m, end + m, seg, start - m, start - m + len(seg)
            )

    return TextEdit(start, end, marker + seg + marker, start + m, end + m)


def toggle_wrap(
    text: str,
    sel_start: int,
    sel_end: int,
    prefix: str,
    suffix: str,
    caret_after: bool = False,
) -> TextEdit:
    """Toggle an asymmetric ``prefix``/``suffix`` pair around the selection.

    Used for pairs where :func:`toggle_inline`'s single-marker run logic does
    not apply (``<mark>…</mark>``, ``[[…]]``).  ``caret_after`` places the
    caret after the closing marker when wrapping a selection (wikilinks).
    """
    start, end = sorted((sel_start, sel_end))
    lp, ls = len(prefix), len(suffix)

    if start == end:
        if (
            text[max(0, start - lp):start] == prefix
            and text[start:start + ls] == suffix
        ):
            return TextEdit(start - lp, start + ls, "", start - lp, start - lp)
        return TextEdit(start, start, prefix + suffix, start + lp, start + lp)

    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return TextEdit(start, start, prefix + suffix, start + lp, start + lp)

    seg = text[start:end]
    # Selection includes the markers ("<mark>text</mark>" selected).
    if seg.startswith(prefix) and seg.endswith(suffix) and len(seg) >= lp + ls:
        inner = seg[lp:len(seg) - ls]
        return TextEdit(start, end, inner, start, start + len(inner))
    # Markers sit just outside the selection.
    if (
        text[max(0, start - lp):start] == prefix
        and text[end:end + ls] == suffix
    ):
        return TextEdit(
            start - lp, end + ls, seg, start - lp, start - lp + len(seg)
        )

    replacement = prefix + seg + suffix
    if caret_after:
        pos = start + len(replacement)
        return TextEdit(start, end, replacement, pos, pos)
    return TextEdit(start, end, replacement, start + lp, start + lp + len(seg))


# ── line-level toggles ─────────────────────────────────────────────────


def _line_bounds(text: str, sel_start: int, sel_end: int) -> tuple[int, int]:
    """Full-line span touched by the selection (exclusive of newlines)."""
    start, end = sorted((sel_start, sel_end))
    # A selection ending exactly at a line start does not touch that line.
    if end > start and end > 0 and text[end - 1] == "\n":
        end -= 1
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return line_start, line_end


def _lines_edit(
    text: str,
    sel_start: int,
    sel_end: int,
    line_start: int,
    line_end: int,
    old_lines: list[str],
    new_lines: list[str],
) -> TextEdit:
    new_block = "\n".join(new_lines)
    if sel_start != sel_end:
        return TextEdit(
            line_start, line_end, new_block, line_start,
            line_start + len(new_block),
        )
    # Caret only: keep it on the same column, shifted by the prefix delta.
    delta = len(new_lines[0]) - len(old_lines[0])
    pos = min(max(line_start, sel_start + delta), line_start + len(new_lines[0]))
    return TextEdit(line_start, line_end, new_block, pos, pos)


def toggle_heading(
    text: str, sel_start: int, sel_end: int, level: int
) -> TextEdit:
    prefix = "#" * level + " "
    ls, le = _line_bounds(text, sel_start, sel_end)
    lines = text[ls:le].split("\n")
    if all(line.startswith(prefix) and not line.startswith(prefix[:-1] + "#")
           for line in lines):
        new_lines = [line[len(prefix):] for line in lines]
    else:
        new_lines = [prefix + _HEADING_RE.sub("", line) for line in lines]
    return _lines_edit(text, sel_start, sel_end, ls, le, lines, new_lines)


def _toggle_prefix(
    text: str,
    sel_start: int,
    sel_end: int,
    prefix: str,
    match: re.Pattern[str],
) -> TextEdit:
    ls, le = _line_bounds(text, sel_start, sel_end)
    lines = text[ls:le].split("\n")
    if all(match.match(line) for line in lines):
        new_lines = [match.sub("", line, count=1) for line in lines]
    else:
        new_lines = [prefix + line for line in lines]
    return _lines_edit(text, sel_start, sel_end, ls, le, lines, new_lines)


def toggle_bullet_list(text: str, sel_start: int, sel_end: int) -> TextEdit:
    return _toggle_prefix(text, sel_start, sel_end, "- ", re.compile(r"^- "))


def toggle_task_list(text: str, sel_start: int, sel_end: int) -> TextEdit:
    return _toggle_prefix(text, sel_start, sel_end, "- [ ] ", _TASK_RE)


def toggle_quote(text: str, sel_start: int, sel_end: int) -> TextEdit:
    return _toggle_prefix(text, sel_start, sel_end, "> ", re.compile(r"^> "))


def toggle_ordered_list(text: str, sel_start: int, sel_end: int) -> TextEdit:
    ls, le = _line_bounds(text, sel_start, sel_end)
    lines = text[ls:le].split("\n")
    if all(_ORDERED_RE.match(line) for line in lines):
        new_lines = [_ORDERED_RE.sub("", line, count=1) for line in lines]
    else:
        # Renumber sequentially; an existing "N. " prefix is replaced.
        new_lines = [
            f"{index + 1}. " + _ORDERED_RE.sub("", line, count=1)
            for index, line in enumerate(lines)
        ]
    return _lines_edit(text, sel_start, sel_end, ls, le, lines, new_lines)


# ── insertions ─────────────────────────────────────────────────────────


def insert_link(text: str, sel_start: int, sel_end: int) -> TextEdit:
    start, end = sorted((sel_start, sel_end))
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        label = LINK_PLACEHOLDER_TEXT
        replacement = f"[{label}]({LINK_PLACEHOLDER_URL})"
        return TextEdit(
            start, start, replacement, start + 1, start + 1 + len(label)
        )
    seg = text[start:end]
    replacement = f"[{seg}]({LINK_URL_HINT})"
    url_start = start + 1 + len(seg) + 2
    return TextEdit(
        start, end, replacement, url_start, url_start + len(LINK_URL_HINT)
    )


def _insert_block(
    text: str,
    pos: int,
    block: str,
    caret_start: int,
    caret_end: int,
    pad: bool = False,
) -> TextEdit:
    """Insert ``block`` on its own line at (or after) the caret's line.

    ``pad`` additionally keeps a blank line between the block and any
    non-blank neighbours (needed by ``---`` so it cannot form a setext
    heading with the line above).
    """
    line_start = text.rfind("\n", 0, pos) + 1
    line_end = text.find("\n", pos)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]

    if line.strip() == "":
        insert_at = line_start
        prefix = ""
        if pad and line_start >= 1:
            prev_start = text.rfind("\n", 0, line_start - 1) + 1
            if text[prev_start:line_start - 1].strip():
                prefix = "\n"  # keep a blank line above (no setext heading)
    else:
        insert_at = line_end
        prefix = "\n\n" if pad else "\n"

    rest = text[insert_at:]
    if rest.startswith("\n"):
        suffix = ""
        if pad and len(rest) > 1 and rest[1] != "\n":
            suffix = "\n"
    else:
        suffix = "\n"
        if pad and rest.strip():
            suffix = "\n\n"

    replacement = prefix + block + suffix
    base = insert_at + len(prefix)
    return TextEdit(
        insert_at, insert_at, replacement, base + caret_start, base + caret_end
    )


def insert_table(text: str, sel_start: int, sel_end: int) -> TextEdit:
    pos = max(sel_start, sel_end)
    # Caret selects the first header cell so typing replaces it directly.
    return _insert_block(text, pos, TABLE_TEMPLATE, 2, 2 + len("標題1"))


def insert_horizontal_rule(text: str, sel_start: int, sel_end: int) -> TextEdit:
    pos = max(sel_start, sel_end)
    return _insert_block(text, pos, "---", 3, 3, pad=True)


def mermaid_block(text: str, sel_start: int, sel_end: int) -> TextEdit:
    """Insert a valid starter diagram and select its first editable label."""
    pos = max(sel_start, sel_end)
    block = f"```mermaid\n{MERMAID_TEMPLATE}\n```"
    label = "步驟一"
    selection_start = block.index(label)
    selection_end = selection_start + len(label)
    return _insert_block(
        text,
        pos,
        block,
        selection_start,
        selection_end,
    )


def math_block(text: str, sel_start: int, sel_end: int) -> TextEdit:
    """Insert empty ``$$`` fences with the caret on the inner line."""
    pos = max(sel_start, sel_end)
    # pad=True: dollarmath only opens a block outside a paragraph, so the
    # fences need a blank line between them and adjacent text.
    return _insert_block(text, pos, "$$\n\n$$", 3, 3, pad=True)


def code_block(text: str, sel_start: int, sel_end: int) -> TextEdit:
    start, end = sorted((sel_start, sel_end))
    if start == end:
        return _insert_block(text, start, "```\n\n```", 4, 4)
    ls, le = _line_bounds(text, start, end)
    inner = text[ls:le]
    replacement = "```\n" + inner + "\n```"
    return TextEdit(ls, le, replacement, ls, ls + len(replacement))


# ── dispatch + Qt application layer ────────────────────────────────────


def compute_edit(
    action: str, text: str, sel_start: int, sel_end: int
) -> TextEdit | None:
    if action == "bold":
        return toggle_inline(text, sel_start, sel_end, BOLD)
    if action == "italic":
        return toggle_inline(text, sel_start, sel_end, ITALIC)
    if action == "strikethrough":
        return toggle_inline(text, sel_start, sel_end, STRIKE)
    if action == "inline_code":
        return toggle_inline(text, sel_start, sel_end, CODE)
    if action in ("h1", "h2", "h3"):
        return toggle_heading(text, sel_start, sel_end, int(action[1]))
    if action == "bullet_list":
        return toggle_bullet_list(text, sel_start, sel_end)
    if action == "ordered_list":
        return toggle_ordered_list(text, sel_start, sel_end)
    if action == "task_list":
        return toggle_task_list(text, sel_start, sel_end)
    if action == "quote":
        return toggle_quote(text, sel_start, sel_end)
    if action == "link":
        return insert_link(text, sel_start, sel_end)
    if action == "table":
        return insert_table(text, sel_start, sel_end)
    if action == "hr":
        return insert_horizontal_rule(text, sel_start, sel_end)
    if action == "code_block":
        return code_block(text, sel_start, sel_end)
    if action == "mermaid":
        return mermaid_block(text, sel_start, sel_end)
    if action == "math_inline":
        return toggle_inline(text, sel_start, sel_end, MATH)
    if action == "math_block":
        return math_block(text, sel_start, sel_end)
    if action == "wikilink":
        return toggle_wrap(
            text, sel_start, sel_end, WIKI_OPEN, WIKI_CLOSE, caret_after=True
        )
    if action == "highlight":
        return toggle_wrap(text, sel_start, sel_end, MARK_OPEN, MARK_CLOSE)
    return None


def active_format_actions(
    text: str, selection_start: int, selection_end: int
) -> set[str]:
    """Return formatting states that can be inferred without a full parser."""
    start, end = sorted((selection_start, selection_end))
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    active: set[str] = set()

    structural_line = line.lstrip(" \t")
    heading = _HEADING_RE.match(structural_line)
    if heading:
        active.add(f"h{heading.group(0).count('#')}")
    if re.match(r"^[-+*] \[[ xX]\] ", structural_line):
        active.add("task_list")
    elif re.match(r"^\d+[.)] ", structural_line):
        active.add("ordered_list")
    elif re.match(r"^[-+*] ", structural_line):
        active.add("bullet_list")
    if structural_line.startswith("> "):
        active.add("quote")

    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        column = start - line_start
        star_runs = list(re.finditer(r"\*+", line))
        for action, qualifies in (
            ("bold", lambda length: length >= 2),
            ("italic", lambda length: length % 2 == 1),
        ):
            before = sum(
                1
                for run in star_runs
                if run.end() <= column and qualifies(run.end() - run.start())
            )
            has_closer = any(
                run.start() >= column
                and qualifies(run.end() - run.start())
                for run in star_runs
            )
            if before % 2 == 1 and has_closer:
                active.add(action)

        for action, marker in (
            ("strikethrough", STRIKE),
            ("inline_code", CODE),
            ("math_inline", MATH),
        ):
            positions: list[int] = []
            offset = 0
            while True:
                found = line.find(marker, offset)
                if found < 0:
                    break
                positions.append(found)
                offset = found + len(marker)
            before = sum(
                1 for found in positions if found + len(marker) <= column
            )
            if before % 2 == 1 and any(found >= column for found in positions):
                active.add(action)
        for action, prefix, suffix in (
            ("highlight", MARK_OPEN, MARK_CLOSE),
            ("wikilink", WIKI_OPEN, WIKI_CLOSE),
        ):
            last_open = line.rfind(prefix, 0, column)
            last_close = line.rfind(suffix, 0, column)
            if last_open > last_close and line.find(suffix, column) >= 0:
                active.add(action)
        return active

    segment = text[start:end]
    in_left = _run_right(segment, 0, "*")
    in_right = _run_left(segment, len(segment), "*")
    out_left = _run_left(text, start, "*")
    out_right = _run_right(text, end, "*")
    total_left = in_left + out_left
    total_right = in_right + out_right
    if _star_runs_ok(BOLD, total_left, total_right):
        active.add("bold")
    if _star_runs_ok(ITALIC, total_left, total_right):
        active.add("italic")
    for action, marker in (
        ("strikethrough", STRIKE),
        ("inline_code", CODE),
        ("math_inline", MATH),
    ):
        if (
            segment.startswith(marker)
            and segment.endswith(marker)
            and len(segment) >= 2 * len(marker)
        ) or (
            text[max(0, start - len(marker)):start] == marker
            and text[end:end + len(marker)] == marker
        ):
            active.add(action)
    for action, prefix, suffix in (
        ("highlight", MARK_OPEN, MARK_CLOSE),
        ("wikilink", WIKI_OPEN, WIKI_CLOSE),
    ):
        if (
            segment.startswith(prefix)
            and segment.endswith(suffix)
            and len(segment) >= len(prefix) + len(suffix)
        ) or (
            text[max(0, start - len(prefix)):start] == prefix
            and text[end:end + len(suffix)] == suffix
        ):
            active.add(action)
    return active


def apply_text_edit(editor, edit: TextEdit, *, edit_block: bool = True) -> bool:
    """Apply a computed text edit and restore its requested selection."""
    from PySide6.QtGui import QTextCursor  # local import keeps logic pure
    from .text_positions import py_to_qt_position

    original = editor.toPlainText()
    updated = original[: edit.start] + edit.replacement + original[edit.end :]
    cursor = editor.textCursor()
    if edit_block:
        cursor.beginEditBlock()
    cursor.setPosition(py_to_qt_position(original, edit.start))
    cursor.setPosition(
        py_to_qt_position(original, edit.end),
        QTextCursor.MoveMode.KeepAnchor,
    )
    cursor.insertText(edit.replacement)
    if edit_block:
        cursor.endEditBlock()
    cursor.setPosition(py_to_qt_position(updated, edit.sel_start))
    cursor.setPosition(
        py_to_qt_position(updated, edit.sel_end),
        QTextCursor.MoveMode.KeepAnchor,
    )
    editor.setTextCursor(cursor)
    return True


def apply_format_action(
    editor, action: str, *, edit_block: bool = True
) -> bool:
    """Apply ``action`` to a QPlainTextEdit, normally as one undo step."""
    from .text_positions import qt_to_py_position

    text = editor.toPlainText()
    cursor = editor.textCursor()
    edit = compute_edit(
        action,
        text,
        qt_to_py_position(text, cursor.selectionStart()),
        qt_to_py_position(text, cursor.selectionEnd()),
    )
    if edit is None:
        return False
    return apply_text_edit(editor, edit, edit_block=edit_block)
