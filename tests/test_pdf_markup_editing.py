import json
from types import SimpleNamespace

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QContextMenuEvent

from app.pdf_annotation_card import PdfAnnotationCard
from app.pdf_embedded_annotations import EmbeddedAnnotation
from app.pdf_highlights import PdfHighlightStore
from app.pdf_highlights_panel import PdfHighlightsPanel
from app.window import MainWindow


def test_card_visible_menu_and_right_click_use_same_builder(qapp, monkeypatch):
    card = PdfAnnotationCard()
    card.set_author("Reader")
    entry = EmbeddedAnnotation(page=0, kind="Text", author="Reader", content="note")
    card.show_for(entry, [], QRect(0, 0, 10, 10), QRect(0, 0, 800, 600))
    assert card._menu_button.isVisible()
    calls = []
    original = card._build_menu

    def build():
        menu = original()
        calls.append([(action.text(), action.isEnabled()) for action in menu.actions()])
        monkeypatch.setattr(menu, "exec", lambda *args: None)
        return menu

    monkeypatch.setattr(card, "_build_menu", build)
    card._menu_button.click()
    card.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(), QPoint()))
    assert calls == [[("編輯…", True), ("刪除", True)]] * 2
    card.set_write_blocked("Read only")
    assert not any(action.isEnabled() for action in original().actions())
    card.close()


def test_inline_highlight_text_persists_old_sidecar_and_preserves_geometry(
    qapp, tmp_path,
):
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF")
    sidecar = PdfHighlightStore.sidecar_path(pdf)
    legacy = {"highlights": [{"id": "one", "page": 2, "text": "old",
                              "rects": [{"x": 1, "y": 2, "w": 3, "h": 4}],
                              "color": "#ffd54f", "note": "keep", "tags": ["tag"]}]}
    sidecar.write_text(json.dumps(legacy), encoding="utf-8")
    window = SimpleNamespace(_current_kind="pdf", _current_file=pdf,
                             _pdf_highlights=PdfHighlightStore.load(pdf),
                             _pdf_view=SimpleNamespace(set_highlights=lambda items: None))
    window._find_pdf_highlight = lambda hid: next(
        (h for h in window._pdf_highlights if h.id == hid), None)
    panel = PdfHighlightsPanel({"text": lambda hid, text:
                               MainWindow._pdf_highlight_edit_text(window, hid, text)})
    window._refresh_pdf_highlights_panel = lambda: panel.set_highlights(window._pdf_highlights)
    panel.set_highlights(window._pdf_highlights)
    panel._begin_text_edit(panel._list.item(0))
    panel._text_editor.setPlainText("新文字\n第二行")
    panel._save_text_edit()
    reopened = PdfHighlightStore.load(pdf)[0]
    assert reopened.text == "新文字\n第二行"
    assert reopened.page == 2 and reopened.rects[0].to_dict() == legacy["highlights"][0]["rects"][0]
    assert reopened.note == "keep" and reopened.tags == ["tag"]
    assert panel._editing_id is None
    panel.close()


def test_failed_highlight_save_keeps_editor_and_previous_model(qapp, tmp_path, monkeypatch):
    from app.pdf_highlights import PdfHighlight

    original = PdfHighlight.new(0, [], "before")
    messages = []
    window = SimpleNamespace(
        _current_kind="pdf", _current_file=tmp_path / "note.pdf",
        _pdf_highlights=[original], _find_pdf_highlight=lambda hid: original,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *a: messages.append(a)),
    )
    monkeypatch.setattr(PdfHighlightStore, "save",
                        lambda *a: (_ for _ in ()).throw(OSError("locked")))
    panel = PdfHighlightsPanel({"text": lambda hid, text:
                               MainWindow._pdf_highlight_edit_text(window, hid, text)})
    panel.set_highlights([original])
    panel._begin_text_edit(panel._list.item(0))
    panel._text_editor.setPlainText("draft")
    panel._save_text_edit()
    assert window._pdf_highlights[0].text == "before"
    assert panel._text_editor.toPlainText() == "draft"
    assert panel._editing_id == original.id
    assert messages
    panel.close()


def test_switching_pdf_cancels_draft_even_when_sidecar_ids_match(qapp, tmp_path):
    from app.pdf_highlights import PdfHighlight
    from app.pdf_highlights_panel import PdfMarkupPanel

    saves = []
    panel = PdfMarkupPanel({}, {"text": lambda *args: saves.append(args)})
    first, second = tmp_path / "A.pdf", tmp_path / "B.pdf"
    panel.set_pdf_document(first)
    panel.highlights.set_highlights([PdfHighlight(id="same-id", page=0, text="A")])
    panel.highlights._begin_text_edit(panel.highlights._list.item(0))
    panel.highlights._text_editor.setPlainText("A draft")
    panel.set_pdf_document(second)
    panel.highlights.set_highlights([PdfHighlight(id="same-id", page=0, text="B")])
    panel.highlights._save_text_edit()
    assert saves == []
    assert panel.highlights._editing_id is None
    panel.close()
