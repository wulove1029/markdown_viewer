"""Independent acceptance of reading, source exports, and native PDF reuse."""

import json
import os
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget

real_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="set RUN_WEBENGINE_TESTS=1 for real Chromium acceptance",
)


def _evaluate(view, script):
    result = {}
    loop = QEventLoop()

    def complete(value):
        result["value"] = value
        loop.quit()

    view.page().runJavaScript(script, complete)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert "value" in result, "Chromium evaluation timed out"
    return result["value"]


def _wait_until(qapp, predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate(), "Expected asynchronous state was not reached"


@real_webengine
def test_initial_markdown_with_distant_checkbox_keeps_page_top(qapp, tmp_path):
    from app.renderer import RendererView

    note = tmp_path / "distant-control.md"
    note.write_text(
        "# Beginning\n\n" + "\n\n".join(
            f"Paragraph {index}: Ordinary reading text." for index in range(160)
        ) + "\n\n- [ ] Distant task must not receive initial focus\n",
        encoding="utf-8",
    )
    view = RendererView()
    view.resize(820, 620)
    view.show()
    view.load_file(note)
    _wait_until(qapp, lambda: view._loaded_markdown_generation == view._render_generation)
    QTest.qWait(350)
    state = json.loads(_evaluate(view, """JSON.stringify({
        y: window.scrollY,
        taskY: document.querySelector('input[type=checkbox]').getBoundingClientRect().top,
        active: document.activeElement.tagName,
        viewport: window.innerHeight
    })"""))
    assert state["taskY"] > state["viewport"] * 4
    assert state["y"] == 0
    assert state["active"] != "INPUT"
    view.close()


@real_webengine
def test_long_image_keyboard_scroll_and_escape_return_to_same_reading_position(qapp, tmp_path):
    from app.renderer import RendererView

    image = QImage(700, 4200, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(tmp_path / "long.png"))
    note = tmp_path / "long-image.md"
    note.write_text(
        "# Reading\n\n" + "\n\n".join(f"Paragraph {i}." for i in range(25))
        + "\n\n![A long diagram](long.png)\n\nTrailing text.", encoding="utf-8",
    )
    view = RendererView()
    view.resize(820, 620)
    view.show()
    view.load_file(note)
    _wait_until(qapp, lambda: view._loaded_markdown_generation == view._render_generation)
    _wait_until(qapp, lambda: _evaluate(view,
        "!!document.querySelector('img[data-reading-media]') && document.images[0].complete"))
    _evaluate(view, """window.scrollTo(0,500);
        document.querySelector('img[data-reading-media]').focus({preventScroll:true});""")
    initial_y = _evaluate(view, "window.scrollY")
    assert initial_y > 100
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Return)
    _wait_until(qapp, lambda: _evaluate(view, "!!document.querySelector('dialog[open]')"))
    assert _evaluate(view, "document.activeElement.textContent") == "關閉"
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Tab)
    _wait_until(qapp, lambda: _evaluate(view,
        "document.activeElement.classList.contains('reading-media-stage')"))
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_PageDown)
    _wait_until(qapp, lambda: _evaluate(view,
        "document.querySelector('.reading-media-stage').scrollTop > 100"))
    assert _evaluate(view, "window.scrollY") == initial_y
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Escape)
    _wait_until(qapp, lambda: _evaluate(view, "!document.querySelector('dialog')"))
    assert _evaluate(view, "document.activeElement.tagName") == "IMG"
    assert _evaluate(view, "window.scrollY") == initial_y
    assert _evaluate(view, "document.body.style.overflow") == ""
    view.close()


