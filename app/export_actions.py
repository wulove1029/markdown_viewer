"""Export action handlers delegated from MainWindow."""

import html
import math
from pathlib import Path

from PySide6.QtCore import QMarginsF, QObject, QSettings, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPageLayout, QPageSize
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QMessageBox,
    QProgressDialog,
)
from shiboken6 import isValid

from . import edit_backend
from .file_types import is_markdown
from .md_converter import read_text
from .pdf_repair_async import repair_async
from .settings_store import APP, ORG
from .worker_relay import call_on_destroyed

# PDF export page sizes (key -> QPageSize id) plus a "single" long-page mode.
_PDF_PAGE_SIZES = {
    "A4": QPageSize.PageSizeId.A4,
    "A3": QPageSize.PageSizeId.A3,
    "Letter": QPageSize.PageSizeId.Letter,
    "Legal": QPageSize.PageSizeId.Legal,
}
_PDF_SIZE_CHOICES = [
    ("A4", "A4"),
    ("A3", "A3"),
    ("Letter", "Letter（美規信紙）"),
    ("Legal", "Legal（美規法律）"),
    ("single", "單一長頁（不分頁）"),
]
_PT_PER_PX = 72.0 / 96.0


def _wysiwyg_active(window) -> bool:
    """Identify the live Office surface, whose snapshot gate belongs to MainWindow."""
    return (
        getattr(window, "_active_edit_backend", None) == edit_backend.WYSIWYG_BACKEND
        and window.wysiwyg_view is not None
    )


def _export_blocked(window) -> bool:
    return bool(getattr(window, "exporting", False))


def _export_source_text(window) -> str | None:
    """Read the complete live editor snapshot when editing, or the saved source."""
    if window.edit_mode:
        return window._editor.toPlainText()
    result = read_text(window.current_file)
    if result is None:
        return None
    return result[0]


def _fragment_provider(window, failed: list[str]):
    """Build the Mermaid/math image provider for an Office export.

    Returns ``(renderer, provider)``. Every fragment the provider could not
    render (including when the renderer itself could not be built) is
    appended to *failed*, so the export can tell the user instead of quietly
    shipping source text where an image was expected.
    """
    renderer = None
    try:
        from .fragment_render import FragmentRenderer

        renderer = FragmentRenderer(parent=window)
    except Exception:
        renderer = None
    inner = getattr(renderer, "provide", None)

    def provider(kind: str, source: str):
        result = inner(kind, source) if inner is not None else None
        if result is None:
            failed.append(kind)
        return result

    return renderer, provider


def _warn_unrendered_fragments(window, title: str, failed: list[str]) -> None:
    if not failed:
        return
    parts = []
    if failed.count("mermaid"):
        parts.append(f"{failed.count('mermaid')} 個 Mermaid 圖表")
    if failed.count("math"):
        parts.append(f"{failed.count('math')} 個數學式")
    QMessageBox.warning(
        window, title,
        f"{'、'.join(parts)}未能轉成圖片（電腦忙碌或內容有誤時可能發生），"
        "文件中已改放標示為未轉換的原始碼。",
    )


