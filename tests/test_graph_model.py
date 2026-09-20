from pathlib import Path
from time import perf_counter

from app.document_libraries import DocumentLibrary
from app.graph_model import (
    GraphNode,
    assign_node_groups,
    build_graph,
    group_visibility,
    initial_positions,
    layout_step,
    separate_overlapping_nodes,
)
from app.links import LinkIndex


def _index(docs):
    index = LinkIndex()
    index.build([(Path(path), text) for path, text in docs])
    return index


def test_markdown_and_wiki_edges_keep_their_types():
    graph = build_graph(_index([
        ("/vault/a/source.md", "[a](../b/README.md) [[c]]"),
        ("/vault/b/README.md", ""),
        ("/vault/c.md", ""),
    ]))
    assert len(graph.edges) == 2
    assert {edge.kind for edge in graph.edges} == {"wiki", "markdown"}


def test_markdown_encoded_heading_and_angle_links_share_a_target():
    graph = build_graph(_index([
        ("/vault/source.md", "[a](./a%20b.md#one) [b](a%20b.md#two) <a%20b.md>"),
        ("/vault/a b.md", ""),
    ]))
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].target == str(Path("/vault/a b.md"))


def test_markdown_code_images_and_external_urls_do_not_create_edges():
    graph = build_graph(_index([
        ("/vault/a.md", "`[x](b.md)`\n```md\n[x](b.md)\n<b.md>\n```\n"
         "![image](b.md) [web](https://example.com/b.md) [mail](mailto:b.md) [pdf](b.pdf)"),
        ("/vault/b.md", ""),
    ]))
    assert not graph.edges


def test_markdown_missing_path_does_not_fall_back_to_same_basename():
    graph = build_graph(_index([
        ("/vault/a/source.md", "[wrong](./README.md) [right](../b/README.md)"),
        ("/vault/b/README.md", ""),
    ]))
    assert len(graph.edges) == 1
    assert not any(node.ghost for node in graph.nodes)


def test_build_graph_includes_edges_ghosts_and_isolated_notes():
    index = _index(
        [
            ("/vault/alpha.md", "[[beta]] [[Missing Note]] [[missing note#part]]"),
            ("/vault/beta.md", "[[alpha|back]]"),
            ("/vault/alone.md", "No links here"),
        ]
    )

    graph = build_graph(index)
    real = {node.label: node for node in graph.nodes if not node.ghost}
    ghosts = [node for node in graph.nodes if node.ghost]

    assert set(real) == {"alpha", "beta", "alone"}
    assert real["alone"].path == str(Path("/vault/alone.md"))
    assert [(node.label, node.path) for node in ghosts] == [("Missing Note", None)]
    edge_labels = {
        (next(node.label for node in graph.nodes if node.id == edge.source),
         next(node.label for node in graph.nodes if node.id == edge.target))
        for edge in graph.edges
    }
    assert edge_labels == {
        ("alpha", "beta"),
        ("alpha", "Missing Note"),
        ("beta", "alpha"),
    }


def test_layout_step_updates_positions_and_honors_pinned_nodes():
    graph = build_graph(
        _index([("/v/a.md", "[[b]]"), ("/v/b.md", ""), ("/v/c.md", "")])
    )
    positions = initial_positions(graph.nodes)
    pinned_id = graph.nodes[0].id

    updated, movement = layout_step(
        positions, graph.edges, temperature=10.0, pinned={pinned_id}
    )

    assert movement > 0
    assert updated[pinned_id] == positions[pinned_id]
    assert any(updated[node_id] != positions[node_id] for node_id in positions if node_id != pinned_id)


def test_build_graph_200_nodes_is_fast_enough_for_interactive_refresh():
    docs = []
    for index in range(200):
        next_index = (index + 1) % 200
        docs.append((f"/vault/note-{index}.md", f"[[note-{next_index}]] [[ghost-{index % 5}]]"))
    link_index = _index(docs)

    started = perf_counter()
    graph = build_graph(link_index)
    elapsed = perf_counter() - started

    assert len(graph.nodes) == 205
    assert len(graph.edges) == 400
    assert elapsed < 1.0


def test_assign_node_groups_uses_containing_library_and_excludes_ghosts():
    work = Path("C:/vault/Work")
    personal = Path("C:/vault/Personal")
    nodes = (
        GraphNode("work", "Plan", str(work / "plans" / "plan.md")),
        GraphNode("personal", "Journal", str(personal / "journal.md")),
        GraphNode("outside", "Loose", "C:/loose/loose.md"),
        GraphNode("ghost", "Missing", None, ghost=True),
    )
    libraries = [
        DocumentLibrary("1", "工作", str(work)),
        DocumentLibrary("2", "個人", str(personal)),
    ]

    assert assign_node_groups(nodes, libraries) == {
        "work": "工作",
        "personal": "個人",
        "outside": "其他",
        "ghost": None,
    }


def test_separate_overlapping_nodes_leaves_twelve_pixel_gap():
    positions = {"a": (0.0, 0.0), "b": (0.0, 0.0), "c": (3.0, 2.0)}
    sizes = {"a": (80.0, 34.0), "b": (100.0, 34.0), "c": (70.0, 34.0)}

    separated = separate_overlapping_nodes(positions, sizes, min_gap=12.0)

    ids = list(separated)
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            dx = abs(separated[left][0] - separated[right][0])
            dy = abs(separated[left][1] - separated[right][1])
            required_x = (sizes[left][0] + sizes[right][0]) / 2.0 + 12.0
            required_y = (sizes[left][1] + sizes[right][1]) / 2.0 + 12.0
            assert dx >= required_x or dy >= required_y


def test_group_visibility_hides_only_selected_real_groups():
    groups = {"a": "工作", "b": "工作", "c": "個人", "ghost": None}

    assert group_visibility(groups, {"工作"}) == {
        "a": False,
        "b": False,
        "c": True,
        "ghost": True,
    }
