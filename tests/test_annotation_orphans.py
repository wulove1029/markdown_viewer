"""User-visible orphan feedback must preserve notes and reject stale results."""

from types import SimpleNamespace

from PySide6.QtWidgets import QDockWidget

from app import renderer
from app.annotations import Annotation, DocumentAnnotations
from app.annotations_panel import AnnotationsPanel
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
    md_files,
)


def test_panel_marks_orphans_without_removing_notes(qapp):
    annotation = Annotation.new("original text", note="keep this note")
    document = DocumentAnnotations(annotations=[annotation])
    panel = AnnotationsPanel({})
    panel.set_document(document)
    panel.select(annotation.id)
    panel.set_orphans([annotation.id])
    assert "找不到原文" in panel._list.item(0).text()
    assert panel._note.toPlainText() == "keep this note"
    assert document.annotations == [annotation]
    panel.set_orphans([])
    assert "找不到原文" not in panel._list.item(0).text()
    panel.set_orphans([annotation.id])
    panel.set_document(DocumentAnnotations(annotations=[annotation]))
    assert "找不到原文" not in panel._list.item(0).text()
    panel.deleteLater()


def test_window_shows_orphan_feedback_and_ignores_late_reply(
    make_window, md_files, monkeypatch,  # noqa: F811
):
    window = make_window()
    window._open_file(str(md_files[0]))
    annotation = Annotation.new("original", note="retained")
    window._doc_annotations.annotations = [annotation]
    callbacks = []
    # Restore instance methods before fixture-owned Qt objects are deleted.
    with monkeypatch.context() as patch:
        patch.setattr(window._renderer, "annotation_orphans", callbacks.append, raising=False)
        window._on_bridge_orphans(["untrusted old notification"])
        callbacks.pop()([annotation.id, "unknown"])
        assert window._annotation_orphans == {annotation.id}
        assert window._doc_annotations.annotations[0].note == "retained"
        patch.setattr(window._renderer, "scroll_to_annotation",
                            lambda _identity, callback: callbacks.append(callback))
        window._annot_activated(annotation.id)
        callbacks.pop()(False)
        assert "找不到這則註記的原文" in window.statusBar().currentMessage()
        window._annot_activated(annotation.id)
        late = callbacks.pop()
        window._on_bridge_orphans([])
        late_orphans = callbacks.pop()
        window._open_file(str(md_files[1]))
        window.statusBar().clearMessage()
        late(False)
        late_orphans([annotation.id])
        assert not window.statusBar().currentMessage()
        assert window._annotation_orphans_path == md_files[0]


def test_renderer_rejects_stale_orphan_and_navigation_callbacks(monkeypatch):
    callbacks = []
    monkeypatch.setattr(renderer, "_run_page_javascript",
                        lambda _view, _script, callback=None: callbacks.append(callback))
    view = SimpleNamespace(_render_generation=3)
    received = []
    renderer.RendererView.annotation_orphans(view, received.append)
    callbacks.pop()('["a"]')
    assert received == [["a"]]
    renderer.RendererView.annotation_orphans(view, received.append)
    late = callbacks.pop()
    renderer.RendererView.scroll_to_annotation(view, "a", received.append)
    late_navigation = callbacks.pop()
    view._render_generation += 1
    late('["b"]')
    late_navigation(False)
    assert received == [["a"]]


def test_reading_tool_pin_persists_and_keeps_close_available(make_window):  # noqa: F811
    first = make_window()
    first._reading_tools.pin.click()
    second = make_window()
    assert second._reading_tools.pin.isChecked()
    for dock in (second._reading_tools.dock, second._properties_dock):
        assert dock.features() == QDockWidget.DockWidgetFeature.DockWidgetClosable
    second._reading_tools.pin.click()
    assert second._reading_tools.dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