@real_webengine
@pytest.mark.parametrize("backend", ["source", "split"])
def test_exported_html_reopens_live_snapshot_and_relative_asset_elsewhere(
    qapp, tmp_path, monkeypatch, backend,
):
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from app import edit_backend, export_actions

    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "draft.md"
    saved = "# SAVED VERSION ONLY"
    source.write_text(saved, encoding="utf-8")
    image = QImage(240, 160, QImage.Format.Format_RGB32)
    image.fill(QColor("#557799"))
    assert image.save(str(source_dir / "relative.png"))
    output = output_dir / "snapshot.html"
    owner = QWidget()
    owner.current_file = source
    owner.edit_mode = True
    owner.exporting = False
    owner.pdf_progress = None
    owner._active_edit_backend = (
        edit_backend.SOURCE_BACKEND if backend == "source" else edit_backend.SPLIT_BACKEND
    )
    owner.wysiwyg_view = None
    owner._export_btn = QPushButton(owner)
    owner.renderer = SimpleNamespace(width=lambda: 850)
    owner._editor = SimpleNamespace(toPlainText=lambda:
        "# UNSAVED VERSION\n\n![Local asset](relative.png)\n\nEND OF SNAPSHOT")
    owner.statusBar = lambda: SimpleNamespace(showMessage=lambda *args: None)
    owner._refresh_icons = lambda: None
    completed = []
    errors = []
    monkeypatch.setattr(export_actions, "show_pdf_progress", lambda *args: None)
    monkeypatch.setattr(export_actions, "show_export_complete",
                        lambda *args: completed.append(args))
    monkeypatch.setattr(export_actions.QMessageBox, "warning",
                        lambda *args: errors.append(args))
    monkeypatch.setattr(export_actions.QFileDialog, "getSaveFileName",
                        lambda *args: (str(output), ""))
    export_actions.export_html(owner)
    assert owner.exporting and not owner._export_btn.isEnabled()
    owner._editor.toPlainText = lambda: "# LATER EDIT MUST NOT LEAK"
    _wait_until(qapp, lambda: bool(completed or errors))
    assert errors == []
    assert completed == [(owner, str(output))]
    assert owner._source_export_job is None and not owner.exporting
    assert owner._export_btn.isEnabled()
    reopened = QWebEngineView()
    loaded = []
    reopened.loadFinished.connect(loaded.append)
    reopened.load(QUrl.fromLocalFile(str(output)))
    _wait_until(qapp, lambda: bool(loaded))
    assert loaded == [True]
    body = _evaluate(reopened, "document.body.innerText")
    assert "UNSAVED VERSION" in body and "END OF SNAPSHOT" in body
    assert "SAVED VERSION ONLY" not in body and "LATER EDIT" not in body
    assert _evaluate(reopened, "document.images[0].naturalWidth") == 240
    assert _evaluate(reopened, "document.images[0].currentSrc") == QUrl.fromLocalFile(
        str(source_dir / "relative.png")).toString()
    assert source.read_text("utf-8") == saved
    reopened.close()
    owner.close()


def _make_pdf_bytes(text, color):
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=200)
        page.draw_rect(page.rect, color=color, fill=color)
        page.insert_text((15, 30), text)
        return document.tobytes()