def export_pdf(window):
    if (
        not window.current_file
        or _export_blocked(window)
        or not is_markdown(window.current_file)
    ):
        return
    if getattr(window, "_pdf_export_busy", False):
        # An export is still printing or repairing its PDF: refuse here, before
        # any dialog, so the running export's callback and progress dialog are
        # never touched by a second request.
        window.statusBar().showMessage("PDF 仍在處理中", 5000)
        return
    setup = ask_page_setup(window)
    if setup is None:
        return
    default = str(window.current_file.with_suffix(".pdf"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 PDF", default, "PDF 檔案 (*.pdf)"
    )
    if not path:
        return

    window._export_btn.setEnabled(False)
    window._pdf_export_busy = True  # released by on_pdf_exported / _complete
    if _wysiwyg_active(window):
        _export_wysiwyg_pdf(window, path, setup)
        return
    if window.edit_mode:
        _start_source_snapshot_export(window, path, setup)
        return
    if setup["size"] == "single":
        window._pending_pdf_path = path
        window.renderer.content_size(window._export_single_page)
    else:
        layout = pdf_layout(setup["size"], setup["orientation"])
        show_pdf_progress(window)
        window.renderer.export_pdf(
            path, window._on_pdf_exported, layout,
            async_repair=True, on_repair_failed=_repair_failed_handler(window),
        )


def export_pptx(window):
    if (
        window.exporting
        or not window.current_file
        or _export_blocked(window)
        or not is_markdown(window.current_file)
    ):
        return
    text = _export_source_text(window)
    if text is None:
        QMessageBox.warning(window, "匯出 PPT", "無法讀取檔案內容。")
        return
    default = str(window.current_file.with_suffix(".pptx"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 PPT", default, "PowerPoint 簡報 (*.pptx)"
    )
    if not path:
        return

    window.exporting = True
    # Render Mermaid / math fragments to images via the web engine; if that
    # module can't be built, export still works with source-code boxes.
    failed: list[str] = []
    renderer, provider = _fragment_provider(window, failed)

    try:
        from .pptx_export import export_markdown_to_pptx

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = export_markdown_to_pptx(
                text,
                path,
                base_dir=window.current_file.parent,
                image_provider=provider,
            )
        finally:
            QApplication.restoreOverrideCursor()
            if renderer is not None:
                renderer.cleanup()
    except Exception as exc:
        QMessageBox.warning(window, "匯出 PPT", f"匯出失敗：{exc}")
        return
    finally:
        window.exporting = False
    window.statusBar().showMessage(
        f"已匯出 {count} 張投影片至 {Path(path).name}", 5000
    )
    _warn_unrendered_fragments(window, "匯出 PPT", failed)
    show_export_complete(window, path)


def export_docx(window):
    if (
        window.exporting
        or not window.current_file
        or _export_blocked(window)
        or not is_markdown(window.current_file)
    ):
        return
    text = _export_source_text(window)
    if text is None:
        QMessageBox.warning(window, "匯出 Word", "無法讀取檔案內容。")
        return
    default = str(window.current_file.with_suffix(".docx"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 Word", default, "Word 文件 (*.docx)"
    )
    if not path:
        return

    window.exporting = True
    failed: list[str] = []
    renderer, provider = _fragment_provider(window, failed)

    try:
        from .docx_export import export_markdown_to_docx

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            export_markdown_to_docx(
                text,
                path,
                base_dir=window.current_file.parent,
                image_provider=provider,
            )
        finally:
            QApplication.restoreOverrideCursor()
            if renderer is not None:
                renderer.cleanup()
    except Exception as exc:
        QMessageBox.warning(window, "匯出 Word", f"匯出失敗：{exc}")
        return
    finally:
        window.exporting = False
    window.statusBar().showMessage(
        f"已匯出 Word 文件至 {Path(path).name}", 5000
    )
    _warn_unrendered_fragments(window, "匯出 Word", failed)
    show_export_complete(window, path)


def export_html(window):
    """Export the live Office HTML or a freshly rendered Markdown snapshot."""
    if (
        not window.current_file
        or not is_markdown(window.current_file)
        or _export_blocked(window)
    ):
        return
    default = str(window.current_file.with_suffix(".html"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 HTML", default, "HTML 檔案 (*.html)"
    )
    if not path:
        return
    if not _wysiwyg_active(window):
        _start_source_snapshot_export(window, path, None)
        return

    def _on_html(html_value, path=path, window=window):
        if not isinstance(html_value, str):
            QMessageBox.warning(window, "匯出 HTML", "匯出失敗，請重試。")
            return
        try:
            Path(path).write_text(html_value, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(window, "匯出 HTML", f"匯出失敗：{exc}")
            return
        window.statusBar().showMessage(f"已匯出 HTML 至 {Path(path).name}", 5000)
        show_export_complete(window, path)

    window.wysiwyg_view.get_html(_on_html)


def _start_source_snapshot_export(window, path: str, setup: dict | None) -> None:
    """Own an isolated renderer so delayed split previews cannot leak into exports."""
    text = _export_source_text(window)
    if text is None:
        window._pdf_export_busy = False
        window._export_btn.setEnabled(bool(window.current_file))
        QMessageBox.warning(window, "匯出文件", "無法讀取檔案內容。")
        return
    try:
        job = _SourceSnapshotExport(window, path, setup)
        window._source_export_job = job
        window.exporting = True
        window._export_btn.setEnabled(False)
        show_pdf_progress(window)
        if setup is None and window.pdf_progress is not None:
            window.pdf_progress.setLabelText("正在匯出 HTML…")
            window.pdf_progress.setWindowTitle("匯出 HTML")
        job.start(text, Path(window.current_file))
    except BaseException:
        # Nothing asynchronous owns the export yet: undo every guard so the
        # next export is not refused, tell the user, then let the error out.
        window._pdf_export_busy = False
        window.exporting = False
        window._source_export_job = None
        close_pdf_progress(window)
        window._export_btn.setEnabled(bool(window.current_file))
        QMessageBox.warning(window, "匯出文件", "匯出失敗，請重試。")
        raise


SNAPSHOT_READY_JS = """(function() {
    const marker = document.querySelector('meta[name="markdown-viewer-render-generation"]');
    if (!marker || Number(marker.content) !== GENERATION) return 0;
    if (document.querySelector('meta[name="markdown-viewer-render-error"]')) return -1;
    if (document.readyState !== 'complete' || document.fonts.status === 'loading') return 0;
    if (Array.from(document.images).some(image => !image.complete)) return 0;
    if (window.mdDiagrams) {
        window.mdDiagrams.ensureAll();
        if (!window.mdDiagrams.status().complete) return 0;
    } else if (Array.from(document.querySelectorAll('.mermaid')).some(
        diagram => !diagram.querySelector('svg'))) return 0;
    return 1;
})()"""


def snapshot_ready_js(generation: int) -> str:
    """Probe script: 1 when the rendered snapshot is printable, -1 on error."""
    return SNAPSHOT_READY_JS.replace("GENERATION", str(generation))


def html_with_base(value: str, base_url: QUrl) -> str:
    """Keep relative local assets valid when the destination is another folder."""
    if "<base " not in value:
        base = html.escape(base_url.toString(), quote=True)
        value = value.replace("<head>", f'<head><base href="{base}">', 1)
    return value


class _SourceSnapshotExport(QObject):
    """Print one immutable source snapshot and release every owned Qt resource."""

    def __init__(self, window, path: str, setup: dict | None):
        super().__init__(window)
        from .renderer import RendererView

        self.window = window
        self.path = path
        self.setup = setup
        self.phase = "preparing"
        self.view = RendererView(parent=window)
        self.view.hide()
        self.view.resize(max(800, window.renderer.width()), 700)
        self.poll = QTimer(self)
        self.poll.setInterval(50)
        self.poll.timeout.connect(self._poll_ready)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._finish(False))
        self._checking = False
        self.base_url = QUrl()
        self._cancel_on_destroy = None

    def start(self, text: str, source: Path) -> None:
        self.base_url = QUrl.fromLocalFile(str(source.resolve().parent) + "/")
        self.view.render_markdown_text(
            text, theme="light", title=source.stem, base_url=self.base_url,
            defer_code=False,
        )
        # A closing window must cancel conversion even if its event loop is leaving.
        # The Event is shared with the render worker, so it is reached through a
        # closure rather than connecting its bound set method, which PySide
        # would track weakly and tear down wherever the worker releases it.
        self._cancel_on_destroy = call_on_destroyed(self.window, self.view._render_cancel.set)
        self.timeout.start(60000)
        self.poll.start()

    def _poll_ready(self) -> None:
        if self.phase != "preparing" or self._checking:
            return
        self._checking = True
        generation = self.view._render_generation
        self.view.page().runJavaScript(
            snapshot_ready_js(generation), self._on_ready)

    def _on_ready(self, value) -> None:
        self._checking = False
        if self.phase != "preparing":
            return
        if value == -1 or self.view._render_failed_generation is not None:
            self._finish(False)
            return
        if value != 1:
            return
        self.poll.stop()
        self.phase = "exporting"
        if self.setup is None:
            self.view.page().toHtml(self._write_html)
        elif self.setup["size"] == "single":
            self.view.content_size(self._print_single)
        else:
            self._print(pdf_layout(self.setup["size"], self.setup["orientation"]))

    def _print_single(self, dimensions) -> None:
        if self.phase != "exporting":
            return
        layout = _single_page_layout(dimensions, self.view.width())
        if layout is None:
            self._finish(False)
        else:
            self._print(layout)

    def _print(self, layout) -> None:
        try:
            self.view.export_pdf(
                self.path, lambda _path, ok: self._finish(bool(ok)), layout,
                async_repair=True,
                on_repair_failed=_repair_failed_handler(self.window),
            )
        except (RuntimeError, TypeError):
            self._finish(False)

    def _write_html(self, value) -> None:
        if self.phase != "exporting":
            return
        if not isinstance(value, str) or not value:
            self._finish(False)
            return
        value = html_with_base(value, self.base_url)
        try:
            Path(self.path).write_text(value, encoding="utf-8")
        except OSError:
            self._finish(False)
            return
        self._finish(True)

    def _finish(self, ok: bool) -> None:
        if self.phase == "done":
            return
        self.phase = "done"
        self.poll.stop()
        self.timeout.stop()
        if self._cancel_on_destroy is not None:
            self.window.destroyed.disconnect(self._cancel_on_destroy)
            self._cancel_on_destroy = None
        self.view.deactivate()
        self.view.deleteLater()
        window = self.window
        window._source_export_job = None
        window.exporting = False
        self.deleteLater()
        if self.setup is not None:
            on_pdf_exported(window, self.path, ok)
            return
        close_pdf_progress(window)
        window._export_btn.setEnabled(bool(window.current_file))
        window._refresh_icons()
        if ok:
            window.statusBar().showMessage(f"已匯出 HTML 至 {Path(self.path).name}", 5000)
            show_export_complete(window, self.path)
        else:
            QMessageBox.warning(window, "匯出 HTML", "匯出失敗，請重試。")


def ask_page_setup(window):
    settings = QSettings(ORG, APP)
    last_size = settings.value("pdf_page_size", "A4") or "A4"
    last_orient = settings.value("pdf_orientation", "portrait") or "portrait"

    dialog = QDialog(window)
    dialog.setWindowTitle("匯出 PDF 設定")
    form = QFormLayout(dialog)

    size_combo = QComboBox(dialog)
    for key, label in _PDF_SIZE_CHOICES:
        size_combo.addItem(label, key)
    size_index = next(
        (i for i, (k, _) in enumerate(_PDF_SIZE_CHOICES) if k == last_size), 0
    )
    size_combo.setCurrentIndex(size_index)

    orient_combo = QComboBox(dialog)
    orient_combo.addItem("直向", "portrait")
    orient_combo.addItem("橫向", "landscape")
    orient_combo.setCurrentIndex(1 if last_orient == "landscape" else 0)

    def _sync_orientation():
        orient_combo.setEnabled(size_combo.currentData() != "single")

    size_combo.currentIndexChanged.connect(_sync_orientation)
    _sync_orientation()

    form.addRow("紙張大小", size_combo)
    form.addRow("方向", orient_combo)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=dialog,
    )
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("匯出")
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    size_key = size_combo.currentData()
    orientation = orient_combo.currentData()
    settings.setValue("pdf_page_size", size_key)
    settings.setValue("pdf_orientation", orientation)
    return {"size": size_key, "orientation": orientation}


def pdf_layout(size_key: str, orientation: str) -> QPageLayout:
    size_id = _PDF_PAGE_SIZES.get(size_key, QPageSize.PageSizeId.A4)
    orient = (
        QPageLayout.Orientation.Landscape
        if orientation == "landscape"
        else QPageLayout.Orientation.Portrait
    )
    return QPageLayout(
        QPageSize(size_id),
        orient,
        QMarginsF(12, 12, 12, 12),
        QPageLayout.Unit.Millimeter,
    )


def _single_page_layout(dims, viewport_width) -> QPageLayout | None:
    """Build an exact one-page layout from validated CSS-pixel dimensions."""
    try:
        measured_w = float(dims[0])
        h_px = float(dims[1])
        viewport_w = float(viewport_width)
    except (TypeError, ValueError, IndexError):
        return None

    if (
        not math.isfinite(measured_w)
        or not math.isfinite(h_px)
        or measured_w <= 0
        or h_px <= 0
    ):
        return None
    if not math.isfinite(viewport_w) or viewport_w <= 0:
        viewport_w = 0.0

    # Mirror the visible document width while still accommodating content
    # that genuinely overflows it (for example, a wide Markdown table).
    w_px = max(viewport_w, measured_w)
    if w_px < 200:
        w_px = 800.0

    return QPageLayout(
        QPageSize(
            QSizeF(w_px * _PT_PER_PX, (h_px + 4) * _PT_PER_PX),
            QPageSize.Unit.Point,
            "Continuous",
            QPageSize.SizeMatchPolicy.ExactMatch,
        ),
        QPageLayout.Orientation.Portrait,
        QMarginsF(0, 0, 0, 0),
        QPageLayout.Unit.Point,
    )


def _export_wysiwyg_pdf(window, path: str, setup: dict) -> None:
    """Prepare the live Office surface, print it, then always restore it."""
    view = window.wysiwyg_view
    show_pdf_progress(window)
    state = {"phase": "preparing", "cleaned": False, "connected": False}
    page = None

    def _cleanup() -> None:
        if state["cleaned"]:
            return
        state["cleaned"] = True
        finish = getattr(view, "finish_pdf_export", None)
        if callable(finish):
            try:
                finish()
            except (RuntimeError, TypeError):
                pass

    def _disconnect() -> None:
        nonlocal page
        if not state["connected"] or page is None:
            return
        state["connected"] = False
        try:
            page.pdfPrintingFinished.disconnect(_on_printed)
        except (RuntimeError, TypeError):
            pass

    def _complete(exported_path: str, ok: bool) -> None:
        if state["phase"] == "done":
            return
        state["phase"] = "done"
        window._pdf_export_busy = False
        _disconnect()
        try:
            _cleanup()
        finally:
            on_pdf_exported(window, exported_path, bool(ok))

    def _on_printed(exported_path, ok) -> None:
        if state["phase"] != "printing":
            return
        if not ok:
            _complete(str(exported_path), False)
            return
        # Keep the progress dialog up while a worker thread repairs the text
        # layer; the export is reported only afterwards.  "printing" phase
        # stays set so a second print signal is ignored meanwhile.
        state["phase"] = "repairing"

        def _repaired(succeeded: bool) -> None:
            if not isValid(window):
                return
            if not succeeded:
                _repair_failed_handler(window)(str(exported_path))
            state["phase"] = "printing"
            _complete(str(exported_path), True)

        try:
            repair_async(str(exported_path), _repaired)
        except BaseException:
            # Could not hand the work to the worker: still restore the live
            # surface and close the progress dialog, then let it propagate.
            _repaired(False)
            raise

    def _on_prepared(result) -> None:
        nonlocal page
        if state["phase"] != "preparing":
            return
        prepared = result is True or (
            isinstance(result, dict) and result.get("ok") is True
        )
        if not prepared:
            _complete(path, False)
            return

        if setup["size"] == "single":
            dims = (
                (result.get("width"), result.get("height"))
                if isinstance(result, dict)
                else None
            )
            # Office preparation reports the printable content shell width.
            # Do not widen it back to the whole widget (which can include the
            # outline/sidebar); doing so changes line wrapping in the PDF.
            layout = _single_page_layout(dims, 0)
            if layout is None:
                _complete(path, False)
                return
        else:
            layout = pdf_layout(setup["size"], setup["orientation"])

        try:
            page = view.page()
            page.pdfPrintingFinished.connect(_on_printed)
            state["connected"] = True
            state["phase"] = "printing"
            page.printToPdf(path, layout)
        except (AttributeError, RuntimeError, TypeError):
            _complete(path, False)

    def _view_destroyed(*_args) -> None:
        # The view died before the export finished: release the guard and
        # close the progress dialog instead of leaving both stuck.
        if state["phase"] in ("preparing", "printing") and isValid(window):
            _complete(path, False)

    destroyed = getattr(view, "destroyed", None)
    if destroyed is not None and hasattr(destroyed, "connect"):
        destroyed.connect(_view_destroyed)

    prepare = getattr(view, "prepare_pdf_export", None)
    if not callable(prepare):
        _complete(path, False)
        return
    try:
        prepare(_on_prepared)
    except (RuntimeError, TypeError):
        if state["phase"] == "preparing":
            _complete(path, False)


def export_single_page(window, dims):
    layout = _single_page_layout(dims, window.renderer.width())
    if layout is None:
        window._pending_pdf_path = None
        window._pdf_export_busy = False
        window._export_btn.setEnabled(
            bool(window.current_file) and is_markdown(window.current_file)
        )
        window._refresh_icons()
        QMessageBox.warning(
            window,
            "匯出 PDF",
            "無法取得完整頁面尺寸，請等待文件載入完成後再重試。",
        )
        return

    show_pdf_progress(window)
    window.renderer.export_pdf(
        window._pending_pdf_path, window._on_pdf_exported, layout,
        async_repair=True, on_repair_failed=_repair_failed_handler(window),
    )
    window._pending_pdf_path = None


def show_pdf_progress(window):
    window.pdf_progress = QProgressDialog("正在匯出 PDF…", None, 0, 0, window)
    window.pdf_progress.setWindowTitle("匯出 PDF")
    window.pdf_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    window.pdf_progress.setMinimumDuration(0)
    window.pdf_progress.setAutoClose(False)
    window.pdf_progress.setAutoReset(False)
    window.pdf_progress.show()


def close_pdf_progress(window):
    if window.pdf_progress is not None:
        window.pdf_progress.close()
        window.pdf_progress = None


def _repair_failed_handler(window):
    """Callback that remembers the PDF text repair failed, for on_pdf_exported."""
    def handler(_path: str) -> None:
        window._pdf_repair_failed = True
    return handler


def on_pdf_exported(window, path: str, ok: bool):
    window._pdf_export_busy = False
    repair_failed = bool(getattr(window, "_pdf_repair_failed", False))
    window._pdf_repair_failed = False
    close_pdf_progress(window)
    window._export_btn.setEnabled(
        bool(window.current_file)
        and is_markdown(window.current_file)
    )
    window._refresh_icons()
    if not ok:
        window.statusBar().clearMessage()
        QMessageBox.warning(window, "匯出 PDF", "匯出失敗，請重試。")
        return

    window.statusBar().showMessage(f"已匯出 PDF：{path}", 5000)
    if repair_failed:
        QMessageBox.warning(
            window,
            "匯出 PDF",
            "PDF 已匯出，但文字層修正失敗，已保留未修正的檔案。\n"
            "複製或搜尋 PDF 中的部分中文字（如部首字形）可能不正確。",
        )
    show_export_complete(window, path)


def show_export_complete(window, path: str) -> None:
    """Offer the same local file and folder actions for every export format."""
    destination = Path(path).absolute()
    box = QMessageBox(window)
    box.setWindowTitle("匯出完成")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(f"已成功匯出：\n{path}")
    open_btn = box.addButton("開啟檔案", QMessageBox.ButtonRole.AcceptRole)
    folder_btn = box.addButton("開啟所在資料夾", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("關閉", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is open_btn:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination)))
    elif box.clickedButton() is folder_btn:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination.parent)))
