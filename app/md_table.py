"""GFM pipe tables as a structured model, and back again.

The preview's grid editor wants a table as rows and columns and then has to
write plain Markdown back into the file, so this module is the single place
that knows pipe-table syntax. Deliberately Qt-free, like ``inline_edit``, so
the syntax rules stay unit-testable headless.

The model is a plain dict, cheap to hand across the Qt/JS boundary::

    {
        "headers": ["Bytes read back", "Meaning"],
        "aligns": ["", "center"],          # "" | "left" | "center" | "right"
        "rows": [["`FF FF FF FF`", "**never calibrated**"]],
        "indent": "",                      # leading spaces of the first line
    }

Every cell holds that cell's *Markdown source* -- inline markup is kept
verbatim -- with the ``\\|`` escape already decoded and the padding whitespace
removed. ``aligns`` always has one entry per header, and every row is padded
or truncated to ``len(headers)``, so the editor never has to reason about
ragged rows.

The guarantee the editor leans on is::

    parse_table(serialize_table(m)) == m

for every ``m`` that ``parse_table`` produced. Note the direction: serializing
is deliberately *normalizing* -- outer pipes are always written, columns are
padded to a common display width, and the delimiter row is rebuilt -- so
``serialize_table(parse_table(s)) == s`` does not hold and is not meant to.
Editing a table reformats it; that is the intended trade for a stable model.
"""

from __future__ import annotations

import re
import unicodedata

# GFM allows a table to be indented by up to three spaces; a fourth makes it
# an indented code block, which is a different block entirely.
_INDENT_RE = re.compile(r"^( {0,3})(?=\S)")
_DELIMITER_RE = re.compile(r"^(:?)-+(:?)$")

_MIN_DASHES = 3


def display_width(text: str) -> int:
    """Return how many monospaced cells *text* occupies.

    Padding by ``len()`` would leave a Chinese table visibly crooked in the
    raw Markdown, and these documents are mostly Chinese, so column widths are
    measured the way a terminal measures them: East Asian Wide/Fullwidth
    characters (and emoji, which Unicode also classifies as Wide) take two
    cells, combining marks take none, everything else takes one.
    """
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def parse_table(source: str) -> dict | None:
    """Parse one block's Markdown *source* into a table model, or None.

    Returns None rather than raising for anything that is not a GFM pipe
    table: the caller is a double-click handler that must fall back to plain
    text editing, so "not a table" is an ordinary answer, not an error.
    """
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) < 2:
        return None
    # A blank line ends a table in GFM. The caller hands us exactly one block,
    # so an interior blank means we were given something else entirely -- and
    # silently dropping the tail would lose text on the way back out.
    if any(not line.strip() for line in lines):
        return None

    indent_match = _INDENT_RE.match(lines[0])
    if indent_match is None:
        return None
    stripped = [line.strip() for line in lines]

    headers = _split_row(stripped[0])
    if not headers:
        return None
    aligns = _parse_delimiter_row(stripped[1])
    # GFM requires the delimiter row to have exactly as many cells as the
    # header; a mismatch is not a table at all, not a table to be repaired.
    if aligns is None or len(aligns) != len(headers):
        return None

    rows = []
    for line in stripped[2:]:
        cells = _split_row(line)
        if cells is None:
            return None
        rows.append(_fit(cells, len(headers)))

    return {
        "headers": headers,
        "aligns": aligns,
        "rows": rows,
        "indent": indent_match.group(1),
    }


def serialize_table(model: dict) -> str:
    """Render a table model back to Markdown, without a trailing newline.

    Always emits the normalized form -- leading and trailing pipes on every
    row, cells padded to a common display width, delimiter row rebuilt from
    ``aligns`` -- because a grid editor has no way to preserve the author's
    original spacing and a single predictable layout keeps diffs readable.
    """
    headers = [str(cell) for cell in (model.get("headers") or [])]
    if not headers:
        return ""
    aligns = _fit([str(a) for a in (model.get("aligns") or [])], len(headers))
    rows = [
        _fit([str(cell) for cell in row], len(headers))
        for row in (model.get("rows") or [])
    ]
    indent = model.get("indent", "")

    header_cells = [_escape(cell) for cell in headers]
    row_cells = [[_escape(cell) for cell in row] for row in rows]

    widths = []
    for index, align in enumerate(aligns):
        content = max(
            [display_width(header_cells[index])]
            + [display_width(row[index]) for row in row_cells]
        )
        widths.append(max(content, _MIN_DASHES + _colon_count(align)))

    lines = [_render_row(header_cells, widths), _render_delimiter(aligns, widths)]
    lines += [_render_row(row, widths) for row in row_cells]
    return "\n".join(indent + line for line in lines)


