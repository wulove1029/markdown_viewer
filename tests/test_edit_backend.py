"""Unit tests for the pure edit-backend selection logic."""

from app.edit_backend import (
    DEFAULT_BACKEND,
    PREVIEW_DOUBLE_CLICK_DEFAULT,
    PREVIEW_DOUBLE_CLICK_INLINE,
    PREVIEW_DOUBLE_CLICK_SETTINGS_KEY,
    PREVIEW_DOUBLE_CLICK_WYSIWYG,
    SETTINGS_KEY,
    SOURCE_BACKEND,
    SPLIT_BACKEND,
    WYSIWYG_BACKEND,
    backend_allows,
    normalize_backend,
    normalize_preview_double_click,
    preview_double_click_enters_wysiwyg,
    toggle_backend,
)


def test_default_backend_is_original_markdown_source():
    assert DEFAULT_BACKEND == SOURCE_BACKEND == SPLIT_BACKEND
    assert SETTINGS_KEY == "edit_backend"


def test_normalize_coerces_unknown_values_to_source():
    assert normalize_backend(SPLIT_BACKEND) == SPLIT_BACKEND
    assert normalize_backend(WYSIWYG_BACKEND) == WYSIWYG_BACKEND
    assert normalize_backend("") == SOURCE_BACKEND
    assert normalize_backend(None) == SOURCE_BACKEND
    assert normalize_backend("bogus") == SOURCE_BACKEND


def test_toggle_backend_flips_between_split_and_wysiwyg():
    assert toggle_backend(SPLIT_BACKEND) == WYSIWYG_BACKEND
    assert toggle_backend(WYSIWYG_BACKEND) == SPLIT_BACKEND
    # Unknown values normalize to the safe source default first, then toggle.
    assert toggle_backend("bogus") == WYSIWYG_BACKEND
    assert toggle_backend(None) == WYSIWYG_BACKEND


def test_backend_allows_preserves_wysiwyg_for_markdown():
    assert backend_allows(WYSIWYG_BACKEND, ".md") == WYSIWYG_BACKEND
    assert backend_allows(SPLIT_BACKEND, ".md") == SPLIT_BACKEND
    assert backend_allows(WYSIWYG_BACKEND, ".markdown") == WYSIWYG_BACKEND


def test_backend_allows_forces_split_for_txt_suffix():
    assert backend_allows(WYSIWYG_BACKEND, ".txt") == SPLIT_BACKEND
    assert backend_allows(WYSIWYG_BACKEND, ".TXT") == SPLIT_BACKEND
    assert backend_allows(WYSIWYG_BACKEND, ".txt", is_plain_text=True) == SPLIT_BACKEND


def test_backend_allows_forces_split_for_plain_text_flag_regardless_of_suffix():
    assert backend_allows(WYSIWYG_BACKEND, ".md", is_plain_text=True) == SPLIT_BACKEND


def test_backend_allows_normalizes_unknown_backend_first():
    assert backend_allows("bogus", ".md") == SOURCE_BACKEND
    assert backend_allows(None, ".md") == SOURCE_BACKEND


def test_default_source_backend_and_txt_use_the_same_plain_editor():
    assert normalize_backend(None) == SOURCE_BACKEND
    assert backend_allows(DEFAULT_BACKEND, ".txt") == SPLIT_BACKEND
    assert backend_allows(DEFAULT_BACKEND, ".TXT") == SPLIT_BACKEND
    assert backend_allows(DEFAULT_BACKEND, ".md", is_plain_text=True) == SPLIT_BACKEND
    assert backend_allows(DEFAULT_BACKEND, ".md") == SOURCE_BACKEND


def test_backend_allows_handles_missing_suffix():
    assert backend_allows(WYSIWYG_BACKEND, None) == WYSIWYG_BACKEND
    assert backend_allows(WYSIWYG_BACKEND, "") == WYSIWYG_BACKEND


# ---------------------------------------------------------------------------
# v2: preview_double_click preference + routing decision.

def test_preview_double_click_default_keeps_original_selection_behavior():
    assert PREVIEW_DOUBLE_CLICK_DEFAULT == PREVIEW_DOUBLE_CLICK_INLINE
    assert PREVIEW_DOUBLE_CLICK_SETTINGS_KEY == "preview_double_click"


def test_normalize_preview_double_click_coerces_unknown_to_inline():
    assert normalize_preview_double_click(PREVIEW_DOUBLE_CLICK_WYSIWYG) == (
        PREVIEW_DOUBLE_CLICK_WYSIWYG
    )
    assert normalize_preview_double_click(PREVIEW_DOUBLE_CLICK_INLINE) == (
        PREVIEW_DOUBLE_CLICK_INLINE
    )
    assert normalize_preview_double_click("") == PREVIEW_DOUBLE_CLICK_INLINE
    assert normalize_preview_double_click(None) == PREVIEW_DOUBLE_CLICK_INLINE
    assert normalize_preview_double_click("bogus") == PREVIEW_DOUBLE_CLICK_INLINE


def test_preview_double_click_enters_wysiwyg_only_with_explicit_pref():
    assert preview_double_click_enters_wysiwyg(
        PREVIEW_DOUBLE_CLICK_WYSIWYG, is_markdown=True
    )
    assert not preview_double_click_enters_wysiwyg(None, is_markdown=True)


def test_preview_double_click_enters_wysiwyg_false_for_inline_pref():
    assert not preview_double_click_enters_wysiwyg(
        PREVIEW_DOUBLE_CLICK_INLINE, is_markdown=True
    )


def test_preview_double_click_enters_wysiwyg_false_for_non_markdown():
    # .txt / PDF: never routed to WYSIWYG regardless of the preference.
    assert not preview_double_click_enters_wysiwyg(
        PREVIEW_DOUBLE_CLICK_WYSIWYG, is_markdown=False
    )
    assert not preview_double_click_enters_wysiwyg(
        PREVIEW_DOUBLE_CLICK_INLINE, is_markdown=False
    )
