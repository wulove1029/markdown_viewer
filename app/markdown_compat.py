"""Compatibility checks at the source/Office editor boundary.

The Office editor still reads and writes Markdown, but a visual editor may
normalise syntax it does not understand.  These helpers deliberately flag
only constructs that this project already documents as unsupported so the
warning stays useful instead of firing for ordinary Markdown.
"""

from __future__ import annotations

import hashlib
import re


FRONT_MATTER = "front_matter"
WIKI_LINKS = "wiki_links"
OBSIDIAN_CALLOUTS = "obsidian_callouts"
REFERENCE_LINK_TITLES = "reference_link_titles"

RISK_LABELS = {
    FRONT_MATTER: "front matter／頂端分隔線區塊",
    WIKI_LINKS: "wiki-links／嵌入連結",
    OBSIDIAN_CALLOUTS: "Obsidian callouts",
    REFERENCE_LINK_TITLES: "帶標題的參照式連結",
}

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(?:[^`]|`(?!\1))*?\1")
_WIKI_LINK_RE = re.compile(r"!?\[\[[^\]\n]+\]\]")
_CALLOUT_RE = re.compile(
    r"^\s*(?:>\s*)+\[![A-Za-z0-9_-]+\][+-]?(?:\s|$)"
)
_YAML_OPEN_RE = re.compile(r"^\ufeff?---[ \t]*$")
_TOML_OPEN_RE = re.compile(r"^\ufeff?\+\+\+[ \t]*$")
_YAML_CLOSE_RE = re.compile(r"^(?:---|\.\.\.)[ \t]*$")
_TOML_CLOSE_RE = re.compile(r"^\+\+\+[ \t]*$")
_REFERENCE_DEFINITION_RE = re.compile(
    r"^ {0,3}\[[^\]\n]+\]:[ \t]*(?:<[^>\n]*>|[^ \t]+)(?P<tail>[ \t].*)?$"
)


def _has_front_matter(lines: list[str]) -> bool:
    if not lines:
        return False
    first = lines[0]
    if _YAML_OPEN_RE.fullmatch(first):
        closing_re = _YAML_CLOSE_RE
    elif _TOML_OPEN_RE.fullmatch(first):
        closing_re = _TOML_CLOSE_RE
    else:
        return False
    for index, line in enumerate(lines[1:], start=1):
        if closing_re.fullmatch(line):
            body = lines[1:index]
            meaningful = [candidate for candidate in body if candidate.strip()]
            # A top ``---`` plus a later closing delimiter is ambiguous with
            # thematic rules, but Vditor may normalize that layout too. Warn
            # conservatively rather than silently risking a source rewrite.
            return bool(meaningful)
    return False


def _lines_outside_fences(lines: list[str]):
    fence_char = ""
    fence_length = 0
    for line in lines:
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = ""
                fence_length = 0
            # Preserve line adjacency for multi-line reference definitions.
            yield ""
            continue
        yield "" if fence_char else _INLINE_CODE_RE.sub("", line)


def _has_reference_link_title(lines: tuple[str, ...]) -> bool:
    for index, line in enumerate(lines):
        match = _REFERENCE_DEFINITION_RE.fullmatch(line)
        if match is None:
            continue
        tail = (match.group("tail") or "").lstrip(" \t")
        if tail.startswith(('"', "'", "(")):
            return True
        if index + 1 < len(lines):
            continuation = lines[index + 1]
            indent = len(continuation) - len(continuation.lstrip(" "))
            if indent <= 3 and continuation.lstrip(" ").startswith(
                ('"', "'", "(")
            ):
                return True
    return False


def office_compatibility_risks(markdown: str) -> tuple[str, ...]:
    """Return stable risk codes for syntax the Office editor may normalise."""
    lines = str(markdown or "").splitlines()
    risks: list[str] = []
    if _has_front_matter(lines):
        risks.append(FRONT_MATTER)

    visible_lines = tuple(_lines_outside_fences(lines))
    if any(_WIKI_LINK_RE.search(line) for line in visible_lines):
        risks.append(WIKI_LINKS)
    if any(_CALLOUT_RE.match(line) for line in visible_lines):
        risks.append(OBSIDIAN_CALLOUTS)
    if _has_reference_link_title(visible_lines):
        risks.append(REFERENCE_LINK_TITLES)
    return tuple(risks)


def office_risk_labels(risks: tuple[str, ...]) -> tuple[str, ...]:
    """Convert risk codes to concise, user-facing labels."""
    return tuple(RISK_LABELS.get(risk, risk) for risk in risks)


def office_warning_fingerprint(markdown: str, risks: tuple[str, ...]) -> str:
    """Identify one acknowledged document revision without retaining its text."""
    payload = "\0".join((*risks, markdown)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