def test_pdf_explicit_same_stat_invalidation_refreshes_text_and_real_raster(qapp, tmp_path):
    from app.pdf_render_scheduler import PdfRenderSpec
    from app.pdf_view import PdfView

    first = tmp_path / "mutable.pdf"
    other = tmp_path / "other.pdf"
    before = _make_pdf_bytes("OLD", (1, 0, 0))
    after = _make_pdf_bytes("NEW", (0, 0, 1))
    common_length = max(len(before), len(after))
    before = before.ljust(common_length, b"\n")
    after = after.ljust(common_length, b"\n")
    assert len(before) == len(after)
    first.write_bytes(before)
    other.write_bytes(_make_pdf_bytes("OTHER", (0, 1, 0)))
    stat = first.stat()
    view = PdfView()
    scheduler = view._render_scheduler
    images = {}
    scheduler.rendered.connect(lambda spec, image: images.__setitem__(spec.key, image))

    def sample(key):
        generation = view.load_generation()
        _wait_until(qapp, lambda: scheduler.pending_count(generation) < scheduler.max_inflight)
        assert scheduler.request(PdfRenderSpec(key, generation, 0, 0, "preview", 100, (200, 200)))
        _wait_until(qapp, lambda: key in images)
        return images[key].pixelColor(100, 100).name()

    try:
        assert view.load(first)
        original_gui = view._doc
        original_raster = scheduler._sessions_by_generation[view.load_generation()].document
        assert sample("old") == "#ff0000"
        # A native session with outstanding automatic viewport requests may
        # legitimately retire instead of becoming reusable. Exercise idle reuse.
        _wait_until(qapp, lambda: all(not session.requests
                                    for session in scheduler._sessions_by_renderer.values()))
        assert view.load(other)
        assert view.load(first)
        assert view._doc is original_gui
        assert scheduler._sessions_by_generation[view.load_generation()].document is original_raster
        assert sample("warm") == "#ff0000"
        assert view.load(other)
        first.write_bytes(after)
        os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert first.stat().st_size == stat.st_size
        assert first.stat().st_mtime_ns == stat.st_mtime_ns
        view.invalidate_cached_document(first)
        assert view.load(first)
        assert view._doc is not original_gui
        refreshed_raster = scheduler._sessions_by_generation[view.load_generation()].document
        assert refreshed_raster is not original_raster
        assert "NEW" in view._doc.getAllText(0).text()
        assert sample("new") == "#0000ff"
        _wait_until(qapp, lambda: all(not session.requests
                                    for session in scheduler._sessions_by_renderer.values()))
        assert len(view._document_sessions) <= 2
        assert len(scheduler._sessions_by_renderer) <= 2
        view.shutdown_document_sessions()
        assert not view._document_sessions and not scheduler._sessions_by_renderer
    finally:
        view.close()
        view.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("mode", ["edit", "split"])
def test_source_and_split_expose_working_toolbar_and_compact_export(
    qapp, tmp_path, monkeypatch, mode,
):
    from PySide6.QtCore import QSettings

    from app import export_actions, session_state
    from app import window as window_mod
    from app.recovery import RecoveryStore
    from tests import test_window_integration as helpers

    settings_path = tmp_path / "window.ini"
    def settings(*args):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)
    monkeypatch.setattr(window_mod, "QSettings", settings)
    monkeypatch.setattr(session_state, "QSettings", settings)
    monkeypatch.setattr(window_mod, "RecoveryStore", lambda: RecoveryStore(tmp_path / "recovery"))
    monkeypatch.setattr(window_mod, "RendererView", helpers._FakeRenderer)
    monkeypatch.setattr(window_mod, "PdfView", helpers._FakePdfView)
    monkeypatch.setattr(window_mod, "LeftPanel", helpers._FakePanel)
    monkeypatch.setattr(window_mod, "TagIndex", helpers._FakeTagIndex)
    monkeypatch.setattr(window_mod.MainWindow, "_refresh_tags_panel", lambda self: None)
    monkeypatch.setattr(
        window_mod.MainWindow, "_refresh_link_index", lambda self, force=False: None,
    )
    monkeypatch.setattr(window_mod.QTimer, "singleShot", staticmethod(lambda *args: None))
    monkeypatch.setattr(window_mod.QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(window_mod.QMessageBox, "question",
                        lambda *args: window_mod.QMessageBox.StandardButton.Discard)
    exported = []
    monkeypatch.setattr(export_actions, "export_pdf", lambda owner: exported.append(
        (owner._view_mode, owner._editor.toPlainText())))
    note = tmp_path / "note.md"
    note.write_text("# Saved", encoding="utf-8")
    window = window_mod.MainWindow()
    try:
        window._open_file(str(note))
        window._select_reading_mode(window._reading_mode_combo.findData(mode))
        window._editor.insertPlainText("UNSAVED ")
        assert window._view_mode == mode and window.edit_mode
        controls = window._workspace_controls
        controls.set_compact(False)
        assert not window._export_btn.isHidden()
        assert window._export_btn.isEnabled()
        window._export_btn.click()
        controls.set_compact(True)
        action = next(action for action in controls.more_menu.actions()
                      if action.text() == "匯出 PDF…")
        assert action.isEnabled()
        action.trigger()
        assert exported == [(mode, "UNSAVED # Saved"), (mode, "UNSAVED # Saved")]
        assert note.read_text("utf-8") == "# Saved"
    finally:
        window._editor.document().setModified(False)
        helpers._dispose_window(window)
