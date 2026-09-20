"""Session persistence helpers delegated from MainWindow."""

import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from .content_zoom import ZOOM_FACTORS, clamp_zoom_factor
from .edit_backend import (
    PREVIEW_DOUBLE_CLICK_SETTINGS_KEY as PREVIEW_DOUBLE_CLICK_KEY,
)
from .edit_backend import (
    SETTINGS_KEY as EDIT_BACKEND_KEY,
)
from .edit_backend import (
    SPLIT_BACKEND,
    WYSIWYG_BACKEND,
    normalize_backend,
    normalize_preview_double_click,
)
from .file_types import document_kind, is_markdown, is_supported_document
from .md_converter import set_user_css
from .reading_style import SPACING_KEY, WIDTH_KEY, reading_css
from .settings_dialog import SettingsDialog
from .settings_store import APP as _APP
from .settings_store import ORG as _ORG

# Text content (Markdown / plain text / Office) and PDF zoom are remembered
# separately: text zoom scales the font, PDF zoom scales the page, so a PDF
# shrunk to fit the window must not shrink every Markdown file afterwards.
CONTENT_ZOOM_KEY = "content_zoom"
PDF_ZOOM_KEY = "pdf_zoom"

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


def _session_path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def restore_last_session(window, file_arg: str = ""):
    """Restore all tabs, loading only the requested or previously active file."""
    was_restoring = getattr(window, "_restoring_session", False)
    window._restoring_session = True
    timer = getattr(window, "_session_save_timer", None)
    if timer is not None:
        timer.stop()
    try:
        _restore_session_documents(window, file_arg)
    finally:
        window._restoring_session = was_restoring
    if not was_restoring:
        schedule = getattr(window, "_schedule_session_save", None)
        if callable(schedule):
            schedule()
    show_pending_recovery(window)


def _restore_session_documents(window, file_arg: str):
    restore_file_tree_state(window)
    settings = QSettings(_ORG, _APP)
    raw = settings.value("open_tabs")
    paths = None
    if raw:
        try:
            paths = json.loads(raw)
        except (ValueError, TypeError):
            paths = None
    recovery_store = getattr(window, "_recovery_store", None)

    def available(path) -> bool:
        if not isinstance(path, str) or not path or not is_supported_document(path):
            return False
        try:
            if Path(path).is_file():
                return True
            if recovery_store is None:
                return False
            return recovery_store.load(path) is not None
        except (OSError, ValueError):
            return False

    # An explicit [] means the user closed every tab. Only absent/malformed
    # tab lists may fall back to the single-file setting from old releases.
    if not isinstance(paths, list):
        last = settings.value("last_file")
        paths = [last] if available(last) else []
    try:
        active = int(settings.value("active_tab", 0))
    except (ValueError, TypeError):
        active = 0
    active = max(0, min(active, len(paths) - 1))
    preferred = settings.value("active_tab_path")
    if not isinstance(preferred, str) or preferred not in paths:
        preferred = paths[active] if paths else None
    # Resolve identity before filtering missing files, so deleting an earlier
    # tab does not silently activate the following document instead.
    known = {
        _session_path_key(p): p
        for i in range(window.tab_bar.count())
        if isinstance(p := window.tab_bar.tabData(i), str) and p
    }
    for path in paths:
        if not available(path):
            continue
        key = _session_path_key(path)
        if key not in known:
            window._add_tab(Path(path), document_kind(Path(path)))
            known[key] = path
    if file_arg:
        # A file-manager launch selects its file while keeping the old tabs.
        # Reuse saved path spelling for case-insensitive Windows duplicates.
        window.open_path(known.get(_session_path_key(file_arg), file_arg))
        return
    if window.tab_bar.count():
        restored = [window.tab_bar.tabData(i) for i in range(window.tab_bar.count())]
        preferred = known.get(_session_path_key(preferred)) if isinstance(preferred, str) and preferred else None
        active = restored.index(preferred) if preferred in restored else 0
        window._tab_guard = True
        window.tab_bar.setCurrentIndex(active)
        window._tab_guard = False
        window._activate_tab(active)


def restore_startup(window, file_arg: str = ""):
    """A CLI file takes focus without replacing the previous workspace."""
    restore_last_session(window, file_arg)


def save_open_tabs(window, settings=None) -> None:
    """Checkpoint tab identity/order without serializing editor buffers."""
    if window._is_detached or getattr(window, "_restoring_session", False):
        return
    settings = settings if settings is not None else QSettings(_ORG, _APP)
    paths = [
        p for i in range(window.tab_bar.count())
        if isinstance(p := window.tab_bar.tabData(i), str) and p
    ]
    active_path = window._active_path
    if active_path not in paths:
        active_path = window.tab_bar.tabData(window.tab_bar.currentIndex())
    active = paths.index(active_path) if active_path in paths else -1
    values = {
        "open_tabs": json.dumps(paths, ensure_ascii=False),
        "active_tab": active,
        "active_tab_path": active_path or "",
    }
    for key, value in values.items():
        if settings.value(key) != value:
            settings.setValue(key, value)
    if active_path:
        settings.setValue("last_file", active_path)
    else:
        settings.remove("last_file")
    # Persist checkpoints even when no closeEvent arrives (e.g. a crash).
    settings.sync()
    if settings.status() != settings.Status.NoError:
        window.statusBar().showMessage("無法儲存開啟分頁紀錄，請檢查設定檔的寫入權限。", 6000)


