"""Parser for ``markdownviewer://open?path=<percent-encoded absolute path>``.

Pure functions, no Qt.  The parser only *extracts and validates* a local path;
it never touches the filesystem and never executes anything.
"""

from __future__ import annotations

import ntpath
import re
from urllib.parse import unquote

SCHEME = "markdownviewer"
_ABSOLUTE_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
ALLOWED_EXTENSIONS = (".md", ".markdown", ".pdf")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def is_protocol_url(arg: str) -> bool:
    """True when *arg* looks like a markdownviewer URL (valid or not)."""
    return isinstance(arg, str) and arg.strip()[: len(SCHEME) + 1].lower() == SCHEME + ":"


MAX_HEADING_LENGTH = 500


def parse_open_url(url: str) -> str | None:
    """Return the local document path requested by *url*, or None if invalid."""
    target = parse_open_target(url)
    return target[0] if target else None


def parse_open_target(url: str) -> tuple[str, dict | None] | None:
    """Return ``(path, anchor)`` for *url*, or None if the URL is invalid.

    *anchor* is ``{"heading": text}`` (``&heading=``) or ``{"line": n}``
    (``&line=N``, one-based), else None. A malformed anchor is dropped (the
    document still opens); the path rules are unchanged.
    """
    if not is_protocol_url(url):
        return None
    rest = url.strip()[len(SCHEME) + 1 :]
    if rest.startswith("//"):
        rest = rest[2:]
    rest = rest.split("#", 1)[0]
    action, _, query = rest.partition("?")
    if action.strip("/").lower() != "open":
        return None
    path = None
    heading = None
    line = None
    for part in query.split("&"):
        name, sep, value = part.partition("=")
        if not sep:
            continue
        key = unquote(name)
        if key == "path" and path is None:
            path = unquote(value)
        elif key == "heading" and heading is None:
            heading = unquote(value).strip()
        elif key == "line" and line is None:
            line = unquote(value).strip()
    if not path or not _is_safe_absolute(path):
        return None
    anchor: dict | None = None
    if heading and len(heading) <= MAX_HEADING_LENGTH and not _CONTROL.search(heading):
        anchor = {"heading": heading}
    elif line and line.isascii() and line.isdigit() and 1 <= int(line) <= 10**9:
        anchor = {"line": int(line)}
    return path, anchor


def _is_safe_absolute(path: str) -> bool:
    if _CONTROL.search(path):
        return False
    if path.startswith(("\\\\", "//")):  # UNC, \\?\ and \\.\ device paths
        return False
    if not _ABSOLUTE_DRIVE.match(path):
        return False
    # Parent-directory segments are refused before any resolve().
    if ".." in re.split(r"[\\/]+", path):
        return False
    # Files only, by extension: folders, .lnk/.url, executables never pass.
    return ntpath.splitext(path)[1].lower() in ALLOWED_EXTENSIONS
