"""Bounded native-document reuse, file identity, and shutdown contracts."""

import pytest
from PySide6.QtPdf import QPdfDocument

from app.pdf_view import PdfView

pymupdf = pytest.importorskip("pymupdf")


def _make_pdf(path, text="document", pages=2):
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page()
            page.insert_text((50, 60), text)
        document.save(path)
    return path


@pytest.fixture
def view(qapp, monkeypatch):
    widget = PdfView()
    # These tests inspect real native documents without queueing raster work;
    # the real-renderer benchmark covers reuse after asynchronous painting.
    monkeypatch.setattr(widget._render_scheduler, "request", lambda _spec: False)
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_warm_return_reuses_both_gui_thread_native_documents(view, qapp, tmp_path):
    first = _make_pdf(tmp_path / "first.pdf", "first")
    second = _make_pdf(tmp_path / "second.pdf", "second")
    assert view.load(first)
    source = view._doc
    raster = view._render_scheduler._sessions_by_generation[view.load_generation()].document
    assert source.thread() == qapp.thread()
    assert raster.thread() == qapp.thread()
    assert view.load(second)
    assert view._doc is not source
    assert "second" in view._doc.getAllText(0).text()
    assert view.load(first)
    assert view._doc is source
    assert "first" in view._doc.getAllText(0).text()
    assert view._render_scheduler._sessions_by_generation[view.load_generation()].document is raster
    assert view._search.document() is source
    assert len(view._document_sessions) == 2
    assert len(view._render_scheduler._sessions_by_renderer) == 2


def test_reload_and_changed_file_reject_both_native_caches(view, tmp_path):
    first = _make_pdf(tmp_path / "first.pdf", "before")
    other = _make_pdf(tmp_path / "other.pdf")
    view.load(first)
    original = view._doc
    original_session = view._render_scheduler._sessions_by_generation[view.load_generation()]
    view.load(first, force_reload=True)
    assert view._doc is not original
    current_session = view._render_scheduler._sessions_by_generation[view.load_generation()]
    assert current_session.document is not original_session.document
    replaced = view._doc
    view.load(other)
    replacement = _make_pdf(tmp_path / "replacement.pdf", "after", pages=3)
    first.write_bytes(replacement.read_bytes())
    view.load(first)
    assert view._doc is not replaced
    assert view.page_count() == 3
    assert "after" in view._doc.getAllText(0).text()


def test_document_reuse_is_count_bounded_and_shutdown_releases_all(view, tmp_path):
    paths = [_make_pdf(tmp_path / f"file-{index}.pdf") for index in range(6)]
    for path in paths:
        assert view.load(path)
        assert len(view._document_sessions) <= 2
        assert len(view._render_scheduler._sessions_by_renderer) <= 2
    view.shutdown_document_sessions()
    assert not view._document_sessions
    assert not view._render_scheduler._idle_sessions
    assert not view._render_scheduler._sessions_by_renderer
    assert view._doc.status() == QPdfDocument.Status.Null
    assert view.load(paths[-1])
    assert view.page_count() == 2


def test_oversize_documents_are_readable_without_retention(view, tmp_path):
    first = _make_pdf(tmp_path / "first.pdf")
    other = _make_pdf(tmp_path / "other.pdf")
    view._DOCUMENT_REUSE_BUDGET_BYTES = 1
    view._render_scheduler._reuse_budget_bytes = 1
    assert view.load(first)
    original = view._doc
    assert not view._document_sessions
    assert view.load(other)
    assert view.load(first)
    assert view._doc is not original
    assert not view._document_sessions
    assert not view._render_scheduler._idle_sessions
    assert len(view._render_scheduler._sessions_by_renderer) == 1
