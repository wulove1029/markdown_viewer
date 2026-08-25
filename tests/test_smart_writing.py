"""Pure tests for cursor-local Markdown writing conveniences."""

import pytest

from app.format_actions import active_format_actions
from app.format_commands import command_for, filter_commands
from app.smart_writing import (
    active_slash_query,
    fence_state_after_line,
    linkify_paste_edit,
    smart_enter_edit,
)
from app.text_positions import py_to_qt_position, qt_to_py_position


def _apply(text, edit):
    return text[: edit.start] + edit.replacement + text[edit.end :]


def test_format_command_metadata_is_shared_across_surfaces():
    link = command_for("link")
    assert link.shortcut == "Ctrl+K"
    assert set(link.surfaces) == {"toolbar", "slash", "selection"}
    assert filter_commands("slash", "table")[0].action_id == "table"
    assert filter_commands("slash", "表格")[0].action_id == "table"
    assert filter_commands("slash", "流程圖")[0].action_id == "mermaid"


def test_slash_query_requires_first_non_space_text_and_skips_fences():
    assert active_slash_query("/table", 6).query == "table"
    assert active_slash_query("  /表格", 5).start == 0
    assert active_slash_query("text /table", 11) is None
    assert active_slash_query("[[/", 3) is None
    fenced = "```python\n/table"
    assert active_slash_query(fenced, len(fenced)) is None
    closed = "```\n```\n/table"
    assert active_slash_query(closed, len(closed)).query == "table"


def test_fence_closer_requires_only_trailing_space_and_at_most_three_indent():
    false_close = "```python\n```not-close\n- code"
    assert smart_enter_edit(false_close, len(false_close)) is None
    false_tilde_close = "~~~lang\n~~~not-close\n1. code"
    assert smart_enter_edit(false_tilde_close, len(false_tilde_close)) is None
    four_space_fence = "    ```\n- item"
    edit = smart_enter_edit(four_space_fence, len(four_space_fence))
    assert edit is not None
    assert _apply(four_space_fence, edit).endswith("- item\n- ")


def test_fence_state_preserves_delimiter_type_and_required_length():
    state = fence_state_after_line("````python")
    assert state > 0
    assert fence_state_after_line("```", state) == state
    assert fence_state_after_line("~~~~", state) == state
    assert fence_state_after_line("````   ", state) == 0
    assert fence_state_after_line("    ```") == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("- item", "- item\n- "),
        ("* item", "* item\n* "),
        ("  + item", "  + item\n  + "),
        ("7. first", "7. first\n8. "),
        ("7) first", "7) first\n8) "),
        ("- [ ] task", "- [ ] task\n- [ ] "),
        ("- [x] done", "- [x] done\n- [ ] "),
        ("> quote", "> quote\n> "),
    ),
)
def test_smart_enter_continues_markdown_structures(text, expected):
    edit = smart_enter_edit(text, len(text))
    assert edit is not None
    assert _apply(text, edit) == expected


@pytest.mark.parametrize("text", ("- ", "9. ", "- [ ] ", "  * "))
def test_smart_enter_exits_an_empty_item(text):
    edit = smart_enter_edit(text, len(text))
    assert edit is not None
    assert _apply(text, edit) == text[: len(text) - len(text.lstrip())]


def test_smart_enter_ignores_mid_line_plain_text_and_fenced_code():
    assert smart_enter_edit("- item tail", 6) is None
    assert smart_enter_edit("plain", 5) is None
    fenced = "```\n- code"
    assert smart_enter_edit(fenced, len(fenced)) is None


def test_pasted_url_wraps_trimmed_single_line_selection():
    text = " hello "
    edit = linkify_paste_edit(text, 0, len(text), "https://example.com/a")
    assert edit is not None
    assert _apply(text, edit) == " [hello](https://example.com/a) "
    assert edit.sel_start == edit.sel_end


def test_pasted_url_requires_selection_single_line_and_web_scheme():
    assert linkify_paste_edit("hello", 2, 2, "https://example.com") is None
    assert linkify_paste_edit("a\nb", 0, 3, "https://example.com") is None
    assert linkify_paste_edit("hello", 0, 5, "file:///tmp/a") is None
    assert linkify_paste_edit("hello", 0, 5, "not a url") is None


def test_active_format_actions_reports_reliable_line_and_selection_states():
    text = "## **Title**\n- [ ] task"
    heading_active = active_format_actions(text, 5, 10)
    assert {"h2", "bold"} <= heading_active
    assert "italic" not in heading_active
    task_start = text.index("- [ ]") + 6
    assert "task_list" in active_format_actions(text, task_start, task_start)


@pytest.mark.parametrize(
    ("text", "position", "action"),
    (
        ("**bold**", 4, "bold"),
        ("*italic*", 4, "italic"),
        ("~~strike~~", 5, "strikethrough"),
        ("`code`", 3, "inline_code"),
        ("[[Note]]", 4, "wikilink"),
        ("<mark>text</mark>", 8, "highlight"),
        ("  * item", 5, "bullet_list"),
        ("  7) item", 6, "ordered_list"),
        ("  - [ ] task", 9, "task_list"),
        ("  > quote", 6, "quote"),
    ),
)
def test_active_format_actions_at_caret_and_indented_structures(
    text, position, action
):
    assert action in active_format_actions(text, position, position)


@pytest.mark.parametrize(
    ("text", "needle", "action"),
    (
        ("**a** plain **b**", "plain", "bold"),
        ("~~a~~ plain ~~b~~", "plain", "strikethrough"),
        ("`a` plain `b`", "plain", "inline_code"),
        ("[[A]] plain [[B]]", "plain", "wikilink"),
        ("<mark>A</mark> plain <mark>B</mark>", "plain", "highlight"),
    ),
)
def test_active_format_actions_does_not_bridge_separate_pairs(
    text, needle, action
):
    position = text.index(needle) + 2
    assert action not in active_format_actions(text, position, position)


def test_indented_slash_command_consumes_trigger_whitespace():
    from app.format_actions import compute_edit

    text = "  /h1"
    query = active_slash_query(text, len(text))
    without_trigger = text[: query.start] + text[query.end :]
    edit = compute_edit("h1", without_trigger, query.start, query.start)
    assert _apply(without_trigger, edit) == "# "


def test_qt_utf16_and_python_positions_round_trip_astral_characters():
    text = "a😀b"
    assert [py_to_qt_position(text, index) for index in range(4)] == [0, 1, 3, 4]
    assert [qt_to_py_position(text, index) for index in (0, 1, 3, 4)] == [0, 1, 2, 3]
    assert qt_to_py_position(text, 2) == 1  # clamps inside the surrogate pair
