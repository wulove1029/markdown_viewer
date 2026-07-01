"""Visual-editor routing helpers for Mermaid workspace."""

from __future__ import annotations

from .flowchart_mermaid import parse_flowchart
from .gantt_mermaid import parse_gantt


def visual_editor_kind(source: str) -> str:
    if parse_flowchart(source).supported:
        return "flowchart"
    if parse_gantt(source).supported:
        return "gantt"
    return "unsupported"
