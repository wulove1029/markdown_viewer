"""Session persistence helpers delegated from MainWindow."""

import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from .content_zoom import clamp_zoom_factor
from .edit_backend import (
    SETTINGS_KEY as EDIT_BACKEND_KEY,
    SPLIT_BACKEND,
    WYSIWYG_BACKEND,
    normalize_backend,
)
from .edit_backend import (
    PREVIEW_DOUBLE_CLICK_SETTINGS_KEY as PREVIEW_DOUBLE_CLICK_KEY,
    normalize_preview_double_click,
)
from .file_types import document_kind, is_markdown, is_supported_document
from .md_converter import set_user_css
from .settings_dialog import SettingsDialog

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"

DOCUMENT_EDIT_BACKENDS_KEY = "document_edit_backends_v1"
_DOCUMENT_EDIT_BACKENDS_LIMIT = 300
_DOCUMENT_EDIT_BACKEND_VALUES = {SPLIT_BACKEND, WYSIWYG_BACKEND}


def _document_backend_path_key(path) -> str | None:
    """Return the exact app-state key for a Markdown path, if supported."""
    try:
        key = str(Path(path))
    except (TypeError, ValueError, OSError):
        return None
    return key if key and is_markdown(key) else None


def _document_edit_backends() -> dict[str, str]:
    """Read and sanitize the per-document backend map from QSettings."""
    raw = QSettings(_ORG, _APP).value(DOCUMENT_EDIT_BACKENDS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    cleaned = {
        key: backend
        for key, backend in data.items()
        if isinstance(key, str)
        and is_markdown(key)
        and backend in _DOCUMENT_EDIT_BACKEND_VALUES
    }
    if len(cleaned) > _DOCUMENT_EDIT_BACKENDS_LIMIT:
        cleaned = dict(
            list(cleaned.items())[-_DOCUMENT_EDIT_BACKENDS_LIMIT:]
        )
    return cleaned


def _save_document_edit_backends(backends: dict[str, str]) -> None:
    QSettings(_ORG, _APP).setValue(
        DOCUMENT_EDIT_BACKENDS_KEY,
        json.dumps(backends, ensure_ascii=False),
    )


def load_document_edit_backend(path) -> str | None:
    """Return a Markdown document's remembered backend, if it has one."""
    key = _document_backend_path_key(path)
    if key is None:
        return None
    return _document_edit_backends().get(key)


def remember_document_edit_backend(path, backend: str) -> None:
    """Remember one Markdown backend, refreshing its bounded insertion order."""
    key = _document_backend_path_key(path)
    if key is None or backend not in _DOCUMENT_EDIT_BACKEND_VALUES:
        return
    backends = _document_edit_backends()
    backends.pop(key, None)
    backends[key] = backend
    while len(backends) > _DOCUMENT_EDIT_BACKENDS_LIMIT:
        backends.pop(next(iter(backends)))
    _save_document_edit_backends(backends)


def migrate_document_edit_backends(mapping) -> None:
    """Move remembered backend entries alongside renamed Markdown paths."""
    if not isinstance(mapping, dict):
        return
    backends = _document_edit_backends()
    changed = False
    for old_path, new_path in mapping.items():
        old_key = _document_backend_path_key(old_path)
        if old_key is None or old_key not in backends:
            continue
        backend = backends.pop(old_key)
        changed = True
        new_key = _document_backend_path_key(new_path)
        if new_key is None:
            continue
        backends.pop(new_key, None)
        backends[new_key] = backend
    if changed:
        while len(backends) > _DOCUMENT_EDIT_BACKENDS_LIMIT:
            backends.pop(next(iter(backends)))
        _save_document_edit_backends(backends)


def forget_document_edit_backends(paths) -> None:
    """Forget remembered backend entries for one path or an iterable of paths."""
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    try:
        iterator = iter(paths)
    except TypeError:
        return
    backends = _document_edit_backends()
    changed = False
    for path in iterator:
        key = _document_backend_path_key(path)
        if key is not None and key in backends:
            del backends[key]
            changed = True
    if changed:
        _save_document_edit_backends(backends)


def restore_geometry(window):
    settings = QSettings(_ORG, _APP)
    geometry = settings.value("geometry")
    if geometry:
        window.restoreGeometry(geometry)
    else:
        window.resize(1200, 750)


def restore_file_tree_state(window):
    """Re-open the file tree the way it looked last session."""
    raw = QSettings(_ORG, _APP).value("file_tree_state")
    if not raw:
        return
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return
    if isinstance(state, dict):
        window._panel.file_browser.restore_tree_state(state)


def restore_last_session(window):
    restore_file_tree_state(window)
    settings = QSettings(_ORG, _APP)
    raw = settings.value("open_tabs")
    paths = []
    if raw:
        try:
            paths = json.loads(raw)
        except (ValueError, TypeError):
            paths = []
    recovery_store = getattr(window, "_recovery_store", None)

    def available(path) -> bool:
        if not path or not is_supported_document(path):
            return False
        if Path(path).exists():
            return True
        if recovery_store is None:
            return False
        try:
            return recovery_store.load(path) is not None
        except OSError:
            return False

    paths = [p for p in paths if available(p)]
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
    if available(last):
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
    if EDIT_BACKEND_KEY in r:
        # Only the *default* for tabs that have not chosen a backend of
        # their own; open tabs keep whatever they are already showing.
        window._edit_backend = normalize_backend(r[EDIT_BACKEND_KEY])
    if PREVIEW_DOUBLE_CLICK_KEY in r:
        window._preview_double_click = normalize_preview_double_click(
            r[PREVIEW_DOUBLE_CLICK_KEY]
        )
        if not window._edit_mode:
            window._renderer.set_preview_double_click_mode(
                window._preview_double_click
            )
    window._panel.file_browser.refresh_libraries()
    window._refresh_link_index(force=True)


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


def apply_zoom(
    window,
    factor: float,
    *,
    sync_pdf: bool = True,
    sync_wysiwyg: bool = True,
):
    window._content_zoom = window._renderer.set_zoom(clamp_zoom_factor(factor))
    window._edit_preview.set_zoom(window._content_zoom)
    if sync_pdf and window._current_kind == "pdf":
        window._pdf_view.set_zoom_factor(window._content_zoom)
    if sync_wysiwyg and window._wysiwyg_view is not None:
        window._wysiwyg_view.page().setZoomFactor(window._content_zoom)
    QSettings(_ORG, _APP).setValue("content_zoom", window._content_zoom)
    window.statusBar().showMessage(
        f"縮放：{round(window._content_zoom * 100)}%", 2000
    )


def close_event(window, event) -> bool:
    confirm = getattr(window, "_confirm_close_all_edits", None)
    safe_to_close = confirm() if callable(confirm) else window._confirm_discard_edits()
    if not safe_to_close:
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
        tree_state = window._panel.file_browser.tree_state()
        if isinstance(tree_state, dict):
            settings.setValue("file_tree_state", json.dumps(tree_state))
    return True
