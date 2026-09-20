import pytest

from app.gantt_model import GanttChart, default_gantt


def test_sections_and_tasks_remain_unique_after_removal():
    chart = GanttChart()
    first = chart.add_section("Build")
    second = chart.add_section("Build")
    assert second.name == "Build 2"
    one = chart.add_task(first.name)
    two = chart.add_task(first.name)
    assert two.start == f"after {one.task_id}"
    chart.remove_task(one.id)
    replacement = chart.add_task(second.name)
    assert len({task.id for task in chart.all_tasks()}) == 2
    assert chart.task(replacement.id) is replacement
    assert chart.section(second.name) is second


def test_empty_chart_creates_default_section_and_missing_lookups_are_explicit():
    chart = GanttChart()
    task = chart.add_task("missing")
    assert chart.sections[0].name == "Tasks"
    assert task.start == "2026-07-01"
    assert chart.find_task("absent") is None
    assert chart.find_section("absent") is None
    with pytest.raises(KeyError):
        chart.task("absent")
    with pytest.raises(KeyError):
        chart.section("absent")


def test_default_charts_do_not_share_mutable_state():
    first, second = default_gantt(), default_gantt()
    first.sections[0].tasks[0].tags.append("done")
    first.remove_task("T2")
    assert second.task("T1").tags == ["active"]
    assert second.task("T2").start == "after plan"
