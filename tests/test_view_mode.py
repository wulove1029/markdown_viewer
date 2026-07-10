"""Unit tests for the pure view-mode state machine and scroll-sync mapping."""

from app.view_mode import (
    EDIT,
    PREVIEW,
    SPLIT,
    ScrollSyncGuard,
    cycle_mode,
    editor_scroll_ratio,
    is_editing,
    normalize,
    toggle_edit,
    toggle_split,
)


# --- mode state machine ---
def test_cycle_walks_preview_edit_split_and_wraps():
    assert cycle_mode(PREVIEW) == EDIT
    assert cycle_mode(EDIT) == SPLIT
    assert cycle_mode(SPLIT) == PREVIEW


def test_cycle_treats_unknown_mode_as_preview():
    assert cycle_mode("bogus") == EDIT
    assert cycle_mode(None) == EDIT


def test_toggle_edit_is_preview_edit_toggle():
    assert toggle_edit(PREVIEW) == EDIT
    assert toggle_edit(EDIT) == PREVIEW
    # From split, Ctrl+E means "back to preview" (leaving the editor).
    assert toggle_edit(SPLIT) == PREVIEW


def test_toggle_split_jumps_to_split_from_anywhere_and_back():
    assert toggle_split(PREVIEW) == SPLIT
    assert toggle_split(EDIT) == SPLIT
    assert toggle_split(SPLIT) == PREVIEW
    assert toggle_split("bogus") == SPLIT


def test_normalize_coerces_unknown_values_to_preview():
    assert normalize(PREVIEW) == PREVIEW
    assert normalize(EDIT) == EDIT
    assert normalize(SPLIT) == SPLIT
    assert normalize("") == PREVIEW
    assert normalize(None) == PREVIEW


def test_is_editing_true_for_edit_and_split_only():
    assert is_editing(EDIT) is True
    assert is_editing(SPLIT) is True
    assert is_editing(PREVIEW) is False
    assert is_editing("bogus") is False


# --- scroll-sync mapping ---
def test_scroll_ratio_degenerate_ranges_map_to_top():
    assert editor_scroll_ratio(0, 0) == 0.0
    assert editor_scroll_ratio(5, 0) == 0.0
    assert editor_scroll_ratio(3, -1) == 0.0
    assert editor_scroll_ratio(3, None) == 0.0


def test_scroll_ratio_maps_linearly_and_clamps():
    assert editor_scroll_ratio(0, 100) == 0.0
    assert editor_scroll_ratio(50, 100) == 0.5
    assert editor_scroll_ratio(100, 100) == 1.0
    assert editor_scroll_ratio(150, 100) == 1.0  # over-scroll clamps
    assert editor_scroll_ratio(-5, 100) == 0.0  # negative clamps


# --- anti-echo direction lock ---
def _guard_with_clock():
    now = [0.0]
    guard = ScrollSyncGuard(cooldown=0.15, clock=lambda: now[0])
    return guard, now


def test_guard_blocks_the_other_source_during_cooldown():
    guard, now = _guard_with_clock()
    assert guard.try_acquire("editor") is True
    assert guard.try_acquire("preview") is False
    now[0] = 0.1  # still inside the cooldown window
    assert guard.try_acquire("preview") is False


def test_guard_releases_after_cooldown():
    guard, now = _guard_with_clock()
    assert guard.try_acquire("editor") is True
    now[0] = 0.2  # past the cooldown
    assert guard.try_acquire("preview") is True
    # Ownership flipped: now the editor is the one being suppressed.
    assert guard.try_acquire("editor") is False


def test_guard_same_source_keeps_reacquiring():
    guard, now = _guard_with_clock()
    assert guard.try_acquire("editor") is True
    now[0] = 0.05
    assert guard.try_acquire("editor") is True  # extends its own lock
    assert guard.try_acquire("preview") is False
