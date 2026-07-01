"""Tests for Mermaid visual editor routing."""

from app.mermaid_visual import visual_editor_kind


def test_gantt_source_routes_to_gantt_visual_editor():
    assert (
        visual_editor_kind(
            """gantt
    title Plan
    dateFormat  YYYY-MM-DD
    section Build
    Work :active, work, 2026-07-01, 2d
"""
        )
        == "gantt"
    )


def test_flowchart_source_routes_to_flowchart_visual_editor():
    assert visual_editor_kind("flowchart LR\nA --> B\n") == "flowchart"


def test_other_mermaid_source_routes_to_unsupported_visual_editor():
    assert visual_editor_kind("sequenceDiagram\nA->>B: hi\n") == "unsupported"
