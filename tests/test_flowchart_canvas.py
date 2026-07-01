"""Tests for the visual flowchart canvas widget."""

from app.flowchart_canvas import FlowchartCanvas
from app.flowchart_model import FlowchartGraph


def _graph():
    graph = FlowchartGraph(direction="LR")
    graph.add_node("A", "Start", "start")
    graph.add_node("B", "Work", "process")
    graph.add_edge("A", "B", "go")
    return graph


def test_canvas_constructs_and_sets_graph(qapp):
    canvas = FlowchartCanvas()
    canvas.set_graph(_graph())
    graph = canvas.graph()
    assert graph.direction == "LR"
    assert [node.id for node in graph.nodes] == ["A", "B"]
    assert [(edge.source, edge.target, edge.label) for edge in graph.edges] == [
        ("A", "B", "go")
    ]


def test_canvas_add_node_emits_graph_changed(qapp):
    canvas = FlowchartCanvas()
    canvas.set_graph(_graph())
    calls = []
    canvas.graph_changed.connect(lambda _graph: calls.append(True))
    canvas.add_node("decision")
    assert len(canvas.graph().nodes) == 3
    assert canvas.graph().nodes[-1].shape == "decision"
    assert calls


def test_canvas_remove_selected_node_removes_edges(qapp):
    canvas = FlowchartCanvas()
    canvas.set_graph(_graph())
    canvas.select_node("A")
    canvas.delete_selected()
    assert [node.id for node in canvas.graph().nodes] == ["B"]
    assert canvas.graph().edges == []


def test_canvas_add_edge_and_set_direction(qapp):
    canvas = FlowchartCanvas()
    graph = FlowchartGraph(direction="TD")
    graph.add_node("A", "A", "process")
    graph.add_node("B", "B", "process")
    canvas.set_graph(graph)
    canvas.add_edge("A", "B", "next")
    canvas.set_direction("LR")
    assert canvas.graph().direction == "LR"
    assert [(edge.source, edge.target, edge.label) for edge in canvas.graph().edges] == [
        ("A", "B", "next")
    ]


def test_canvas_add_node_auto_connects_from_selected_node(qapp):
    canvas = FlowchartCanvas()
    canvas.set_graph(_graph())
    canvas.select_node("A")
    canvas.add_node("process")
    graph = canvas.graph()
    assert graph.nodes[-1].id == "N1"
    assert graph.edges[-1].source == "A"
    assert graph.edges[-1].target == "N1"
    assert canvas.selected_node_id() == "N1"


def test_canvas_auto_layout_updates_positions(qapp):
    canvas = FlowchartCanvas()
    graph = FlowchartGraph(direction="TD")
    graph.add_node("A", "A", "process", x=500, y=500)
    graph.add_node("B", "B", "process", x=500, y=500)
    graph.add_edge("A", "B")
    canvas.set_graph(graph)
    canvas.auto_layout()
    updated = canvas.graph()
    assert updated.node("A").y < updated.node("B").y


def test_canvas_property_methods_update_graph(qapp):
    canvas = FlowchartCanvas()
    canvas.set_graph(_graph())
    canvas.set_node_label("A", "Begin")
    canvas.set_node_shape("A", "decision")
    canvas.set_node_position("A", 42, 84)
    edge_id = canvas.graph().edges[0].id
    canvas.set_edge_label(edge_id, "ok")
    graph = canvas.graph()
    assert graph.node("A").label == "Begin"
    assert graph.node("A").shape == "decision"
    assert graph.node("A").x == 42
    assert graph.node("A").y == 84
    assert graph.edges[0].label == "ok"
