"""Export action handlers delegated from MainWindow."""

import math
from pathlib import Path

from PySide6.QtCore import QMarginsF, QSettings, QSizeF, Qt, QUrl
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

from . import edit_backend
from .file_types import is_markdown
from .md_converter import read_text

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
    """True when the active editor backend is WYSIWYG (Vditor).

    Export actions relax their usual "no export while editing" guard only
    for this backend. PDF/HTML use the live WysiwygView page; source-based
    Word/PowerPoint exports are entered through MainWindow's acknowledged
    snapshot gate. Split/source-code editing still blocks exports because its
    on-screen renderer can lag the source buffer.
    """
    return (
        getattr(window, "_active_edit_backend", None) == edit_backend.WYSIWYG_BACKEND
        and window._wysiwyg_view is not None
    )


def _export_blocked(window) -> bool:
    return window._edit_mode and not _wysiwyg_active(window)


def _export_source_text(window) -> str | None:
    """Markdown text to feed docx/pptx export: live buffer in WYSIWYG, disk otherwise."""
    if _wysiwyg_active(window):
        return window._editor.toPlainText()
    result = read_text(window._current_file)
    if result is None:
        return None
    return result[0]


def export_pdf(window):
    if (
        not window._current_file
        or _export_blocked(window)
        or not is_markdown(window._current_file)
    ):
        return
    setup = ask_page_setup(window)
    if setup is None:
        return
    default = str(window._current_file.with_suffix(".pdf"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 PDF", default, "PDF 檔案 (*.pdf)"
    )
    if not path:
        return

    window._export_btn.setEnabled(False)
    if _wysiwyg_active(window):
        # The WysiwygView page already *is* the live buffer (push model), so
        # printing it directly needs no extra "feed buffer to renderer" step.
        # "single long page" has no equivalent for printToPdf's paginated
        # engine; fall back to A4 at the chosen orientation for that case.
        size_key = "A4" if setup["size"] == "single" else setup["size"]
        layout = pdf_layout(size_key, setup["orientation"])
        show_pdf_progress(window)
        view = window._wysiwyg_view

        def _on_wysiwyg_pdf(exported_path, ok, view=view):
            try:
                view.page().pdfPrintingFinished.disconnect(_on_wysiwyg_pdf)
            except (RuntimeError, TypeError):
                pass
            on_pdf_exported(window, exported_path, ok)

        view.page().pdfPrintingFinished.connect(_on_wysiwyg_pdf)
        view.page().printToPdf(path, layout)
        return
    if setup["size"] == "single":
        window._pending_pdf_path = path
        window._renderer.content_size(window._export_single_page)
    else:
        layout = pdf_layout(setup["size"], setup["orientation"])
        show_pdf_progress(window)
        window._renderer.export_pdf(path, window._on_pdf_exported, layout)


def export_pptx(window):
    if (
        window._exporting
        or not window._current_file
        or _export_blocked(window)
        or not is_markdown(window._current_file)
    ):
        return
    text = _export_source_text(window)
    if text is None:
        QMessageBox.warning(window, "匯出 PPT", "無法讀取檔案內容。")
        return
    default = str(window._current_file.with_suffix(".pptx"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 PPT", default, "PowerPoint 簡報 (*.pptx)"
    )
    if not path:
        return

    window._exporting = True
    renderer = None
    provider = None
    # Render Mermaid / math fragments to images via the web engine; if that
    # module can't be built, export still works with source-code boxes.
    try:
        from .fragment_render import FragmentRenderer

        renderer = FragmentRenderer(parent=window)
        provider = renderer.provide
    except Exception:
        renderer = None
        provider = None

    try:
        from .pptx_export import export_markdown_to_pptx

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = export_markdown_to_pptx(
                text,
                path,
                base_dir=window._current_file.parent,
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
        window._exporting = False
    window.statusBar().showMessage(
        f"已匯出 {count} 張投影片至 {Path(path).name}", 5000
    )


def export_docx(window):
    if (
        window._exporting
        or not window._current_file
        or _export_blocked(window)
        or not is_markdown(window._current_file)
    ):
        return
    text = _export_source_text(window)
    if text is None:
        QMessageBox.warning(window, "匯出 Word", "無法讀取檔案內容。")
        return
    default = str(window._current_file.with_suffix(".docx"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 Word", default, "Word 文件 (*.docx)"
    )
    if not path:
        return

    window._exporting = True
    renderer = None
    provider = None
    try:
        from .fragment_render import FragmentRenderer

        renderer = FragmentRenderer(parent=window)
        provider = renderer.provide
    except Exception:
        renderer = None
        provider = None

    try:
        from .docx_export import export_markdown_to_docx

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            export_markdown_to_docx(
                text,
                path,
                base_dir=window._current_file.parent,
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
        window._exporting = False
    window.statusBar().showMessage(
        f"已匯出 Word 文件至 {Path(path).name}", 5000
    )


def export_html(window):
    """Export Vditor's own rendered HTML (``getHTML()``) -- WYSIWYG only.

    Unlike PDF/Word/PPT, this has no Python-side rendering pipeline of its
    own: it is only reachable from the WYSIWYG toolbar/context menu and
    reads whatever Vditor currently renders, buffer included.
    """
    if (
        not window._current_file
        or not is_markdown(window._current_file)
        or not _wysiwyg_active(window)
    ):
        window.statusBar().showMessage(
            "請先在所見即所得編輯模式下匯出 HTML", 4000
        )
        return
    default = str(window._current_file.with_suffix(".html"))
    path, _ = QFileDialog.getSaveFileName(
        window, "匯出 HTML", default, "HTML 檔案 (*.html)"
    )
    if not path:
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

    window._wysiwyg_view.get_html(_on_html)


def ask_page_setup(window):
    settings = QSettings("markdown-viewer", "MarkdownViewer")
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


def export_single_page(window, dims):
    try:
        measured_w = float(dims[0])
        h_px = float(dims[1])
    except (TypeError, ValueError, IndexError):
        measured_w, h_px = 0.0, 0.0

    if (
        not math.isfinite(measured_w)
        or not math.isfinite(h_px)
        or measured_w <= 0
        or h_px <= 0
    ):
        window._pending_pdf_path = None
        window._export_btn.setEnabled(
            bool(window._current_file) and not window._edit_mode
        )
        window._refresh_icons()
        QMessageBox.warning(
            window,
            "匯出 PDF",
            "無法取得完整頁面尺寸，請等待文件載入完成後再重試。",
        )
        return

    # Base the page width on the actual viewport so the PDF mirrors the
    # on-screen layout; widen if the content itself overflows (wide tables).
    w_px = max(float(window._renderer.width()), measured_w)
    if w_px < 200:
        w_px = 800.0

    w_pt = w_px * _PT_PER_PX
    h_pt = (h_px + 4) * _PT_PER_PX

    layout = QPageLayout(
        QPageSize(
            QSizeF(w_pt, h_pt),
            QPageSize.Unit.Point,
            "Continuous",
            QPageSize.SizeMatchPolicy.ExactMatch,
        ),
        QPageLayout.Orientation.Portrait,
        QMarginsF(0, 0, 0, 0),
        QPageLayout.Unit.Point,
    )

    show_pdf_progress(window)
    window._renderer.export_pdf(
        window._pending_pdf_path, window._on_pdf_exported, layout
    )
    window._pending_pdf_path = None


def show_pdf_progress(window):
    window._pdf_progress = QProgressDialog("正在匯出 PDF…", None, 0, 0, window)
    window._pdf_progress.setWindowTitle("匯出 PDF")
    window._pdf_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    window._pdf_progress.setMinimumDuration(0)
    window._pdf_progress.setAutoClose(False)
    window._pdf_progress.setAutoReset(False)
    window._pdf_progress.show()


def close_pdf_progress(window):
    if window._pdf_progress is not None:
        window._pdf_progress.close()
        window._pdf_progress = None


def on_pdf_exported(window, path: str, ok: bool):
    close_pdf_progress(window)
    window._export_btn.setEnabled(bool(window._current_file) and not window._edit_mode)
    window._refresh_icons()
    if not ok:
        window.statusBar().clearMessage()
        QMessageBox.warning(window, "匯出 PDF", "匯出失敗，請重試。")
        return

    window.statusBar().showMessage(f"已匯出 PDF：{path}", 5000)
    box = QMessageBox(window)
    box.setWindowTitle("匯出 PDF")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(f"已成功匯出：\n{path}")
    open_btn = box.addButton("開啟 PDF", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("關閉", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is open_btn:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
