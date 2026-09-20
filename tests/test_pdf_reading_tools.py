"""Exercise reading tools through real Qt events and a generated PDF."""

import hashlib

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QThreadPool, QTimer
from PySide6.QtGui import QContextMenuEvent, QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QMenu

from app.pdf_embedded_annotations import EmbeddedAnnotation
from app.pdf_view import PdfView

pymupdf = pytest.importorskip("pymupdf")


@pytest.fixture
def reading_view(qapp, tmp_path):
    path = tmp_path / "reading.pdf"
    with pymupdf.open() as doc:
        for _ in range(2):
            page = doc.new_page(width=900, height=600)
            page.insert_text((180, 180), "Select these words", fontsize=20)
            page.draw_rect((150, 140, 650, 400), color=(1, 0, 0), width=3)
        doc.set_metadata({"title": "Circuit <test>", "author": "Reader"})
        doc.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    view = PdfView()
    view.resize(720, 460)
    view.show()
    assert view.load(path)
    qapp.processEvents()
    view.set_zoom_factor(2.0)
    qapp.processEvents()
    view.horizontalScrollBar().setValue(200)
    view.verticalScrollBar().setValue(200)
    view.setFocus()
    qapp.processEvents()
    yield view
    view.close()
    QThreadPool.globalInstance().waitForDone(10000)
    qapp.processEvents()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def mouse(view, kind, pos, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
    pos = QPointF(pos)
    event = QMouseEvent(kind, pos, QPointF(view.viewport().mapToGlobal(pos.toPoint())),
                        button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)
    return event


def key(view, kind, code, *, repeat=False):
    event = QKeyEvent(kind, code, Qt.KeyboardModifier.NoModifier, "", repeat)
    QApplication.sendEvent(view, event)
    return event


def drag(view, start, end, button=Qt.MouseButton.LeftButton):
    mouse(view, QEvent.Type.MouseButtonPress, start, button, button)
    mouse(view, QEvent.Type.MouseMove, end, buttons=button)
    mouse(view, QEvent.Type.MouseButtonRelease, end, button)


def action(menu, prefix):
    return next(a for a in menu.actions() if a.text().startswith(prefix))


def screen_rect(view, page, *rect):
    return view._page_rect_to_screen(page, *rect, view.horizontalScrollBar().value(),
                                     view.verticalScrollBar().value())


@pytest.mark.parametrize("tool", ["hand", "space", "middle"])
def test_pan_moves_both_axes_without_selecting_or_highlighting(reading_view, tool):
    view = reading_view
    highlights = []
    view.highlight_requested.connect(highlights.append)
    view.set_pen_mode(True)
    if tool == "hand":
        view.set_hand_mode(True)
    elif tool == "space":
        key(view, QEvent.Type.KeyPress, Qt.Key.Key_Space)
    button = Qt.MouseButton.MiddleButton if tool == "middle" else Qt.MouseButton.LeftButton
    mouse(view, QEvent.Type.MouseButtonPress, QPoint(240, 200), button, button)
    assert view.viewport().cursor().shape() == Qt.CursorShape.ClosedHandCursor
    mouse(view, QEvent.Type.MouseMove, QPoint(180, 160), buttons=button)
    assert view.horizontalScrollBar().value() == 260
    assert view.verticalScrollBar().value() == 240
    mouse(view, QEvent.Type.MouseButtonRelease, QPoint(180, 160), button)
    if tool == "space":
        key(view, QEvent.Type.KeyRelease, Qt.Key.Key_Space)
    assert not view.has_selection()
    assert highlights == []
    expected = Qt.CursorShape.OpenHandCursor if tool == "hand" else Qt.CursorShape.CrossCursor
    assert view.viewport().cursor().shape() == expected


def test_pan_clamps_at_edges_and_recovers(reading_view):
    view = reading_view
    view.set_hand_mode(True)
    drag(view, QPoint(250, 250), QPoint(1500, 1500))
    assert view.horizontalScrollBar().value() == 0
    assert view.verticalScrollBar().value() == 0
    drag(view, QPoint(300, 200), QPoint(200, 150))
    assert view.horizontalScrollBar().value() == 100
    assert view.verticalScrollBar().value() == 50


@pytest.mark.parametrize("cancel", ["focus", "hide", "reload", "escape", "space_release"])
def test_temporary_pan_cannot_stick_after_interruption(reading_view, cancel):
    view = reading_view
    key(view, QEvent.Type.KeyPress, Qt.Key.Key_Space)
    mouse(view, QEvent.Type.MouseButtonPress, QPoint(250, 220), Qt.MouseButton.LeftButton,
          Qt.MouseButton.LeftButton)
    if cancel == "focus":
        QApplication.sendEvent(view, QFocusEvent(QEvent.Type.FocusOut))
    elif cancel == "hide":
        view.hide()
    elif cancel == "reload":
        assert view.load(view._path)
    elif cancel == "space_release":
        key(view, QEvent.Type.KeyRelease, Qt.Key.Key_Space)
    else:
        key(view, QEvent.Type.KeyPress, Qt.Key.Key_Escape)
    before = (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
    mouse(view, QEvent.Type.MouseMove, QPoint(150, 120), buttons=Qt.MouseButton.LeftButton)
    assert (view.horizontalScrollBar().value(), view.verticalScrollBar().value()) == before
    assert view.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor


def test_space_autorepeat_does_not_end_gesture(reading_view):
    view = reading_view
    key(view, QEvent.Type.KeyPress, Qt.Key.Key_Space)
    key(view, QEvent.Type.KeyRelease, Qt.Key.Key_Space, repeat=True)
    drag(view, QPoint(240, 200), QPoint(180, 160))
    assert view.horizontalScrollBar().value() == 260
    key(view, QEvent.Type.KeyRelease, Qt.Key.Key_Space)
    assert view.viewport().cursor().shape() == Qt.CursorShape.IBeamCursor


def test_tool_switch_restores_selection_and_synchronizes_pen(reading_view):
    view = reading_view
    changes = []
    view.pen_mode_changed.connect(changes.append)
    view.set_pen_mode(True)
    view.set_hand_mode(True)
    assert not view.pen_mode()
    menu = view._build_context_menu(QPoint(240, 200))
    assert action(menu, "手形工具").isChecked()
    action(menu, "文字選取工具").trigger()
    assert not view.hand_mode()
    rect = screen_rect(view, 0, 180, 172, 165, 1)
    drag(view, rect.topLeft().toPoint(), rect.topRight().toPoint())
    assert "Select" in view.selected_text()
    assert changes == [True, False]
    view.set_pen_mode(True)
    assert not view.hand_mode()
    assert view.pen_mode()


def test_hand_click_opens_annotation_but_drag_does_not(reading_view):
    view = reading_view
    note = EmbeddedAnnotation(page=0, kind="Text", rect=(200, 220, 24, 24),
                              content="Review this", xref=42)
    view.set_embedded_annotations([note])
    point = screen_rect(view, 0, *note.rect).topLeft().toPoint() + QPoint(10, 10)
    seen = []
    view.embedded_annotation_selected.connect(seen.append)
    view.set_hand_mode(True)
    drag(view, point, point - QPoint(60, 40))
    assert seen == []
    point = screen_rect(view, 0, *note.rect).topLeft().toPoint() + QPoint(10, 10)
    drag(view, point, point)
    assert seen == [note]
    assert view.annotation_card().isVisible()


def test_blank_area_context_menu_has_working_tools_and_zoom(reading_view):
    view = reading_view
    zooms, finds, notes = [], [], []
    view.zoom_changed.connect(zooms.append)
    view.find_requested.connect(lambda: finds.append(True))
    view.page_note_requested.connect(notes.append)
    pos = QPoint(280, 220)
    page_before, point_before = view._pos_to_page(pos)
    menu = view._build_context_menu(pos)
    assert not action(menu, "複製選取文字").isEnabled()
    action(menu, "放大").trigger()
    page_after, point_after = view._pos_to_page(pos)
    assert zooms == [2.5]
    assert page_before == page_after
    assert point_after.x() == pytest.approx(point_before.x(), abs=1)
    assert point_after.y() == pytest.approx(point_before.y(), abs=1)
    action(menu, "尋找文字").trigger()
    action(menu, "新增頁面註記").trigger()
    assert finds == [True]
    assert notes == [page_before]
    action(menu, "符合頁面寬度").trigger()
    assert view.zoom_factor() == 1.0


def test_note_targets_clicked_page_instead_of_top_visible_page(reading_view):
    view = reading_view
    view.set_zoom_factor(0.5)
    view.jump_to_page(0)
    QApplication.processEvents()
    assert view.current_page() == 0
    pos = screen_rect(view, 1, 50, 50, 10, 10).center().toPoint()
    assert view.viewport().rect().contains(pos)
    captured = []
    view.page_note_requested.connect(captured.append)
    menu = view._build_context_menu(pos)
    action(menu, "新增頁面註記").trigger()
    assert captured == [1]


def test_snapshot_copies_image_and_restores_hand_tool(reading_view):
    view = reading_view
    view.set_hand_mode(True)
    menu = view._build_context_menu(QPoint(250, 200))
    action(menu, "拍攝快照").trigger()
    assert view.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
    mouse(view, QEvent.Type.MouseButtonPress, QPoint(100, 100), Qt.MouseButton.LeftButton,
          Qt.MouseButton.LeftButton)
    mouse(view, QEvent.Type.MouseMove, QPoint(300, 240), buttons=Qt.MouseButton.LeftButton)
    assert view._snapshot_band.isVisible()
    mouse(view, QEvent.Type.MouseButtonRelease, QPoint(300, 240), Qt.MouseButton.LeftButton)
    pixmap = QApplication.clipboard().pixmap()
    assert not pixmap.isNull()
    assert pixmap.width() / pixmap.devicePixelRatio() == pytest.approx(201, abs=1)
    assert pixmap.height() / pixmap.devicePixelRatio() == pytest.approx(141, abs=1)
    assert not view._snapshot_band.isVisible()
    assert view.viewport().cursor().shape() == Qt.CursorShape.OpenHandCursor
    assert view.horizontalScrollBar().value() == 200


def test_snapshot_escape_and_tiny_click_leave_clipboard_untouched(reading_view):
    view = reading_view
    QApplication.clipboard().setText("keep")
    view.start_snapshot()
    key(view, QEvent.Type.KeyPress, Qt.Key.Key_Escape)
    assert not view._snapshot_mode
    view.start_snapshot()
    drag(view, QPoint(100, 100), QPoint(100, 100))
    assert QApplication.clipboard().text() == "keep"
    assert not view.copy_snapshot(QRect(-50, -50, 10, 10))


def test_document_info_reads_metadata_without_modifying_pdf(reading_view):
    info = reading_view.document_info()
    assert "reading.pdf" in info
    assert "頁數：2" in info
    assert "Circuit <test>" in info
    assert "作者：Reader" in info


def test_keyboard_context_menu_uses_viewport_center(reading_view, monkeypatch):
    view = reading_view
    points = []
    build = view._build_context_menu

    def timed_menu(pos):
        menu = build(pos)
        def close_menu():
            points.append(menu.pos())
            menu.close()
        QTimer.singleShot(0, close_menu)
        return menu

    monkeypatch.setattr(view, "_build_context_menu", timed_menu)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(-1, -1), QPoint(-1, -1))
    QApplication.sendEvent(view, event)
    assert points == [view.viewport().mapToGlobal(view.viewport().rect().center())]


def test_unloaded_document_disables_document_actions(qapp):
    view = PdfView()
    menu = view._build_context_menu(QPoint(10, 10))
    for prefix in ("拍攝快照", "新增頁面註記", "尋找文字", "放大", "文件資訊"):
        assert not action(menu, prefix).isEnabled()
    view.close()
