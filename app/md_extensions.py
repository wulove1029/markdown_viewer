"""Small markdown-it extensions shared by the preview and the Office exports.

``mdit_py_plugins`` ships no ``==mark==`` plugin, so the rule lives here.  The
module has no Qt dependency: the DOCX/PPTX parsers import it too.
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.rules_inline.state_inline import Delimiter, StateInline

_MARKER = "="
_MARKER_CODE = ord(_MARKER)


def _tokenize(state: StateInline, silent: bool) -> bool:
    if silent or state.src[state.pos] != _MARKER:
        return False
    scanned = state.scanDelims(state.pos, True)
    length = scanned.length
    if length < 2:
        return False
    if length % 2:
        state.push("text", "", 0).content = _MARKER
        length -= 1
    for _ in range(length // 2):
        state.push("text", "", 0).content = _MARKER * 2
        state.delimiters.append(
            Delimiter(
                marker=_MARKER_CODE,
                length=0,  # disable the "rule of 3" length checks
                token=len(state.tokens) - 1,
                end=-1,
                open=scanned.can_open,
                close=scanned.can_close,
            )
        )
    state.pos += scanned.length
    return True


def _post_process_delimiters(state: StateInline, delimiters: list[Delimiter]) -> None:
    lone_markers: list[int] = []
    for start in delimiters:
        if start.marker != _MARKER_CODE or start.end == -1:
            continue
        end = delimiters[start.end]
        opener = state.tokens[start.token]
        opener.type, opener.tag, opener.nesting = "mark_open", "mark", 1
        opener.markup, opener.content = _MARKER * 2, ""
        closer = state.tokens[end.token]
        closer.type, closer.tag, closer.nesting = "mark_close", "mark", -1
        closer.markup, closer.content = _MARKER * 2, ""
        before = state.tokens[end.token - 1]
        if before.type == "text" and before.content == _MARKER:
            lone_markers.append(end.token - 1)
    # An odd run leaves one literal "=" in front of the closer; move it behind.
    while lone_markers:
        i = lone_markers.pop()
        j = i + 1
        while j < len(state.tokens) and state.tokens[j].type == "mark_close":
            j += 1
        j -= 1
        if i != j:
            state.tokens[i], state.tokens[j] = state.tokens[j], state.tokens[i]


def _post_process(state: StateInline) -> None:
    _post_process_delimiters(state, state.delimiters)
    for meta in state.tokens_meta:
        if meta and "delimiters" in meta:
            _post_process_delimiters(state, meta["delimiters"])


def mark_plugin(md: MarkdownIt) -> None:
    """``==text==`` -> ``<mark>text</mark>`` (token types mark_open/mark_close)."""
    md.inline.ruler.before("emphasis", "mark", _tokenize)
    md.inline.ruler2.before("emphasis", "mark", _post_process)


def is_toc_marker(text: str) -> bool:
    """True for a paragraph whose whole content is ``[TOC]`` (any case)."""
    return text.strip().casefold() == "[toc]"
