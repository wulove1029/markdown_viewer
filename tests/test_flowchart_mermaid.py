"""Tests for the visual flowchart Mermaid subset."""

import pytest

from app.flowchart_mermaid import ParseError, parse_flowchart, render_flowchart
from app.flowchart_model import FlowchartGraph


SAMPLE = """flowchart LR
    A([Start]) --> B[Collect input]
    B --> C{Valid?}
    C -- Yes --> D[Process request]
    C -- No --> E[Fix input]
    E --> B
    D --> F([Done])
"""


def test_parse_supported_flowchart():
    result = parse_flowchart(SAMPLE)
    assert result.supported
    graph = result.graph
    assert graph is not None
    assert graph.direction == "LR"
    assert [n.id for n in graph.nodes] == ["A", "B", "C", "D", "E", "F"]
    assert graph.node("A").shape == "start"
    assert graph.node("B").shape == "process"
    assert graph.node("C").shape == "decision"
    assert graph.node("F").shape == "end"
    assert [(e.source, e.label, e.target) for e in graph.edges] == [
        ("A", "", "B"),
        ("B", "", "C"),
        ("C", "Yes", "D"),
        ("C", "No", "E"),
        ("E", "", "B"),
        ("D", "", "F"),
    ]


def test_render_flowchart_is_stable():
    graph = parse_flowchart(SAMPLE).graph
    assert render_flowchart(graph) == SAMPLE.rstrip()


def test_round_trip_parse_render_parse():
    first = parse_flowchart(SAMPLE).graph
    second = parse_flowchart(render_flowchart(first)).graph
    assert second == first


def test_parse_td_direction_and_safe_punctuation():
    src = """flowchart TD
    Start([Power-on reset]) --> Check{Vout OK?}
    Check -- No: retry --> Start
"""
    graph = parse_flowchart(src).graph
    assert graph.direction == "TD"
    assert graph.node("Start").label == "Power-on reset"
    assert graph.node("Check").label == "Vout OK?"
    assert graph.edges[1].label == "No: retry"


@pytest.mark.parametrize(
    "source",
    [
        "sequenceDiagram\nA->>B: hi\n",
        "flowchart LR\nsubgraph One\nA-->B\nend\n",
        "flowchart LR\nclassDef hot fill:#f00\nA-->B\n",
        "flowchart LR\nstyle A fill:#f00\nA-->B\n",
        "flowchart LR\nA -.-> B\n",
        "flowchart LR\nA-->B; B-->C\n",
        "flowchart LR\nA[One]\nA[Two]\n",
    ],
)
def test_unsupported_source_is_rejected(source):
    result = parse_flowchart(source)
    assert not result.supported
    assert result.reason
    with pytest.raises(ParseError):
        result.require_graph()


def test_graph_helpers_remove_connected_edges():
    graph = FlowchartGraph(direction="LR")
    graph.add_node("N1", "Start", "start")
    graph.add_node("N2", "Work", "process")
    graph.add_node("N3", "Done", "end")
    graph.add_edge("N1", "N2")
    graph.add_edge("N2", "N3")
    graph.remove_node("N2")
    assert [n.id for n in graph.nodes] == ["N1", "N3"]
    assert graph.edges == []
