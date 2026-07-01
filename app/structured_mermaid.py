"""Parse/render structured Mermaid diagram types for Visual mode."""

from __future__ import annotations

from dataclasses import dataclass, field
import re


class StructuredParseError(ValueError):
    pass


@dataclass
class StructuredRow:
    role: str
    cells: dict[str, str] = field(default_factory=dict)


@dataclass
class StructuredDiagram:
    kind: str
    header: str
    rows: list[StructuredRow] = field(default_factory=list)


@dataclass(frozen=True)
class StructuredParseResult:
    diagram: StructuredDiagram | None = None
    reason: str = ""

    @property
    def supported(self) -> bool:
        return self.diagram is not None

    def require_diagram(self) -> StructuredDiagram:
        if self.diagram is None:
            raise StructuredParseError(self.reason or "Unsupported Mermaid diagram.")
        return self.diagram


_SEQUENCE_MESSAGE_RE = re.compile(
    r"^(?P<source>[^:-]+?)\s*(?P<arrow>-->>|->>|-->|->)\s*(?P<target>[^:]+?)\s*:\s*(?P<text>.*)$"
)
_CLASS_BLOCK_RE = re.compile(r"^class\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*\{\s*$")
_CLASS_REL_RE = re.compile(
    r'^(?P<source>[A-Za-z][A-Za-z0-9_]*)(?:\s+"(?P<left>[^"]+)")?\s+'
    r'(?P<arrow>[<>|o*}A-Za-z.\-]+)\s+(?:"(?P<right>[^"]+)"\s+)?'
    r"(?P<target>[A-Za-z][A-Za-z0-9_]*)$"
)
_STATE_TRANSITION_RE = re.compile(
    r"^(?P<source>.+?)\s+-->\s+(?P<target>.+?)(?:\s*:\s*(?P<label>.*))?$"
)
_ER_REL_RE = re.compile(
    r"^(?P<source>[A-Za-z][A-Za-z0-9_]*)\s+(?P<connector>\S+)\s+"
    r"(?P<target>[A-Za-z][A-Za-z0-9_]*)\s*:\s*(?P<label>.+)$"
)
_ER_ENTITY_RE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*\{\s*$")


def parse_structured_mermaid(source: str) -> StructuredParseResult:
    text = source.strip()
    if not text:
        return StructuredParseResult(None, "Empty Mermaid source.")
    try:
        first = _first_content_line(text).lower()
        if first == "sequencediagram":
            return StructuredParseResult(_parse_sequence(text))
        if first == "classdiagram":
            return StructuredParseResult(_parse_class(text))
        if first in ("statediagram-v2", "statediagram"):
            return StructuredParseResult(_parse_state(text, first))
        if first == "erdiagram":
            return StructuredParseResult(_parse_er(text))
    except StructuredParseError as exc:
        return StructuredParseResult(None, str(exc))
    return StructuredParseResult(None, "No structured visual editor is available.")


def render_structured_mermaid(diagram: StructuredDiagram) -> str:
    if diagram.kind == "sequence":
        return _render_sequence(diagram)
    if diagram.kind == "class":
        return _render_class(diagram)
    if diagram.kind == "state":
        return _render_state(diagram)
    if diagram.kind == "er":
        return _render_er(diagram)
    raise StructuredParseError(f"Unsupported structured diagram kind: {diagram.kind}")


def _first_content_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped
    return ""


def _content_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("%%")
    ]


def _parse_sequence(text: str) -> StructuredDiagram:
    diagram = StructuredDiagram(kind="sequence", header="sequenceDiagram")
    for idx, line in enumerate(_content_lines(text)[1:], start=2):
        lowered = line.lower()
        if lowered.startswith("participant "):
            diagram.rows.append(
                StructuredRow("participant", {"name": line[len("participant ") :].strip()})
            )
            continue
        match = _SEQUENCE_MESSAGE_RE.match(line)
        if match:
            diagram.rows.append(StructuredRow("message", match.groupdict()))
            continue
        raise StructuredParseError(f"Line {idx}: unsupported sequence statement.")
    return diagram


def _parse_class(text: str) -> StructuredDiagram:
    diagram = StructuredDiagram(kind="class", header="classDiagram")
    lines = _content_lines(text)
    current_class = ""
    for idx, line in enumerate(lines[1:], start=2):
        if current_class:
            if line == "}":
                current_class = ""
                continue
            diagram.rows.append(
                StructuredRow("member", {"class": current_class, "member": line})
            )
            continue
        block = _CLASS_BLOCK_RE.match(line)
        if block:
            current_class = block.group("name")
            diagram.rows.append(StructuredRow("class", {"class": current_class}))
            continue
        rel = _CLASS_REL_RE.match(line)
        if rel:
            diagram.rows.append(StructuredRow("relation", _clean_groups(rel.groupdict())))
            continue
        raise StructuredParseError(f"Line {idx}: unsupported class statement.")
    if current_class:
        raise StructuredParseError("Class block is missing a closing brace.")
    return diagram


