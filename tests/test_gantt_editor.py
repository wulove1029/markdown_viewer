"""Tests for the Gantt visual editor widget."""

from app.gantt_editor import GanttEditor
from app.gantt_mermaid import parse_gantt


SAMPLE = """gantt
    title Mermaid Workspace Rollout
    dateFormat  YYYY-MM-DD
    section Build
    Workspace      :active, ws, 2026-07-01, 4d
"""


def test_gantt_editor_sets_chart_and_updates_task(qapp):
    editor = GanttEditor()
    editor.set_chart(parse_gantt(SAMPLE).require_chart())
    calls = []
    editor.graph_changed.connect(lambda _chart: calls.append(True))

    task = editor.chart().sections[0].tasks[0]
    editor.select_task(task.id)
    editor.set_task_name(task.id, "Workspace design")
    editor.set_task_start(task.id, "2026-07-02")
    editor.set_task_duration(task.id, "5d")

    chart = editor.chart()
    updated = chart.sections[0].tasks[0]
    assert updated.name == "Workspace design"
    assert updated.start == "2026-07-02"
    assert updated.duration == "5d"
    assert calls


def test_gantt_editor_adds_section_and_task(qapp):
    editor = GanttEditor()
    editor.set_chart(parse_gantt(SAMPLE).require_chart())
    editor.add_section("Release")
    editor.add_task("Release")

    chart = editor.chart()
    assert [section.name for section in chart.sections] == ["Build", "Release"]
    assert chart.sections[1].tasks[0].name == "New task"
