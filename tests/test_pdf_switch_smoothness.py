"""Document identity, bounded reuse, progressive layout and write safety."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QImage

from app import pdf_flow, pdf_metadata_cache
from app.pdf_notes import PdfNote, PdfNoteStore
from app.pdf_render_cache import PdfRenderCache, PdfRenderMeta
from app.pdf_view import PdfView

pymupdf = pytest.importorskip("pymupdf")


def make_pdf(path, pages=3):
    with pymupdf.open() as doc:
        for index in range(pages):
            page = doc.new_page(width=600 if index % 2 else 420, height=800 + index % 3 * 40)
            page.insert_text((40, 60), f"page {index}")
        doc.save(path)
    return path


@pytest.fixture
def view(qapp):
    view = PdfView()
    view.resize(900, 700)
    # Keep real document/layout/paint, only complete raster delivery manually.
    view._render_scheduler.begin_document = lambda *_: True
    view._render_scheduler.request = lambda *_: False
    yield view
    view._geometry_timer.stop()
    view._render_dispatch_timer.stop()
    view._outline_submit_timer.stop()
    view._embedded_annotations_submit_timer.stop()
    view._doc.close()
    view.close()
    view.deleteLater()
    qapp.processEvents()


def deliver_raster(view):
    view._render_dispatch_timer.stop()
    spec = next(s for s in view._visible_render_specs() if s.page == view._readable_page)
    view._wanted_render_keys = {spec.key}
    image = QImage(*spec.page_px, QImage.Format.Format_ARGB32)
    image.fill(0xff123456)
    view._on_rendered_image(spec, image)
    return spec, image


def test_return_to_document_reuses_target_raster_with_new_generation(view, tmp_path):
    first, other = make_pdf(tmp_path / "a.pdf"), make_pdf(tmp_path / "b.pdf")
    assert view.load(first, restore_page=1)
    spec, image = deliver_raster(view)
    assert view.load(other)
    assert view.load(first, restore_page=1)
    assert view.current_page() == 1
    assert view._pixmap_for(1) is not None
    assert view._pixmap_for(0) is None
    assert view._cache.items_for_page(view.load_generation(), 1)
    before = view._cache.bytes_used
    view._on_rendered_image(spec, image)
    assert view._cache.bytes_used == before  # old worker cannot re-enter cache
    assert view._cache.max_bytes + view._document_previews.max_bytes == view._CACHE_BUDGET_BYTES


def test_rapid_return_preserves_partial_geometry_without_reopening_known_pages(
    view, tmp_path, monkeypatch,
):
    first = make_pdf(tmp_path / "large.pdf", pages=200)
    other = make_pdf(tmp_path / "other.pdf")
    assert view.load(first, restore_page=100)
    pending = set(view._geometry_pending)
    key = view._geometry_signature
    assert pending and 100 not in pending
    assert view.load(other)
    assert pdf_metadata_cache.get_sizes(key, 200) is None
    cached = pdf_metadata_cache.get_partial_sizes(key, 200)
    assert cached[100] is not None and cached[50] is None
    original = view._doc.pagePointSize
    calls = []

    def measure(page):
        calls.append(page)
        return original(page)

    monkeypatch.setattr(view._doc, "pagePointSize", measure)
    assert view.load(first, restore_page=100)
    assert calls == []
    assert view._geometry_pending == pending
    view.restore_page(50)
    assert 50 in calls and 50 not in view._geometry_pending
    pdf_metadata_cache.invalidate(first)
    assert pdf_metadata_cache.get_partial_sizes(key, 200) is None


def test_readable_signal_requires_target_raster_paint(view, tmp_path):
    first, other = make_pdf(tmp_path / "a.pdf"), make_pdf(tmp_path / "b.pdf")
    painted = []
    view.first_raster_painted.connect(lambda *args: painted.append(args))
    assert view.load(first, restore_page=1)
    view.viewport().grab()
    assert painted == []
    deliver_raster(view)
    assert painted == []
    view.viewport().grab()
    assert painted == [(view.load_generation(), first, 1, False)]
    view.load(other)
    view.load(first, restore_page=1)
    view.viewport().grab()
    assert painted[-1] == (view.load_generation(), first, 1, True)


def test_reload_and_external_same_size_timestamp_write_reject_preview(view, tmp_path):
    first, other = make_pdf(tmp_path / "a.pdf"), make_pdf(tmp_path / "b.pdf")
    view.load(first)
    deliver_raster(view)
    view.load(first)
    assert view._pixmap_for(0) is None
    deliver_raster(view)
    view.load(other)
    original_signature = pdf_metadata_cache.signature(first)
    stat = first.stat()
    data = first.read_bytes()
    # Harmless same-size PDF header change, restoring mtime on purpose.
    first.write_bytes(data.replace(b"%PDF-1.7", b"%PDF-1.6", 1))
    os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert pdf_metadata_cache.signature(first) != original_signature
    assert view.load(first)
    assert view._pixmap_for(0) is None


def test_preview_identity_includes_render_options_dpr_page_and_version():
    cache = PdfRenderCache(10)
    sig = ("a", 1)
    meta = PdfRenderMeta(1, 2, "page", 100, (100, 200))
    cache.remember_preview(sig, "options", "pixels", 6, meta)
    assert cache.restore_previews(sig, "options", 2, 9, 100)[0][2].generation == 9
    assert not cache.restore_previews(("a", 2), "options", 2, 9, 100)
    assert not cache.restore_previews(sig, "other", 2, 9, 100)
    assert not cache.restore_previews(sig, "options", 2, 9, 200)
    assert not cache.restore_previews(sig, "options", 1, 9, 100)
    cache.remember_preview(("b", 1), "options", "second", 6, meta)
    assert cache.bytes_used == 6
    assert not cache.restore_previews(sig, "options", 2, 9, 100)
    cache.invalidate_document("b")
    assert cache.bytes_used == 0


def test_large_geometry_is_staged_and_preserves_mixed_page_anchor(view, tmp_path):
    path = make_pdf(tmp_path / "large.pdf", 260)
    assert view.load(path, restore_page=190)
    assert len(view._geometry_pending) >= 250
    assert 190 not in view._geometry_pending
    assert view.current_page() == 190
    scale = view._scale
    view.verticalScrollBar().setValue(view._page_tops[190] + 37)
    offset = view.verticalScrollBar().value() - view._page_tops[190]
    while view._geometry_pending:
        view._complete_geometry_batch()
    assert view.current_page() == 190
    assert view._scale == scale
    assert view.verticalScrollBar().value() - view._page_tops[190] == offset
    assert view._page_sizes[0].width() == 420
    assert view._page_sizes[1].width() == 600
    assert pdf_metadata_cache.get_sizes(view._geometry_signature, 260)
    other = make_pdf(tmp_path / "other.pdf")
    view.load(other)
    view.load(path, restore_page=191)
    assert view._scale == scale


def test_scoped_sidecar_read_is_shared_and_does_not_hide_later_edits(tmp_path, monkeypatch):
    path = tmp_path / "notes.pdf"
    PdfNoteStore.save(path, [PdfNote.new(0, "note")], ["tag"])
    original = Path.read_text
    reads = []

    def read(p, *args, **kwargs):
        reads.append(p)
        return original(p, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with PdfNoteStore.scoped_read(path):
        assert PdfNoteStore.load(path)[0].note == "note"
        assert PdfNoteStore.load_doc_tags(path) == ["tag"]
        assert PdfNoteStore.load_doc_tags(path) == ["tag"]
    assert len(reads) == 1
    PdfNoteStore.save_doc_tags(path, ["updated"])
    assert PdfNoteStore.load_doc_tags(path) == ["updated"]


def test_write_probe_coalesces_and_rejects_stale_file_and_document(qapp, tmp_path, monkeypatch):
    class Window(QObject):
        def __init__(self):
            super().__init__()
            self._current_kind = "pdf"
            self._current_file = tmp_path / "a.pdf"
            self._current_file.write_bytes(b"a")
            self.reasons = []
            self._pdf_view = self

        def set_annotation_write_blocked(self, reason):
            self.reasons.append(reason)

    tasks = []

    class Pool:
        def start(self, task):
            tasks.append(task)

    monkeypatch.setattr(pdf_flow.QThreadPool, "globalInstance", lambda: Pool())
    window = Window()
    controller = pdf_flow._WriteCheckController(window)
    controller.request(window._current_file)
    controller._start()
    for _ in range(8):
        controller.request(window._current_file)
    assert len(tasks) == 1
    controller._finished(tasks[0].path, tasks[0].signature, "old result")
    assert not window.reasons
    controller._start()
    assert len(tasks) == 2
    window._current_kind = "txt"
    controller._finished(tasks[1].path, tasks[1].signature, "wrong document")
    assert not window.reasons
    window._current_kind = "pdf"
    controller.request(window._current_file)
    controller._start()
    window._current_file.write_bytes(b"changed")
    controller._finished(tasks[2].path, tasks[2].signature, "wrong version")
    assert not window.reasons
    controller.timer.stop()
