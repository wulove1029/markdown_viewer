"""Conversions between Python string indexes and Qt UTF-16 cursor offsets."""

from __future__ import annotations


def _code_units(char: str) -> int:
    return 2 if ord(char) > 0xFFFF else 1


def qt_to_py_position(text: str, position: int) -> int:
    """Convert a QTextCursor UTF-16 offset to a Python code-point index.

    Qt should never place a cursor inside a surrogate pair; if an external
    caller does, clamp to the character boundary before that pair.
    """
    target = max(0, int(position))
    units = 0
    for index, char in enumerate(text):
        width = _code_units(char)
        if units + width > target:
            return index
        units += width
        if units == target:
            return index + 1
    return len(text)


def py_to_qt_position(text: str, position: int) -> int:
    """Convert a Python code-point index to a QTextCursor UTF-16 offset."""
    end = max(0, min(int(position), len(text)))
    return sum(_code_units(char) for char in text[:end])
