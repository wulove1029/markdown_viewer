"""Pure data model for the visual Mermaid flowchart builder."""

from __future__ import annotations

from dataclasses import dataclass, field

Direction = str
NodeShape = str


@dataclass
class FlowNode:
    id: str
    label: str
    shape: NodeShape = "process"
    x: float = 0.0
    y: float = 0.0


@dataclass
class FlowEdge:
    id: str
    source: str
    target: str
    label: str = ""


@dataclass
class FlowchartGraph:
    direction: Direction = "LR"
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)

    def node(self, node_id: str) -> FlowNode:
        found = self.find_node(node_id)
        if found is None:
            raise KeyError(node_id)
        return found

    def find_node(self, node_id: str) -> FlowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def add_node(
        self,
        node_id: str | None = None,
        label: str = "Process",
        shape: NodeShape = "process",
        *,
        x: float | None = None,
        y: float | None = None,
    ) -> FlowNode:
        node_id = node_id or self.next_node_id()
        if self.find_node(node_id) is not None:
            raise ValueError(f"Duplicate node id: {node_id}")
        index = len(self.nodes)
        node = FlowNode(
            id=node_id,
            label=label,
            shape=shape,
            x=float(80 + (index % 4) * 180 if x is None else x),
            y=float(80 + (index // 4) * 130 if y is None else y),
        )
        self.nodes.append(node)
        return node

    def ensure_node(
        self, node_id: str, label: str | None = None, shape: NodeShape = "process"
    ) -> FlowNode:
        node = self.find_node(node_id)
        if node is None:
            return self.add_node(node_id, label or node_id, shape)
        if label is not None and (node.label != label or node.shape != shape):
            raise ValueError(f"Conflicting definition for node id: {node_id}")
        return node

    def add_edge(self, source: str, target: str, label: str = "") -> FlowEdge:
        self.ensure_node(source)
        self.ensure_node(target)
        edge = FlowEdge(
            id=self.next_edge_id(),
            source=source,
            target=target,
            label=label.strip(),
        )
        self.edges.append(edge)
        return edge

    def remove_node(self, node_id: str) -> None:
        self.nodes = [node for node in self.nodes if node.id != node_id]
        self.edges = [
            edge
            for edge in self.edges
            if edge.source != node_id and edge.target != node_id
        ]

    def remove_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if edge.id != edge_id]

    def next_node_id(self) -> str:
        used = {node.id for node in self.nodes}
        idx = 1
        while f"N{idx}" in used:
            idx += 1
        return f"N{idx}"

    def next_edge_id(self) -> str:
        used = {edge.id for edge in self.edges}
        idx = 1
        while f"E{idx}" in used:
            idx += 1
        return f"E{idx}"


def default_flowchart() -> FlowchartGraph:
    graph = FlowchartGraph(direction="LR")
    graph.add_node("Start", "Start", "start", x=60, y=120)
    graph.add_node("Process", "Process", "process", x=260, y=120)
    graph.add_node("Done", "Done", "end", x=480, y=120)
    graph.add_edge("Start", "Process")
    graph.add_edge("Process", "Done")
    return graph
