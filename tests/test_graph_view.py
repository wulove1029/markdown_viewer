from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.document_libraries import DocumentLibrary
from app.graph_view import GraphWindow
from app.links import LinkIndex
from app.theme import DARK


def test_graph_window_opens_real_nodes_but_not_ghosts(qapp, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("[[Missing]]", encoding="utf-8")
    index = LinkIndex()
    index.build([(note, "[[Missing]]")])
    opened = []
    window = GraphWindow(opened.append)
    window.set_index(index, str(note))
    window.show()
    qapp.processEvents()
    window.canvas._timer.stop()

    real = next(node for node in window.graph.nodes if not node.ghost)
    ghost = next(node for node in window.graph.nodes if node.ghost)
    for node in (ghost, real):
        item = window.canvas._node_items[node.id]
        viewport_position = window.canvas.view.mapFromScene(item.scenePos())
        QTest.mouseClick(
            window.canvas.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=viewport_position,
        )

    assert opened == [str(note)]
    assert window.canvas._node_items[real.id]._current is True
    assert window.canvas._node_items[ghost.id]._current is False
    window.close()


def test_graph_window_applies_dark_theme(qapp):
    window = GraphWindow(lambda _path: None)
    window.apply_theme(DARK)

    assert window.canvas._scene.backgroundBrush().color().name() == DARK.window
    window.close()


def test_graph_legend_toggles_group_nodes_and_edges(qapp):
    work = Path("C:/vault/work")
    personal = Path("C:/vault/personal")
    alpha = work / "alpha.md"
    beta = personal / "beta.md"
    index = LinkIndex()
    index.build([(alpha, "[[beta]]"), (beta, "")])
    libraries = [
        DocumentLibrary("work", "工作", str(work)),
        DocumentLibrary("personal", "個人", str(personal)),
    ]
    window = GraphWindow(lambda _path: None)
    window.set_index(index, libraries=libraries)
    window.show()
    qapp.processEvents()
    window.canvas._timer.stop()

    assert set(window._legend_buttons) == {"工作", "個人"}
    window._legend_buttons["工作"].setChecked(False)
    qapp.processEvents()

    alpha_item = next(
        item for item in window.canvas._node_items.values() if item.node.label == "alpha"
    )
    beta_item = next(
        item for item in window.canvas._node_items.values() if item.node.label == "beta"
    )
    assert alpha_item.isVisible() is False
    assert beta_item.isVisible() is True
    assert all(edge.isVisible() is False for edge in window.canvas._edge_items)
    window.close()


def test_graph_hover_highlights_neighbors_and_zero_edge_hint(qapp):
    alpha = Path("C:/vault/alpha.md")
    beta = Path("C:/vault/beta.md")
    alone = Path("C:/vault/alone.md")
    docs = [(alpha, "[[beta]]"), (beta, ""), (alone, "")]
    index = LinkIndex()
    index.build(docs)
    window = GraphWindow(lambda _path: None)
    window.set_index(index)
    window.show()
    qapp.processEvents()
    window.canvas._timer.stop()

    items = {item.node.label: item for item in window.canvas._node_items.values()}
    window.canvas.set_hovered_node(items["alpha"].node.id)
    assert items["alpha"].opacity() == 1.0
    assert items["beta"].opacity() == 1.0
    assert items["alone"].opacity() == 0.2
    assert window.canvas._edge_items[0].pen().color().name() == window._theme.accent
    assert window.canvas._edge_items[0].pen().widthF() == 2.2

    window.canvas.set_hovered_node(None)
    assert all(item.opacity() == 1.0 for item in items.values())
    no_edges = LinkIndex()
    no_edges.build([(alone, "")])
    window.set_index(no_edges)
    assert window._hint.text() == (
        "筆記之間尚無 [[連結]]——在筆記內文輸入 [[筆記名]] 建立連結後，"
        "關聯圖就會出現線條"
    )
    window.canvas.fit_graph()
    assert window.canvas.view.transform().m11() <= 1.5
    window.close()
