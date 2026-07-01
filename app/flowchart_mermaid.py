"""Parse and render the safe Mermaid flowchart subset used by Visual mode."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from .flowchart_model import (
    FlowchartGraph,
    FlowNode,
    auto_layout_graph,
    default_flowchart,
)


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParseResult:
    graph: FlowchartGraph | None = None
    reason: str = ""

    @property
    def supported(self) -> bool:
        return self.graph is not None

    def require_graph(self) -> FlowchartGraph:
        if self.graph is None:
            raise ParseError(self.reason or "Unsupported Mermaid flowchart source.")
        return self.graph


_HEADER_RE = re.compile(r"^(?:flowchart|graph)\s+(TD|LR)\s*$", re.IGNORECASE)
_ID_RE = r"[A-Za-z][A-Za-z0-9_]*"
_BARE_RE = re.compile(rf"^(?P<id>{_ID_RE})$")
_PROCESS_RE = re.compile(rf"^(?P<id>{_ID_RE})\[(?P<label>[^\]\n]+)\]$")
_DECISION_RE = re.compile(rf"^(?P<id>{_ID_RE})\{{(?P<label>[^}}\n]+)\}}$")
_TERMINAL_RE = re.compile(rf"^(?P<id>{_ID_RE})\(\[(?P<label>[^\]\n]+)\]\)$")
_LABELED_EDGE_RE = re.compile(r"^(?P<left>.+?)\s+--\s+(?P<label>.+?)\s+-->\s+(?P<right>.+?)$")
_EDGE_RE = re.compile(r"^(?P<left>.+?)\s*-->\s*(?P<right>.+?)$")
_UNSUPPORTED_PREFIXES = (
    "subgraph",
    "classdef",
    "style",
    "click",
    "linkstyle",
    "acctitle",
    "accdescr",
    "end",
)
_LAYOUT_PREFIX = "%% markdown-viewer-layout:"


@dataclass(frozen=True)
class _Endpoint:
    id: str
    label: str | None = None
    shape: str = "process"
    defined: bool = False


def parse_flowchart(source: str) -> ParseResult:
    text = source.strip()
    if not text:
        return ParseResult(default_flowchart())
    try:
        graph = _parse(text)
        _apply_layout_metadata(graph, _extract_layout_metadata(source))
        return ParseResult(graph)
    except ParseError as exc:
        return ParseResult(None, str(exc))


def render_flowchart(graph: FlowchartGraph, *, include_layout: bool = True) -> str:
    direction = "TD" if graph.direction == "TD" else "LR"
    lines = [f"flowchart {direction}"]
    if include_layout and graph.nodes:
        lines.append(f"    {_render_layout_metadata(graph)}")
    emitted: set[str] = set()

    for edge in graph.edges:
        left = _render_endpoint(graph.node(edge.source), edge.source not in emitted)
        right = _render_endpoint(graph.node(edge.target), edge.target not in emitted)
        emitted.add(edge.source)
        emitted.add(edge.target)
        if edge.label:
            lines.append(f"    {left} -- {edge.label} --> {right}")
        else:
            lines.append(f"    {left} --> {right}")

    for node in graph.nodes:
        if node.id not in emitted:
            lines.append(f"    {_render_endpoint(node, True)}")
            emitted.add(node.id)

    return "\n".join(lines)


def visual_copy_from_source(source: str) -> ParseResult:
    """Best-effort supported flowchart copy from a source-only Mermaid diagram."""
    supported = parse_flowchart(source)
    if supported.supported:
        return supported

    direction = "LR"
    graph = FlowchartGraph(direction=direction)
    saw_header = False
    for idx, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue
        header = _HEADER_RE.match(line)
        if header:
            direction = header.group(1).upper()
            graph.direction = direction
            saw_header = True
            continue
        if not saw_header:
            continue
        for statement in [part.strip() for part in line.split(";") if part.strip()]:
            if _try_copy_statement(statement, graph, idx):
                continue

    if not graph.nodes:
        return ParseResult(None, "No supported flowchart nodes or edges were found.")
    auto_layout_graph(graph)
    return ParseResult(graph)


def _parse(text: str) -> FlowchartGraph:
    raw_lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in raw_lines if line and not line.startswith("%%")]
    if not lines:
        return default_flowchart()
    header = _HEADER_RE.match(lines[0])
    if not header:
        raise ParseError("Visual mode supports only Mermaid flowchart TD/LR.")
    graph = FlowchartGraph(direction=header.group(1).upper())

    for idx, line in enumerate(lines[1:], start=2):
        _reject_unsupported_line(line, idx)
        if ";" in line:
            raise ParseError(f"Line {idx}: multiple statements are not supported.")
        if _try_parse_edge(line, graph):
            continue
        endpoint = _parse_endpoint(line, idx)
        if not endpoint.defined:
            raise ParseError(f"Line {idx}: standalone node must include a label.")
        try:
            graph.ensure_node(endpoint.id, endpoint.label, endpoint.shape)
        except ValueError as exc:
            raise ParseError(f"Line {idx}: {exc}") from exc

    if not graph.nodes:
        return default_flowchart()
    return graph


def _try_copy_statement(statement: str, graph: FlowchartGraph, line_no: int) -> bool:
    lowered = statement.lower()
    if _has_unsupported_prefix(lowered):
        return False
    try:
        if _try_parse_edge(statement, graph):
            return True
        endpoint = _parse_endpoint(statement, line_no)
        if endpoint.defined:
            graph.ensure_node(endpoint.id, endpoint.label, endpoint.shape)
            return True
    except (ParseError, ValueError):
        return False
    return False


def _try_parse_edge(line: str, graph: FlowchartGraph) -> bool:
    label = ""
    match = _LABELED_EDGE_RE.match(line)
    if match:
        label = match.group("label").strip()
    else:
        match = _EDGE_RE.match(line)
    if not match:
        return False

    left = _parse_endpoint(match.group("left").strip())
    right = _parse_endpoint(match.group("right").strip())
    try:
        _ensure_endpoint(graph, left)
        _ensure_endpoint(graph, right)
        graph.add_edge(left.id, right.id, label)
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
    return True


def _ensure_endpoint(graph: FlowchartGraph, endpoint: _Endpoint) -> None:
    if endpoint.defined:
        graph.ensure_node(endpoint.id, endpoint.label, endpoint.shape)
    else:
        graph.ensure_node(endpoint.id)


def _parse_endpoint(raw: str, line_no: int | None = None) -> _Endpoint:
    text = raw.strip()
    for pattern, shape in (
        (_TERMINAL_RE, "terminal"),
        (_DECISION_RE, "decision"),
        (_PROCESS_RE, "process"),
    ):
        match = pattern.match(text)
        if not match:
            continue
        label = match.group("label").strip()
        if not label:
            break
        real_shape = _terminal_shape(label) if shape == "terminal" else shape
        return _Endpoint(
            id=match.group("id"),
            label=label,
            shape=real_shape,
            defined=True,
        )
    bare = _BARE_RE.match(text)
    if bare:
        return _Endpoint(id=bare.group("id"))
    where = f"Line {line_no}: " if line_no is not None else ""
    raise ParseError(f"{where}unsupported node or edge endpoint: {raw}")


def _terminal_shape(label: str) -> str:
    key = label.strip().lower()
    if key in ("done", "end", "finish", "finished"):
        return "end"
    return "start"


def _render_endpoint(node: FlowNode, include_label: bool) -> str:
    if not include_label:
        return node.id
    label = _escape_label(node.label)
    if node.shape in ("start", "end"):
        return f"{node.id}([{label}])"
    if node.shape == "decision":
        return f"{node.id}{{{label}}}"
    return f"{node.id}[{label}]"


def _escape_label(label: str) -> str:
    # Keep Visual mode conservative: labels that need escaping remain source-only.
    return label.replace("\n", " ").strip()


def _extract_layout_metadata(source: str) -> dict | None:
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line.startswith(_LAYOUT_PREFIX):
            continue
        payload = line[len(_LAYOUT_PREFIX) :].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def _apply_layout_metadata(graph: FlowchartGraph, metadata: dict | None) -> None:
    auto_layout_graph(graph)
    if not metadata:
        return
    nodes = metadata.get("nodes")
    if not isinstance(nodes, dict):
        return
    for node in graph.nodes:
        saved = nodes.get(node.id)
        if not isinstance(saved, dict):
            continue
        x = saved.get("x")
        y = saved.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            node.x = float(x)
            node.y = float(y)


def _render_layout_metadata(graph: FlowchartGraph) -> str:
    nodes = {
        node.id: {"x": _clean_coord(node.x), "y": _clean_coord(node.y)}
        for node in graph.nodes
    }
    payload = json.dumps(
        {"version": 1, "nodes": nodes},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{_LAYOUT_PREFIX} {payload}"


def _clean_coord(value: float) -> int | float:
    rounded = round(float(value))
    if abs(float(value) - rounded) < 0.001:
        return int(rounded)
    return round(float(value), 3)


def _reject_unsupported_line(line: str, line_no: int) -> None:
    lowered = line.lower().strip()
    if lowered.startswith("---") or lowered.startswith("```"):
        raise ParseError(f"Line {line_no}: fenced blocks are not part of the graph.")
    if _has_unsupported_prefix(lowered):
        raise ParseError(f"Line {line_no}: unsupported Mermaid flowchart feature.")
    if any(op in line for op in ("-.->", "==>", "~~~", "o--", "x--", "--o", "--x")):
        raise ParseError(f"Line {line_no}: unsupported edge operator.")
    if "<" in line or ">" in line.replace("-->", ""):
        raise ParseError(f"Line {line_no}: HTML labels are not supported.")


def _has_unsupported_prefix(lowered_line: str) -> bool:
    first = lowered_line.split(maxsplit=1)[0].rstrip(":")
    return first in _UNSUPPORTED_PREFIXES
