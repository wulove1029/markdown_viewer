"""Small Mermaid source cleanup helpers."""

from __future__ import annotations


def format_mermaid_source(source: str) -> str:
    """Clean safe whitespace without changing Mermaid structure."""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    out: list[str] = []
    blank = 0
    for line in lines:
        if not line.strip():
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(line)
    return "\n".join(out)
