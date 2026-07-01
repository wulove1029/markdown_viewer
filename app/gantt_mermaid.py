"""Parse and render the Mermaid Gantt subset used by Visual mode."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .gantt_model import GanttChart, GanttSection, GanttTask, default_gantt


class GanttParseError(ValueError):
    pass


@dataclass(frozen=True)
class GanttParseResult:
    chart: GanttChart | None = None
    reason: str = ""

    @property
    def supported(self) -> bool:
        return self.chart is not None

    def require_chart(self) -> GanttChart:
        if self.chart is None:
            raise GanttParseError(self.reason or "Unsupported Mermaid Gantt source.")
        return self.chart


_TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_KNOWN_TAGS = {"active", "done", "crit", "milestone"}


def parse_gantt(source: str) -> GanttParseResult:
    text = source.strip()
    if not text:
        return GanttParseResult(default_gantt())
    try:
        return GanttParseResult(_parse(text))
    except GanttParseError as exc:
        return GanttParseResult(None, str(exc))


def render_gantt(chart: GanttChart) -> str:
    lines = ["gantt"]
    if chart.title.strip():
        lines.append(f"    title {chart.title.strip()}")
    if chart.date_format.strip():
        lines.append(f"    dateFormat  {chart.date_format.strip()}")
    if chart.axis_format.strip():
        lines.append(f"    axisFormat  {chart.axis_format.strip()}")

    task_width = max(
        12,
        *(len(task.name.strip()) + 2 for section in chart.sections for task in section.tasks),
    )
    for section in chart.sections:
        lines.append(f"    section {section.name.strip() or 'Tasks'}")
        for task in section.tasks:
            lines.append(f"    {task.name.strip():<{task_width}}:{_render_task_spec(task)}")
    return "\n".join(lines)


def _parse(text: str) -> GanttChart:
    lines = [line.rstrip() for line in text.splitlines()]
    content = [line.strip() for line in lines if line.strip() and not line.strip().startswith("%%")]
    if not content or content[0].lower() != "gantt":
        raise GanttParseError("Visual mode supports Mermaid gantt diagrams only.")

    chart = GanttChart()
    current: GanttSection | None = None
    task_index = 1
    for idx, line in enumerate(content[1:], start=2):
        lowered = line.lower()
        if lowered.startswith("title "):
            chart.title = line[6:].strip()
            continue
        if lowered.startswith("dateformat "):
            chart.date_format = line[len("dateFormat") :].strip()
            continue
        if lowered.startswith("axisformat "):
            chart.axis_format = line[len("axisFormat") :].strip()
            continue
        if lowered.startswith("section "):
            current = chart.add_section(line[8:].strip() or "Tasks")
            continue
        if ":" not in line:
            raise GanttParseError(f"Line {idx}: expected a Gantt task or directive.")
        if current is None:
            current = chart.add_section("Tasks")
        task = _parse_task(line, f"T{task_index}", idx)
        task_index += 1
        current.tasks.append(task)

    if not chart.sections:
        return default_gantt()
    return chart


def _parse_task(line: str, task_id: str, line_no: int) -> GanttTask:
    name, spec = line.split(":", 1)
    task_name = name.strip()
    if not task_name:
        raise GanttParseError(f"Line {line_no}: task name is required.")
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    if len(parts) < 2:
        raise GanttParseError(f"Line {line_no}: task start and duration are required.")

    duration = parts[-1]
    start = parts[-2]
    metadata = parts[:-2]
    tags: list[str] = []
    mermaid_id = ""
    for token in metadata:
        lowered = token.lower()
        if lowered in _KNOWN_TAGS:
            tags.append(lowered)
        elif _TASK_ID_RE.match(token):
            mermaid_id = token
        else:
            raise GanttParseError(f"Line {line_no}: unsupported task marker '{token}'.")

    return GanttTask(
        id=task_id,
        name=task_name,
        tags=tags,
        task_id=mermaid_id,
        start=start,
        duration=duration,
    )


def _render_task_spec(task: GanttTask) -> str:
    parts: list[str] = []
    parts.extend(tag for tag in task.tags if tag)
    if task.task_id.strip():
        parts.append(task.task_id.strip())
    parts.append(task.start.strip() or "2026-07-01")
    parts.append(task.duration.strip() or "1d")
    return ", ".join(parts)
