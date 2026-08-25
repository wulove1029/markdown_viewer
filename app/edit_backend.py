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

# QSettings key + default, shared by window.py and settings_dialog.py.
SETTINGS_KEY = "edit_backend"
DEFAULT_BACKEND = WYSIWYG_BACKEND


def normalize_backend(value: str | None) -> str:
    """Coerce unknown/empty values to the safe default (split)."""
    return value if value in BACKENDS else DEFAULT_BACKEND


def toggle_backend(value: str | None) -> str:
    """Toolbar button / Ctrl+Shift+W: split <-> wysiwyg."""
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
# v2: "click to edit" -- a PREVIEW-mode double-click can jump straight into
# EDIT mode with the WYSIWYG backend forced on, mirroring VSCode's Office
# Viewer extension. This is a separate, opt-out-able preference from
# ``edit_backend`` above (which only governs the *default* backend once you
# are already in EDIT mode): a user can prefer the split backend by default
# yet still want double-click-to-WYSIWYG, or vice versa.

PREVIEW_DOUBLE_CLICK_WYSIWYG = "wysiwyg"
PREVIEW_DOUBLE_CLICK_INLINE = "inline"
PREVIEW_DOUBLE_CLICK_VALUES = (PREVIEW_DOUBLE_CLICK_WYSIWYG, PREVIEW_DOUBLE_CLICK_INLINE)

# QSettings key + default, shared by window.py and settings_dialog.py.
PREVIEW_DOUBLE_CLICK_SETTINGS_KEY = "preview_double_click"
PREVIEW_DOUBLE_CLICK_DEFAULT = PREVIEW_DOUBLE_CLICK_WYSIWYG


def normalize_preview_double_click(value: str | None) -> str:
    """Coerce unknown/empty values to the new default ("wysiwyg")."""
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
