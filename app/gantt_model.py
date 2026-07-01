"""Pure data model for the visual Mermaid Gantt editor."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GanttTask:
    id: str
    name: str
    tags: list[str] = field(default_factory=list)
    task_id: str = ""
    start: str = ""
    duration: str = "1d"


@dataclass
class GanttSection:
    name: str
    tasks: list[GanttTask] = field(default_factory=list)


@dataclass
class GanttChart:
    title: str = "Project Plan"
    date_format: str = "YYYY-MM-DD"
    axis_format: str = ""
    sections: list[GanttSection] = field(default_factory=list)

    def task(self, task_id: str) -> GanttTask:
        found = self.find_task(task_id)
        if found is None:
            raise KeyError(task_id)
        return found

    def find_task(self, task_id: str) -> GanttTask | None:
        for section in self.sections:
            for task in section.tasks:
                if task.id == task_id:
                    return task
        return None

    def section(self, name: str) -> GanttSection:
        found = self.find_section(name)
        if found is None:
            raise KeyError(name)
        return found

    def find_section(self, name: str) -> GanttSection | None:
        for section in self.sections:
            if section.name == name:
                return section
        return None

    def add_section(self, name: str = "Section") -> GanttSection:
        section = GanttSection(name=_unique_section_name(self.sections, name))
        self.sections.append(section)
        return section

    def add_task(self, section_name: str | None = None) -> GanttTask:
        if not self.sections:
            self.add_section("Tasks")
        section = self.find_section(section_name or "") or self.sections[-1]
        task = GanttTask(
            id=self.next_task_id(),
            name="New task",
            task_id=f"task{len(self.all_tasks()) + 1}",
            start=_default_task_start(section),
            duration="1d",
        )
        section.tasks.append(task)
        return task

    def remove_task(self, task_id: str) -> None:
        for section in self.sections:
            section.tasks = [task for task in section.tasks if task.id != task_id]

    def all_tasks(self) -> list[GanttTask]:
        tasks: list[GanttTask] = []
        for section in self.sections:
            tasks.extend(section.tasks)
        return tasks

    def next_task_id(self) -> str:
        used = {task.id for task in self.all_tasks()}
        idx = 1
        while f"T{idx}" in used:
            idx += 1
        return f"T{idx}"


def default_gantt() -> GanttChart:
    chart = GanttChart(title="Project Plan", date_format="YYYY-MM-DD")
    section = chart.add_section("Build")
    section.tasks.append(
        GanttTask(
            id="T1",
            name="Plan",
            tags=["active"],
            task_id="plan",
            start="2026-07-01",
            duration="2d",
        )
    )
    section.tasks.append(
        GanttTask(
            id="T2",
            name="Build",
            task_id="build",
            start="after plan",
            duration="3d",
        )
    )
    return chart


def _default_task_start(section: GanttSection) -> str:
    if section.tasks and section.tasks[-1].task_id:
        return f"after {section.tasks[-1].task_id}"
    return "2026-07-01"


def _unique_section_name(sections: list[GanttSection], base: str) -> str:
    names = {section.name for section in sections}
    if base not in names:
        return base
    idx = 2
    while f"{base} {idx}" in names:
        idx += 1
    return f"{base} {idx}"