def show_pending_recovery(window, *, notify_empty: bool = False):
    """Show one non-modal recovery inbox, reusable after choosing Later."""
    from .recovery_browser import RecoveryBrowser, pending_recovery_snapshots

    dialog = getattr(window, "_recovery_browser", None)
    snapshots = pending_recovery_snapshots(window)
    if not snapshots:
        if dialog is not None:
            dialog.close()
        if notify_empty:
            window.statusBar().showMessage("目前沒有待復原草稿。", 5000)
        return None
    if dialog is None:
        dialog = RecoveryBrowser(window, snapshots)
        window._recovery_browser = dialog
    else:
        dialog.refresh(snapshots)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


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
    if not window.current_file:
        return
    pages = pdf_pages_map()
    pages[str(window.current_file)] = int(page0)
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
        state["scroll"] = window.renderer.scroll_y()


def load_user_css(window, reload: bool = False):
    path = QSettings(_ORG, _APP).value("custom_css_path", "") or ""
    css = ""
    if path:
        try:
            css = Path(path).read_text(encoding="utf-8")
        except OSError:
            css = ""
    settings = QSettings(_ORG, _APP)
    set_user_css(reading_css(settings.value(WIDTH_KEY, "comfortable"),
                             settings.value(SPACING_KEY, "comfortable")) + "\n" + css)
    if (
        reload
        and window.current_file
        and is_markdown(window.current_file)
        and not window.edit_mode
    ):
        window.renderer.reload_current()
    elif (reload and window.current_file and is_markdown(window.current_file)
          and window.edit_mode and getattr(window, "_view_mode", "") == "split"
          and getattr(window, "_active_edit_backend", "") != WYSIWYG_BACKEND):
        window._update_preview()


def open_preferences(window):
    dialog = SettingsDialog(
        window,
        current_theme=window.theme_name,
        current_zoom=window.content_zoom,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    r = dialog.results
    window._apply_content_zoom(r["content_zoom"])
    new_theme = r.get("theme", window.theme_name)
    if new_theme != window.theme_name:
        window.theme_name = new_theme
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
        if not window.edit_mode:
            window.renderer.set_preview_double_click_mode(
                window._preview_double_click
            )
    window._panel.file_browser.refresh_libraries()
    window._refresh_link_index(force=True)


def toggle_theme(window):
    window.theme_name = "light" if window.theme_name == "dark" else "dark"
    QSettings(_ORG, _APP).setValue("theme", window.theme_name)
    window._apply_theme()


def toggle_annotation_side_notes(window, checked=None):
    window._side_notes_visible = (
        bool(checked) if checked is not None else window._side_notes_btn.isChecked()
    )
    QSettings(_ORG, _APP).setValue(
        "annotation_side_notes_visible", window._side_notes_visible
    )
    window.renderer.set_annotation_side_notes_visible(window._side_notes_visible)
    window._refresh_icons()


def _is_zoom_stop(value: float) -> bool:
    return any(abs(value - stop) < 1e-6 for stop in ZOOM_FACTORS)


def load_zoom_preferences(settings: QSettings) -> tuple[float, float]:
    """Return ``(content_zoom, pdf_zoom)``, migrating the old shared value.

    Before PDF zoom had its own key, a PDF Ctrl+wheel zoom was written into
    ``content_zoom`` as a continuous factor and then applied to every Markdown
    file. Such a leaked value never matches a keyboard/preference stop, so on
    first run with this version it moves over to the PDF key and text content
    goes back to 100%.
    """
    content = clamp_zoom_factor(settings.value(CONTENT_ZOOM_KEY, 1.0) or 1.0)
    if settings.contains(PDF_ZOOM_KEY):
        pdf = clamp_zoom_factor(settings.value(PDF_ZOOM_KEY, 1.0) or 1.0)
    elif _is_zoom_stop(content):
        pdf = content
    else:
        pdf, content = content, 1.0
        settings.setValue(CONTENT_ZOOM_KEY, content)
        settings.setValue(PDF_ZOOM_KEY, pdf)
    return content, pdf


def apply_zoom(
    window,
    factor: float,
    *,
    sync_wysiwyg: bool = True,
):
    """Apply and persist the text content zoom (Markdown / text / Office)."""
    window.content_zoom = window.renderer.set_zoom(clamp_zoom_factor(factor))
    if window._edit_preview is not None:
        window._edit_preview.set_zoom(window.content_zoom)
    if sync_wysiwyg and window.wysiwyg_view is not None:
        window.wysiwyg_view.page().setZoomFactor(window.content_zoom)
    QSettings(_ORG, _APP).setValue(CONTENT_ZOOM_KEY, window.content_zoom)
    window.statusBar().showMessage(
        f"縮放：{round(window.content_zoom * 100)}%", 2000
    )


def apply_pdf_zoom(window, factor: float, *, sync_view: bool = True):
    """Apply and persist the PDF zoom without touching text content zoom."""
    window._pdf_zoom = clamp_zoom_factor(factor)
    if sync_view:
        window._pdf_view.set_zoom_factor(window._pdf_zoom)
    QSettings(_ORG, _APP).setValue(PDF_ZOOM_KEY, window._pdf_zoom)
    window.statusBar().showMessage(
        f"縮放：{round(window._pdf_zoom * 100)}%", 2000
    )


def close_event(window, event) -> bool:
    confirm = getattr(window, "_confirm_close_all_edits", None)
    safe_to_close = confirm() if callable(confirm) else window._confirm_discard_edits()
    if not safe_to_close:
        event.ignore()
        return False
    save_active_view_state(window)
    timer = getattr(window, "_session_save_timer", None)
    if timer is not None:
        timer.stop()
    window._session_closing = True
    if not window._is_detached:
        settings = QSettings(_ORG, _APP)
        settings.setValue("geometry", window.saveGeometry())
        tree_state = window._panel.file_browser.tree_state()
        if isinstance(tree_state, dict):
            settings.setValue("file_tree_state", json.dumps(tree_state))
        save_open_tabs(window, settings)
    return True
