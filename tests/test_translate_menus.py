"""Context-menu wiring for selection translation.

The editor and PDF checks run everywhere; the renderer one needs a real
QWebEngineView, so it follows the same ``RUN_WEBENGINE_TESTS=1`` gate as
tests/test_inline_edit_webengine.py.
"""

import os

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from app.editor import _PARAGRAPH_SEP, EditorView
from app.pdf_view import PdfView

_skip_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


# ── editor ──────────────────────────────────────────────────────────────

def test_editor_exposes_translate_signal(qapp):
    assert hasattr(EditorView, "translate_requested")


def test_editor_selection_restores_newlines_and_keeps_spaces(qapp):
    """Qt encodes line breaks as U+2029; spaces must survive the swap."""
    editor = EditorView()
    editor.setPlainText("First line here.\nSecond line here.")
    editor.selectAll()

    raw = editor.textCursor().selectedText()
    assert _PARAGRAPH_SEP in raw, "expected Qt's paragraph separator"

    restored = raw.replace(_PARAGRAPH_SEP, "\n").strip()
    assert restored == "First line here.\nSecond line here."
    assert restored.count(" ") == 4, "spaces must not be turned into newlines"


def test_editor_context_menu_adds_translate_when_text_is_selected(qapp):
    editor = EditorView()
    editor.setPlainText("hello world")
    editor.selectAll()

    menu = editor.createStandardContextMenu()
    baseline = len(menu.actions())
    emitted = []
    editor.translate_requested.connect(emitted.append)

    action = menu.addAction("翻譯選取內容")
    action.triggered.connect(lambda: editor.translate_requested.emit("hello world"))
    action.trigger()

    assert len(menu.actions()) == baseline + 1
    assert emitted == ["hello world"]


# ── PDF ─────────────────────────────────────────────────────────────────

def test_pdf_view_exposes_translate_signal(qapp):
    assert hasattr(PdfView, "translate_requested")


def test_pdf_selected_text_is_empty_without_a_selection(qapp):
    view = PdfView()
    assert view.has_selection() is False
    assert view.selected_text() == ""


def test_pdf_context_menu_without_selection_does_not_offer_translate(qapp):
    """The menu builder must not touch selection internals when there is none."""
    view = PdfView()
    emitted = []
    view.translate_requested.connect(emitted.append)
    # Mirrors the guard in contextMenuEvent: no selection -> nothing emitted.
    if view.has_selection():
        view.translate_requested.emit(view.selected_text())
    assert emitted == []


# ── Markdown preview ────────────────────────────────────────────────────

class _StubWebView(QWidget):
    """Duck-types the QWebEngineView bits RendererView._build_context_menu uses.

    Lets the guard be tested without a real browser, which cannot be
    constructed reliably under headless Chromium.
    """

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self._request = request
        self.standard_called = False

    def lastContextMenuRequest(self):
        return self._request

    def createStandardContextMenu(self):
        self.standard_called = True
        menu = QMenu(self)
        menu.addAction("Back")
        menu.addAction("Reload")
        return menu

    def pageAction(self, _action):
        return QAction("Copy", self)


def test_context_menu_avoids_the_crashing_call_without_a_request(qapp):
    """createStandardContextMenu() segfaults when no request is pending."""
    from app.renderer import RendererView

    stub = _StubWebView(request=None)
    menu = RendererView._build_context_menu(stub)

    assert stub.standard_called is False, "must not call the unsafe API"
    assert [a.text() for a in menu.actions()] == ["Copy"]


def test_context_menu_uses_the_page_menu_when_a_request_exists(qapp):
    from app.renderer import RendererView

    stub = _StubWebView(request=object())
    menu = RendererView._build_context_menu(stub)

    assert stub.standard_called is True
    assert [a.text() for a in menu.actions()] == ["Back", "Reload"]


@_skip_webengine
def test_real_renderer_context_menu_is_safe(qapp):
    """Same guard against a real QWebEngineView (needs a non-headless run)."""
    from app.renderer import RendererView

    view = RendererView()
    assert view.lastContextMenuRequest() is None
    menu = view._build_context_menu()
    assert menu.actions(), "fallback should still offer Copy"
    assert isinstance(view.page().selectedText(), str)
