"""Unsaved source exports own a complete snapshot and an isolated renderer."""

import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QPushButton, QWidget

from app import edit_backend, export_actions, renderer


class _Owner(QWidget):
    def __init__(self, source):
        super().__init__()
        self.current_file = source
        self.edit_mode = True
        self.exporting = False
        self.pdf_progress = None
        self._active_edit_backend = edit_backend.SOURCE_BACKEND
        self.wysiwyg_view = None
        self._export_btn = QPushButton(self)
        self._editor = SimpleNamespace(toPlainText=lambda: "# UNSAVED snapshot")
        self.renderer = SimpleNamespace(width=lambda: 900)
        self.statusBar = lambda: SimpleNamespace(showMessage=lambda *args: None)
        self._refresh_icons = lambda: None


class _SnapshotRenderer:
    def __init__(self, parent):
        self._render_generation = 0
        self._render_failed_generation = None
        self._render_cancel = threading.Event()
        self.exports = []
        self.cleaned = False
        self.ready = 0
        self.html = "<html><head></head><body>UNSAVED snapshot</body></html>"

    def hide(self):
        pass

    def resize(self, width, height):
        self.size = (width, height)

    def width(self):
        return self.size[0]

    def render_markdown_text(self, text, **kwargs):
        self.snapshot = text
        self.options = kwargs
        self._render_generation += 1

    def page(self):
        return self

    def runJavaScript(self, script, callback):
        self.probe = script
        callback(self.ready)

    def content_size(self, callback):
        callback([900, 2000])

    def export_pdf(self, path, callback, layout):
        self.exports.append((path, callback, layout))

    def toHtml(self, callback):
        callback(self.html)

    def deactivate(self):
        self._render_cancel.set()

    def deleteLater(self):
        self.cleaned = True


@pytest.fixture
def export_owner(qapp, tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    source.write_text("# SAVED disk", encoding="utf-8")
    owner = _Owner(source)
    monkeypatch.setattr(renderer, "RendererView", _SnapshotRenderer)
    monkeypatch.setattr(export_actions, "show_pdf_progress", lambda *args: None)
    monkeypatch.setattr(export_actions, "show_export_complete", lambda *args: None)
    yield owner
    job = getattr(owner, "_source_export_job", None)
    if job is not None:
        job._finish(False)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("size", ["A4", "single"])
def test_pdf_waits_for_full_snapshot_and_ignores_later_editor_changes(
    export_owner, monkeypatch, tmp_path, size,
):
    completed = []
    monkeypatch.setattr(export_actions, "on_pdf_exported", lambda *args: completed.append(args))
    path = str(tmp_path / "result.pdf")
    export_actions._start_source_snapshot_export(
        export_owner, path, {"size": size, "orientation": "portrait"},
    )
    job = export_owner._source_export_job
    snapshot = job.view
    export_owner._editor.toPlainText = lambda: "# LATER typing"
    job._poll_ready()
    assert snapshot.snapshot == "# UNSAVED snapshot"
    assert snapshot.exports == []
    assert export_owner.exporting is True
    assert export_owner._export_btn.isEnabled() is False
    assert snapshot.options["base_url"] == QUrl.fromLocalFile(str(tmp_path) + "/")
    assert snapshot.options["defer_code"] is False
    snapshot.ready = 1
    job._poll_ready()
    assert len(snapshot.exports) == 1
    assert str(snapshot._render_generation) in snapshot.probe
    snapshot.exports[0][1](path, True)
    job._on_ready(1)
    job._finish(False)
    assert completed == [(export_owner, path, True)]
    assert snapshot.cleaned and snapshot._render_cancel.is_set()
    assert export_owner._source_export_job is None and export_owner.exporting is False
    assert export_owner.current_file.read_text("utf-8") == "# SAVED disk"


@pytest.mark.parametrize("failure", ["conversion", "timeout", "dimensions"])
def test_source_pdf_failure_cleans_once_without_printing(export_owner, monkeypatch, failure):
    completed = []
    monkeypatch.setattr(export_actions, "on_pdf_exported", lambda *args: completed.append(args))
    export_actions._start_source_snapshot_export(
        export_owner, "result.pdf", {"size": "single", "orientation": "portrait"},
    )
    job = export_owner._source_export_job
    snapshot = job.view
    if failure == "conversion":
        job._on_ready(-1)
    elif failure == "timeout":
        job.timeout.timeout.emit()
    else:
        snapshot.content_size = lambda callback: callback(None)
        job._on_ready(1)
    job._on_ready(1)
    assert snapshot.exports == [] and snapshot.cleaned
    assert len(completed) == 1 and completed[0][-1] is False
    assert not export_owner.exporting


def test_source_html_uses_snapshot_and_keeps_relative_assets(export_owner, tmp_path):
    destination = tmp_path / "export.html"
    export_actions._start_source_snapshot_export(export_owner, str(destination), None)
    job = export_owner._source_export_job
    assert job.view.options["defer_code"] is False
    job._on_ready(1)
    value = destination.read_text("utf-8")
    assert "UNSAVED snapshot" in value and "SAVED disk" not in value
    assert f'<base href="{QUrl.fromLocalFile(str(tmp_path) + "/").toString()}">' in value
    assert export_owner._source_export_job is None
    assert export_owner._export_btn.isEnabled()
    assert export_owner.current_file.read_text("utf-8") == "# SAVED disk"


@pytest.mark.parametrize("backend", [edit_backend.SOURCE_BACKEND, edit_backend.SPLIT_BACKEND])
@pytest.mark.parametrize("kind", ["docx", "pptx"])
def test_source_office_exports_feed_live_buffer(export_owner, monkeypatch, tmp_path, backend, kind):
    from app import docx_export, fragment_render, pptx_export

    export_owner._active_edit_backend = backend
    captured = []
    monkeypatch.setattr(fragment_render, "FragmentRenderer", lambda **kwargs: None)
    monkeypatch.setattr(export_actions.QFileDialog, "getSaveFileName",
                        lambda *args: (str(tmp_path / f"result.{kind}"), ""))
    module = docx_export if kind == "docx" else pptx_export
    monkeypatch.setattr(module, f"export_markdown_to_{kind}",
                        lambda text, *args, **kwargs: captured.append(text) or 1)
    getattr(export_actions, f"export_{kind}")(export_owner)
    assert captured == ["# UNSAVED snapshot"]
    assert export_owner.current_file.read_text("utf-8") == "# SAVED disk"


def test_source_pdf_action_routes_to_snapshot_job(export_owner, monkeypatch, tmp_path):
    monkeypatch.setattr(export_actions, "ask_page_setup",
                        lambda *args: {"size": "A4", "orientation": "portrait"})
    monkeypatch.setattr(export_actions.QFileDialog, "getSaveFileName",
                        lambda *args: (str(tmp_path / "result.pdf"), ""))
    monkeypatch.setattr(export_actions, "on_pdf_exported", lambda *args: None)
    export_actions.export_pdf(export_owner)
    job = export_owner._source_export_job
    assert job.view.snapshot == "# UNSAVED snapshot"
    # Reentrant export must keep the original job and immutable source text.
    export_actions.export_pdf(export_owner)
    assert export_owner._source_export_job is job