def _split_row(line: str) -> list[str] | None:
    """Split one stripped table line into finished cells, or None.

    None means "no unescaped pipe on this line", which disqualifies the whole
    block: a pipe table needs a real delimiter on every one of its rows.
    """
    raw, has_delimiter = _split_escaped(line)
    if not has_delimiter:
        return None
    # GFM drops one optional leading and one optional trailing pipe, so
    # ``| a | b |`` and ``a | b`` describe the same two columns.
    if len(raw) >= 2 and line.startswith("|") and not raw[0].strip():
        raw = raw[1:]
    if len(raw) >= 2 and line.endswith("|") and not raw[-1].strip():
        raw = raw[:-1]
    return [_unescape(cell).strip() for cell in raw]


def _split_escaped(line: str) -> tuple[list[str], bool]:
    """Cut *line* at unescaped pipes, scanning character by character.

    A naive ``split("|")`` would break every cell containing ``\\|``. A
    backslash always swallows the character behind it, which is what makes
    ``\\\\|`` a literal backslash followed by a real column delimiter.
    """
    cells: list[str] = []
    current: list[str] = []
    has_delimiter = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            current.append(char)
            current.append(line[index + 1])
            index += 2
            continue
        if char == "|":
            has_delimiter = True
            cells.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current))
    return cells, has_delimiter


def _unescape(cell: str) -> str:
    """Decode ``\\|`` to ``|`` while leaving every other escape untouched.

    Only the pipe escape exists to serve the table syntax; ``\\*`` and friends
    are inline Markdown the cell still owns and must keep verbatim.
    """
    out: list[str] = []
    index = 0
    while index < len(cell):
        char = cell[index]
        if char == "\\" and index + 1 < len(cell):
            following = cell[index + 1]
            out.append("|" if following == "|" else char + following)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _escape(cell: str) -> str:
    """Re-escape a model cell, the exact inverse of :func:`_unescape`.

    Scanning in the same backslash-pairs order is what keeps the round trip
    exact: a backslash already carrying an escape is copied with its partner
    untouched, so ``\\\\`` never inflates, while a bare pipe becomes ``\\|``.
    """
    out: list[str] = []
    index = 0
    while index < len(cell):
        char = cell[index]
        if char == "\\" and index + 1 < len(cell):
            out.append(char)
            out.append(cell[index + 1])
            index += 2
            continue
        out.append("\\|" if char == "|" else char)
        index += 1
    return "".join(out)


def _parse_delimiter_row(line: str) -> list[str] | None:
    """Read alignments out of the ``|---|:---:|`` row, or None if it isn't one."""
    cells = _split_row(line)
    if not cells:
        return None
    aligns = []
    for cell in cells:
        match = _DELIMITER_RE.match(cell)
        if match is None:
            return None
        left, right = bool(match.group(1)), bool(match.group(2))
        if left and right:
            aligns.append("center")
        elif left:
            aligns.append("left")
        elif right:
            aligns.append("right")
        else:
            aligns.append("")
    return aligns


def _fit(cells: list[str], width: int) -> list[str]:
    """Pad with empty strings or truncate, matching GFM's ragged-row rule."""
    return (cells + [""] * width)[:width]


def _colon_count(align: str) -> int:
    if align == "center":
        return 2
    return 1 if align in ("left", "right") else 0


def _render_row(cells: list[str], widths: list[int]) -> str:
    padded = [
        cell + " " * (width - display_width(cell))
        for cell, width in zip(cells, widths)
    ]
    # The space before the closing pipe is load-bearing: a cell ending in a
    # backslash would otherwise escape that pipe away.
    return "| " + " | ".join(padded) + " |"


def _render_delimiter(aligns: list[str], widths: list[int]) -> str:
    cells = []
    for align, width in zip(aligns, widths):
        if align == "center":
            cells.append(":" + "-" * (width - 2) + ":")
        elif align == "left":
            cells.append(":" + "-" * (width - 1))
        elif align == "right":
            cells.append("-" * (width - 1) + ":")
        else:
            cells.append("-" * width)
    return "| " + " | ".join(cells) + " |"
