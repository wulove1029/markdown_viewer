"""Parse and render the safe Mermaid flowchart subset used by Visual mode."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .flowchart_model import FlowchartGraph, FlowNode, default_flowchart


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
    "accTitle",
    "accDescr",
    "end",
)


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
        return ParseResult(_parse(text))
    except ParseError as exc:
        return ParseResult(None, str(exc))


def render_flowchart(graph: FlowchartGraph) -> str:
    direction = "TD" if graph.direction == "TD" else "LR"
    lines = [f"flowchart {direction}"]
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


def _reject_unsupported_line(line: str, line_no: int) -> None:
    lowered = line.lower().strip()
    if lowered.startswith("---") or lowered.startswith("```"):
        raise ParseError(f"Line {line_no}: fenced blocks are not part of the graph.")
    if lowered.startswith(_UNSUPPORTED_PREFIXES):
        raise ParseError(f"Line {line_no}: unsupported Mermaid flowchart feature.")
    if any(op in line for op in ("-.->", "==>", "~~~", "o--", "x--", "--o", "--x")):
        raise ParseError(f"Line {line_no}: unsupported edge operator.")
    if "<" in line or ">" in line.replace("-->", ""):
        raise ParseError(f"Line {line_no}: HTML labels are not supported.")
