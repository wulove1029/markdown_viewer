"""Pure state logic for the edit-mode "backend": split editor vs. WYSIWYG.

Kept free of Qt imports (mirrors ``view_mode.py``) so the backend selection
and file-type gating rules can be unit-tested headless. ``view_mode.py``'s
three-state PREVIEW/EDIT/SPLIT machine is unchanged; this module only decides
*which widget* renders the EDIT/SPLIT states of a Markdown document.
"""

from __future__ import annotations

SPLIT_BACKEND = "split"
WYSIWYG_BACKEND = "wysiwyg"
BACKENDS = (SPLIT_BACKEND, WYSIWYG_BACKEND)

# Public UI name for the existing QPlainTextEdit backend.  Keep
# ``SPLIT_BACKEND`` as the stored value for backwards compatibility: the same
# backend renders both the source-only EDIT view and the source + preview
# SPLIT view.
SOURCE_BACKEND = SPLIT_BACKEND

# QSettings key + default, shared by window.py and settings_dialog.py.
SETTINGS_KEY = "edit_backend"
DEFAULT_BACKEND = SOURCE_BACKEND


def normalize_backend(value: str | None) -> str:
    """Coerce unknown/empty values to the safe default (split)."""
    return value if value in BACKENDS else DEFAULT_BACKEND


def toggle_backend(value: str | None) -> str:
    """Return the other backend for compatibility with older callers."""
    return SPLIT_BACKEND if normalize_backend(value) == WYSIWYG_BACKEND else WYSIWYG_BACKEND


def backend_allows(backend: str, path_suffix: str | None, *, is_plain_text: bool = False) -> str:
    """Return the *effective* backend for a file, forcing split where required.

    ``.txt`` files and anything else classified as plain text have no
    Markdown structure for Vditor to render WYSIWYG, so they always use the
    split (plain QPlainTextEdit) backend regardless of the user's preference
    or a per-tab override.
    """
    backend = normalize_backend(backend)
    if is_plain_text:
        return SPLIT_BACKEND
    suffix = (path_suffix or "").lower()
    if suffix == ".txt":
        return SPLIT_BACKEND
    return backend


# ---------------------------------------------------------------------------
# A PREVIEW-mode double-click can optionally jump straight into the Office
# editor, mirroring VS Code's Office Viewer extension. This remains separate
# from ``edit_backend``, which is the default used by new-note creation flows;
# explicit source/Office commands always take precedence over both settings.

PREVIEW_DOUBLE_CLICK_WYSIWYG = "wysiwyg"
PREVIEW_DOUBLE_CLICK_INLINE = "inline"
PREVIEW_DOUBLE_CLICK_VALUES = (PREVIEW_DOUBLE_CLICK_WYSIWYG, PREVIEW_DOUBLE_CLICK_INLINE)

# QSettings key + default, shared by window.py and settings_dialog.py.
PREVIEW_DOUBLE_CLICK_SETTINGS_KEY = "preview_double_click"
PREVIEW_DOUBLE_CLICK_DEFAULT = PREVIEW_DOUBLE_CLICK_INLINE


def normalize_preview_double_click(value: str | None) -> str:
    """Coerce unknown/empty values to the safe original preview behaviour."""
    return value if value in PREVIEW_DOUBLE_CLICK_VALUES else PREVIEW_DOUBLE_CLICK_DEFAULT


def preview_double_click_enters_wysiwyg(value: str | None, *, is_markdown: bool) -> bool:
    """Route a PREVIEW double-click.

    True only for Markdown documents when the preference is
    ``PREVIEW_DOUBLE_CLICK_WYSIWYG``: a double-click should then jump straight
    into EDIT mode with the WYSIWYG backend forced on. Everything else --
    ``.txt``/PDF (``is_markdown=False``), or the ``"inline"`` preference that
    keeps the v1 double-click behaviour (free for the browser's native
    word-select, same as it always was) -- leaves the double-click alone.
    """
    return bool(is_markdown) and normalize_preview_double_click(value) == PREVIEW_DOUBLE_CLICK_WYSIWYG
