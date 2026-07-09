"""Session persistence helpers delegated from MainWindow."""

import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from .file_types import document_kind, is_markdown, is_supported_document
from .md_converter import set_user_css
from .settings_dialog import SettingsDialog

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


def restore_geometry(window):
    settings = QSettings(_ORG, _APP)
    geometry = settings.value("geometry")
    if geometry:
        window.restoreGeometry(geometry)
    else:
        window.resize(1200, 750)


def restore_last_session(window):
    settings = QSettings(_ORG, _APP)
    raw = settings.value("open_tabs")
    paths = []
    if raw:
        try:
            paths = json.loads(raw)
        except (ValueError, TypeError):
            paths = []
    paths = [
        p for p in paths if p and is_supported_document(p) and Path(p).exists()
    ]
    if paths:
        # Add every remembered tab but load only the active one (the others load
        # lazily when first selected).
        for p in paths:
            kind = document_kind(Path(p))
            if kind:
                window._add_tab(Path(p), kind)
        active = settings.value("active_tab", 0)
        try:
            active = int(active)
        except (ValueError, TypeError):
            active = 0
        active = max(0, min(active, window._tab_bar.count() - 1))
        window._tab_guard = True
        window._tab_bar.setCurrentIndex(active)
        window._tab_guard = False
        window._activate_tab(active)
        return
    # Fallback to the single last_file remembered by older versions.
    last = settings.value("last_file")
    if last and is_supported_document(last) and Path(last).exists():
        window._open_file(last)


def pdf_pages_map() -> dict:
    raw = QSettings(_ORG, _APP).value("pdf_last_pages")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_pdf_page(window, page0: int):
    if not window._current_file:
        return
    pages = pdf_pages_map()
    pages[str(window._current_file)] = int(page0)
    if len(pages) > 200:
        for key in list(pages)[:-200]:
            del pages[key]
    QSettings(_ORG, _APP).setValue("pdf_last_pages", json.dumps(pages))


def save_active_view_state(window):
    """Capture the outgoing tab's view position before switching away."""
    if not window._active_path:
        return
    state = window._tab_state.get(window._active_path)
    if not state:
        return
    if state.get("kind") == "markdown":
        # Last value from the renderer's scroll poll (PDF page persists via
        # pdf_last_pages on page_changed, so nothing to capture for PDFs).
        state["scroll"] = window._renderer.scroll_y()


def load_user_css(window, reload: bool = False):
    path = QSettings(_ORG, _APP).value("custom_css_path", "") or ""
    css = ""
    if path:
        try:
            css = Path(path).read_text(encoding="utf-8")
        except OSError:
            css = ""
    set_user_css(css)
    if (
        reload
        and window._current_file
        and is_markdown(window._current_file)
        and not window._edit_mode
    ):
        window._renderer.reload_current()


def open_preferences(window):
    dialog = SettingsDialog(
        window,
        current_theme=window._theme_name,
        current_zoom=window._content_zoom,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    r = dialog.results
    window._apply_zoom(r["content_zoom"])
    new_theme = r.get("theme", window._theme_name)
    if new_theme != window._theme_name:
        window._theme_name = new_theme
        window._apply_theme()
    load_user_css(window, reload=True)


def toggle_theme(window):
    window._theme_name = "light" if window._theme_name == "dark" else "dark"
    QSettings(_ORG, _APP).setValue("theme", window._theme_name)
    window._apply_theme()


def toggle_annotation_side_notes(window, checked=None):
    window._side_notes_visible = (
        bool(checked) if checked is not None else window._side_notes_btn.isChecked()
    )
    QSettings(_ORG, _APP).setValue(
        "annotation_side_notes_visible", window._side_notes_visible
    )
    window._renderer.set_annotation_side_notes_visible(window._side_notes_visible)
    window._refresh_icons()


def apply_zoom(window, factor: float):
    window._content_zoom = window._renderer.set_zoom(factor)
    window._edit_preview.set_zoom(window._content_zoom)
    if window._current_kind == "pdf":
        window._pdf_view.set_zoom_factor(window._content_zoom)
    QSettings(_ORG, _APP).setValue("content_zoom", window._content_zoom)
    window.statusBar().showMessage(
        f"縮放：{round(window._content_zoom * 100)}%", 2000
    )


def close_event(window, event) -> bool:
    if not window._confirm_discard_edits():
        event.ignore()
        return False
    save_active_view_state(window)
    if not window._is_detached:
        settings = QSettings(_ORG, _APP)
        settings.setValue("geometry", window.saveGeometry())
        open_tabs = [
            window._tab_bar.tabData(i) for i in range(window._tab_bar.count())
        ]
        settings.setValue("open_tabs", json.dumps(open_tabs))
        settings.setValue("active_tab", window._tab_bar.currentIndex())
        if window._current_file:
            settings.setValue("last_file", str(window._current_file))
    return True
