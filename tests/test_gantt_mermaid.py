"""Tests for the Mermaid Gantt visual editor subset."""

from app.gantt_mermaid import parse_gantt, render_gantt


SAMPLE = """gantt
    title Mermaid Workspace Rollout
    dateFormat  YYYY-MM-DD
    axisFormat  %m-%d
    section Build
    Workspace      :active, ws, 2026-07-01, 4d
    Markdown link  :md, after ws, 3d
    Polish         :polish, after md, 2d
"""


def test_parse_gantt_template():
    result = parse_gantt(SAMPLE)
    assert result.supported
    chart = result.require_chart()
    assert chart.title == "Mermaid Workspace Rollout"
    assert chart.date_format == "YYYY-MM-DD"
    assert chart.axis_format == "%m-%d"
    assert [section.name for section in chart.sections] == ["Build"]
    assert [task.name for task in chart.sections[0].tasks] == [
        "Workspace",
        "Markdown link",
        "Polish",
    ]
    assert chart.sections[0].tasks[0].tags == ["active"]
    assert chart.sections[0].tasks[0].task_id == "ws"
    assert chart.sections[0].tasks[1].start == "after ws"
    assert chart.sections[0].tasks[2].duration == "2d"


def test_render_gantt_is_stable():
    chart = parse_gantt(SAMPLE).require_chart()
    assert render_gantt(chart) == SAMPLE.rstrip()


def test_parse_rejects_non_gantt_source():
    result = parse_gantt("flowchart LR\nA --> B\n")
    assert not result.supported
    assert result.reason
