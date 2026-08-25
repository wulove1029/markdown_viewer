"""Pure tests for Markdown smart Tab and delimiter pairing plans."""

import pytest

from app.editor_input import auto_pair_edit, backspace_pair_edit, tab_edit


def _apply(text, edit):
    updated = text[: edit.start] + edit.replacement + text[edit.end :]
    return updated, (edit.sel_start, edit.sel_end)


def _round_trip_undo(text, edit):
    """Model the inverse of the module's single replacement."""

    updated, _selection = _apply(text, edit)
    old_segment = text[edit.start : edit.end]
    restored = (
        updated[: edit.start]
        + old_segment
        + updated[edit.start + len(edit.replacement) :]
    )
    return updated, restored


def test_tab_indents_current_line_and_keeps_unicode_caret_coordinates():
    text = "😀alpha"
    edit = tab_edit(text, 1, 1, reverse=False, enabled=True)

    assert edit is not None
    assert _apply(text, edit) == ("    😀alpha", (5, 5))
    assert (edit.start, edit.end) == (0, len(text))


def test_tab_indents_selected_lines_in_one_edit_and_skips_internal_blank():
    text = "one\n\n😀two\nlast"
    selected_end = text.index("\nlast")
    edit = tab_edit(text, 1, selected_end, reverse=False, enabled=True)

    assert edit is not None
    expected_block = "    one\n\n    😀two"
    assert edit.start == 0
    assert edit.end == selected_end
    assert edit.replacement == expected_block
    assert _apply(text, edit) == (
        expected_block + "\nlast",
        (0, len(expected_block)),
    )
    updated, restored = _round_trip_undo(text, edit)
    assert updated == expected_block + "\nlast"
    assert restored == text


def test_tab_selection_ending_at_next_line_start_does_not_touch_that_line():
    text = "one\ntwo"
    edit = tab_edit(text, 0, 4, reverse=False, enabled=True)

    assert edit is not None
    assert _apply(text, edit)[0] == "    one\ntwo"


def test_tab_on_current_empty_line_inserts_four_spaces():
    edit = tab_edit("", 0, 0, reverse=False, enabled=True)

    assert edit is not None
    assert _apply("", edit) == ("    ", (4, 4))


def test_shift_tab_outdents_mixed_prefixes_as_one_replacement():
    text = "    one\n  two\n\tthree\nfour"
    edit = tab_edit(text, 0, len(text), reverse=True, enabled=True)

    assert edit is not None
    assert _apply(text, edit) == (
        "one\ntwo\nthree\nfour",
        (0, len("one\ntwo\nthree\nfour")),
    )
    assert _round_trip_undo(text, edit)[1] == text


def test_shift_tab_current_line_clamps_caret_inside_removed_indent():
    text = "    item"
    edit = tab_edit(text, 2, 2, reverse=True, enabled=True)

    assert edit is not None
    assert _apply(text, edit) == ("item", (0, 0))


def test_shift_tab_without_indent_has_no_smart_edit():
    assert tab_edit("item", 2, 2, reverse=True, enabled=True) is None


@pytest.mark.parametrize("reverse", (False, True))
def test_txt_policy_keeps_native_tab_behavior(reverse):
    assert tab_edit("plain", 2, 2, reverse=reverse, enabled=False) is None


@pytest.mark.parametrize(
    ("typed", "expected"),
    (
        ("(", "()"),
        ("[", "[]"),
        ("{", "{}"),
        ('"', '""'),
        ("'", "''"),
        ("`", "``"),
    ),
)
def test_auto_pair_inserts_pair_and_places_caret_between(typed, expected):
    edit = auto_pair_edit("", 0, 0, typed, enabled=True)

    assert edit is not None
    assert _apply("", edit) == (expected, (1, 1))


@pytest.mark.parametrize("typed", ("(", "[", "{", '"', "'", "`"))
def test_auto_pair_wraps_unicode_multiline_selection(typed):
    closing = {"(": ")", "[": "]", "{": "}"}.get(typed, typed)
    text = "😀\n文字"
    edit = auto_pair_edit(text, 0, len(text), typed, enabled=True)

    assert edit is not None
    assert _apply(text, edit) == (
        typed + text + closing,
        (1, 1 + len(text)),
    )
    assert _round_trip_undo(text, edit)[1] == text


@pytest.mark.parametrize("closing", (")", "]", "}", '"', "'", "`"))
def test_typing_existing_closer_skips_it_without_changing_text(closing):
    text = "x" + closing + "y"
    edit = auto_pair_edit(text, 1, 1, closing, enabled=True)

    assert edit is not None
    assert _apply(text, edit) == (text, (2, 2))
    assert edit.start == edit.end == 1
    assert edit.replacement == ""


@pytest.mark.parametrize("closing", (")", "]", "}"))
def test_unmatched_closer_is_left_to_native_input(closing):
    assert auto_pair_edit("text", 4, 4, closing, enabled=True) is None


def test_apostrophe_after_word_is_left_native_but_selection_can_be_wrapped():
    assert auto_pair_edit("dont", 3, 3, "'", enabled=True) is None
    edit = auto_pair_edit("word", 0, 4, "'", enabled=True)
    assert _apply("word", edit)[0] == "'word'"


@pytest.mark.parametrize("typed", ("(", '"', "`"))
def test_conservative_disabled_pairing_leaves_txt_and_fence_input_native(typed):
    assert auto_pair_edit("", 0, 0, typed, enabled=False) is None


@pytest.mark.parametrize("pair", ("()", "[]", "{}", '""', "''", "``"))
def test_backspace_deletes_both_characters_of_empty_pair(pair):
    text = "😀" + pair + "tail"
    edit = backspace_pair_edit(text, 2, enabled=True)

    assert edit is not None
    assert _apply(text, edit) == ("😀tail", (1, 1))
    assert _round_trip_undo(text, edit)[1] == text


def test_backspace_nonempty_or_disabled_pair_uses_native_behavior():
    assert backspace_pair_edit("(x)", 1, enabled=True) is None
    assert backspace_pair_edit("()", 1, enabled=False) is None


def test_unknown_or_multi_character_input_has_no_pair_plan():
    assert auto_pair_edit("", 0, 0, "/", enabled=True) is None
    assert auto_pair_edit("", 0, 0, "()", enabled=True) is None