def _parse_state(text: str, header: str) -> StructuredDiagram:
    rendered_header = "stateDiagram-v2" if header == "statediagram-v2" else "stateDiagram"
    diagram = StructuredDiagram(kind="state", header=rendered_header)
    for idx, line in enumerate(_content_lines(text)[1:], start=2):
        match = _STATE_TRANSITION_RE.match(line)
        if not match:
            raise StructuredParseError(f"Line {idx}: unsupported state statement.")
        diagram.rows.append(StructuredRow("transition", _clean_groups(match.groupdict())))
    return diagram


def _parse_er(text: str) -> StructuredDiagram:
    diagram = StructuredDiagram(kind="er", header="erDiagram")
    current_entity = ""
    for idx, line in enumerate(_content_lines(text)[1:], start=2):
        if current_entity:
            if line == "}":
                current_entity = ""
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                raise StructuredParseError(f"Line {idx}: unsupported ER field.")
            diagram.rows.append(
                StructuredRow(
                    "field",
                    {
                        "entity": current_entity,
                        "field_type": parts[0],
                        "field_name": parts[1],
                    },
                )
            )
            continue
        entity = _ER_ENTITY_RE.match(line)
        if entity:
            current_entity = entity.group("name")
            diagram.rows.append(StructuredRow("entity", {"entity": current_entity}))
            continue
        rel = _ER_REL_RE.match(line)
        if rel:
            diagram.rows.append(StructuredRow("relation", rel.groupdict()))
            continue
        raise StructuredParseError(f"Line {idx}: unsupported ER statement.")
    if current_entity:
        raise StructuredParseError("ER entity block is missing a closing brace.")
    return diagram


def _render_sequence(diagram: StructuredDiagram) -> str:
    lines = [diagram.header]
    for row in diagram.rows:
        if row.role == "participant":
            lines.append(f"    participant {row.cells.get('name', '').strip()}")
        elif row.role == "message":
            lines.append(
                "    "
                f"{row.cells.get('source', '').strip()}"
                f"{row.cells.get('arrow', '->>').strip()}"
                f"{row.cells.get('target', '').strip()}: "
                f"{row.cells.get('text', '').strip()}"
            )
    return "\n".join(lines)


def _render_class(diagram: StructuredDiagram) -> str:
    lines = [diagram.header]
    class_names = [
        row.cells.get("class", "").strip()
        for row in diagram.rows
        if row.role == "class"
    ]
    for class_name in class_names:
        if not class_name:
            continue
        members = [
            row.cells.get("member", "").strip()
            for row in diagram.rows
            if row.role == "member" and row.cells.get("class", "").strip() == class_name
        ]
        lines.append(f"    class {class_name} {{")
        for member in members:
            if member:
                lines.append(f"        {member}")
        lines.append("    }")
    for row in diagram.rows:
        if row.role != "relation":
            continue
        left = _quoted(row.cells.get("left", ""))
        right = _quoted(row.cells.get("right", ""))
        source = row.cells.get("source", "").strip()
        arrow = row.cells.get("arrow", "-->").strip()
        target = row.cells.get("target", "").strip()
        pieces = [source]
        if left:
            pieces.append(left)
        pieces.append(arrow)
        if right:
            pieces.append(right)
        pieces.append(target)
        lines.append("    " + " ".join(pieces))
    return "\n".join(lines)


def _render_state(diagram: StructuredDiagram) -> str:
    lines = [diagram.header]
    for row in diagram.rows:
        if row.role != "transition":
            continue
        source = row.cells.get("source", "").strip()
        target = row.cells.get("target", "").strip()
        label = row.cells.get("label", "").strip()
        suffix = f": {label}" if label else ""
        lines.append(f"    {source} --> {target}{suffix}")
    return "\n".join(lines)


def _render_er(diagram: StructuredDiagram) -> str:
    lines = [diagram.header]
    for row in diagram.rows:
        if row.role != "relation":
            continue
        lines.append(
            "    "
            f"{row.cells.get('source', '').strip()} "
            f"{row.cells.get('connector', '||--o{').strip()} "
            f"{row.cells.get('target', '').strip()} : "
            f"{row.cells.get('label', '').strip()}"
        )
    entity_names = [
        row.cells.get("entity", "").strip()
        for row in diagram.rows
        if row.role == "entity"
    ]
    for entity in entity_names:
        if not entity:
            continue
        lines.append(f"    {entity} {{")
        for row in diagram.rows:
            if row.role != "field" or row.cells.get("entity", "").strip() != entity:
                continue
            lines.append(
                "        "
                f"{row.cells.get('field_type', '').strip()} "
                f"{row.cells.get('field_name', '').strip()}"
            )
        lines.append("    }")
    return "\n".join(lines)


def _quoted(value: str) -> str:
    text = value.strip()
    return f'"{text}"' if text else ""


def _clean_groups(groups: dict[str, str | None]) -> dict[str, str]:
    return {key: value or "" for key, value in groups.items()}
