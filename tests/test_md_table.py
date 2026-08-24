"""GFM pipe table model round-tripping (app/md_table.py).

The grid editor reads a table into a dict and writes it straight back, so the
assertions here are mostly about one promise: whatever parse_table produced,
serialize_table must render into something parse_table reads back identically.
"""

import pytest

from app.md_table import display_width, parse_table, serialize_table

# The table that motivated the module, lifted from a real user document.
REAL_WORLD = (
    "| 讀回的前 4 bytes | 代表 |\n"
    "|---|---|\n"
    "| `FF FF FF FF` | **這片板子從未校正過**（韌體實際跑 a=1.000, b=0） |\n"
    "| `E8 03 00 00` | 已校正過，但值等於預設 a=1.000, b=0 |\n"
    "| `1A 04 E2 FF` | a=1.050、b=−30 mA |"
)

SIMPLE = "| a | b |\n| --- | --- |\n| 1 | 2 |"


# --------------------------------------------------------------------------
# display_width
# --------------------------------------------------------------------------


def test_display_width_counts_ascii_as_one_cell_each():
    assert display_width("abc 123") == 7


def test_display_width_counts_cjk_as_two_cells():
    assert display_width("中文字") == 6


def test_display_width_mixes_ascii_and_cjk():
    assert display_width("a中1文") == 6


def test_display_width_counts_emoji_as_two_cells():
    assert display_width("😀") == 2


def test_display_width_ignores_combining_marks():
    # "e" plus a combining acute occupies one cell, not two.
    assert display_width("é") == 1


def test_display_width_of_empty_string_is_zero():
    assert display_width("") == 0


# --------------------------------------------------------------------------
# parse_table: the forms it must accept
# --------------------------------------------------------------------------


def test_parse_standard_form():
    assert parse_table(SIMPLE) == {
        "headers": ["a", "b"],
        "aligns": ["", ""],
        "rows": [["1", "2"]],
        "indent": "",
    }


def test_parse_accepts_rows_without_outer_pipes():
    src = "a | b\n--- | ---\n1 | 2"
    assert parse_table(src) == {
        "headers": ["a", "b"],
        "aligns": ["", ""],
        "rows": [["1", "2"]],
        "indent": "",
    }


def test_parse_accepts_a_mix_of_outer_pipe_styles_within_one_table():
    # Leading-only, trailing-only and bare rows all describe two columns.
    model = parse_table("| a | b\n| --- | ---\n1 | 2 |\n3 | 4")
    assert model["headers"] == ["a", "b"]
    assert model["rows"] == [["1", "2"], ["3", "4"]]


def test_parse_reads_all_four_alignment_markers():
    src = "| a | b | c | d |\n| --- | :--- | :---: | ---: |\n| 1 | 2 | 3 | 4 |"
    assert parse_table(src)["aligns"] == ["", "left", "center", "right"]


def test_parse_accepts_a_single_dash_delimiter():
    assert parse_table("| a |\n| - |\n| 1 |")["aligns"] == [""]
    assert parse_table("| a |\n| :-: |\n| 1 |")["aligns"] == ["center"]


def test_parse_decodes_escaped_pipes_into_literal_pipes():
    src = "| a | b |\n| --- | --- |\n| x \\| y | z |"
    assert parse_table(src)["rows"] == [["x | y", "z"]]


def test_parse_keeps_a_literal_backslash_pair_and_still_splits_after_it():
    # "\\" is a literal backslash, so the pipe behind it is a real delimiter.
    src = "| a | b |\n| --- | --- |\n| x\\\\ | y |"
    assert parse_table(src)["rows"] == [["x\\\\", "y"]]


def test_parse_leaves_other_markdown_escapes_untouched():
    src = "| a | b |\n| --- | --- |\n| \\*not italic\\* | y |"
    assert parse_table(src)["rows"] == [["\\*not italic\\*", "y"]]


def test_parse_pads_short_rows_and_truncates_long_ones():
    src = "| a | b | c |\n| --- | --- | --- |\n| 1 |\n| 1 | 2 | 3 | 4 |"
    assert parse_table(src)["rows"] == [["1", "", ""], ["1", "2", "3"]]


def test_parse_accepts_a_table_with_no_data_rows():
    model = parse_table("| a | b |\n| --- | --- |")
    assert model["headers"] == ["a", "b"]
    assert model["rows"] == []


def test_parse_accepts_up_to_three_spaces_of_indent_and_keeps_it():
    src = "   | a | b |\n   | --- | --- |\n   | 1 | 2 |"
    model = parse_table(src)
    assert model["indent"] == "   "
    assert model["rows"] == [["1", "2"]]


def test_parse_tolerates_trailing_whitespace_on_every_line():
    src = "| a | b |   \n| --- | --- |\t\n| 1 | 2 |  "
    assert parse_table(src)["rows"] == [["1", "2"]]


def test_parse_keeps_empty_cells_as_empty_strings():
    src = "| a |  | c |\n| --- | --- | --- |\n| 1 |  | 3 |"
    model = parse_table(src)
    assert model["headers"] == ["a", "", "c"]
    assert model["rows"] == [["1", "", "3"]]


def test_parse_normalizes_crlf_line_endings():
    assert parse_table("| a | b |\r\n| --- | --- |\r\n| 1 | 2 |") == parse_table(SIMPLE)


def test_parse_ignores_a_trailing_blank_line():
    assert parse_table(SIMPLE + "\n") == parse_table(SIMPLE)


# --------------------------------------------------------------------------
# parse_table: the forms it must refuse
# --------------------------------------------------------------------------


def test_parse_rejects_fewer_than_two_lines():
    assert parse_table("| a | b |") is None
    assert parse_table("") is None


def test_parse_rejects_a_second_line_that_is_not_a_delimiter_row():
    assert parse_table("| a | b |\n| 1 | 2 |\n| 3 | 4 |") is None


def test_parse_rejects_a_delimiter_row_with_a_different_column_count():
    assert parse_table("| a | b |\n| --- |\n| 1 | 2 |") is None
    assert parse_table("| a | b |\n| --- | --- | --- |\n| 1 | 2 |") is None


def test_parse_rejects_a_header_row_without_any_pipe():
    assert parse_table("a b\n| --- |\n| 1 |") is None


def test_parse_rejects_a_header_row_whose_only_pipe_is_escaped():
    assert parse_table("a \\| b\n| --- |\n| 1 |") is None


def test_parse_rejects_junk_characters_in_the_delimiter_row():
    assert parse_table("| a | b |\n| --- | -x- |\n| 1 | 2 |") is None
    assert parse_table("| a | b |\n| --- | --:- |\n| 1 | 2 |") is None
    assert parse_table("| a | b |\n| --- |  |\n| 1 | 2 |") is None


def test_parse_rejects_four_spaces_of_indent_as_a_code_block():
    src = "    | a | b |\n    | --- | --- |\n    | 1 | 2 |"
    assert parse_table(src) is None


def test_parse_rejects_a_data_row_without_any_pipe():
    assert parse_table("| a | b |\n| --- | --- |\n1 2") is None


def test_parse_rejects_a_blank_line_inside_the_block():
    assert parse_table("| a | b |\n| --- | --- |\n\n| 1 | 2 |") is None


# --------------------------------------------------------------------------
# serialize_table
# --------------------------------------------------------------------------


def test_serialize_writes_the_normalized_pipe_form():
    # Outer pipes appear, and every column widens to the 3-cell floor the
    # delimiter row needs so the table still lines up.
    model = parse_table("a | b\n--- | ---\n1 | 2")
    assert serialize_table(model) == "| a   | b   |\n| --- | --- |\n| 1   | 2   |"


def test_serialize_has_no_trailing_newline():
    assert not serialize_table(parse_table(SIMPLE)).endswith("\n")


def test_serialize_pads_every_column_to_the_widest_cell():
    model = {
        "headers": ["a", "bb"],
        "aligns": ["", ""],
        "rows": [["longer", "x"]],
        "indent": "",
    }
    assert serialize_table(model) == (
        "| a      | bb  |\n| ------ | --- |\n| longer | x   |"
    )


def test_serialize_aligns_columns_by_display_width_not_by_len():
    model = {
        "headers": ["名稱", "note"],
        "aligns": ["", ""],
        "rows": [["中文字串", "ok"], ["ascii", "😀 emoji"]],
        "indent": "",
    }
    lines = serialize_table(model).split("\n")
    # Every rendered line must occupy the same number of monospaced cells,
    # which len() would get wrong for both the CJK text and the emoji.
    assert len({display_width(line) for line in lines}) == 1
    assert lines[0] == "| 名稱     | note     |"


def test_serialize_keeps_the_minimum_of_three_dashes_for_narrow_columns():
    model = {"headers": ["a"], "aligns": [""], "rows": [], "indent": ""}
    assert serialize_table(model) == "| a   |\n| --- |"


def test_serialize_writes_each_alignment_marker():
    model = {
        "headers": ["a", "b", "c", "d"],
        "aligns": ["", "left", "center", "right"],
        "rows": [],
        "indent": "",
    }
    assert serialize_table(model).split("\n")[1] == "| --- | :--- | :---: | ---: |"


def test_serialize_widens_alignment_markers_with_the_column():
    model = {
        "headers": ["heading", "heading", "heading"],
        "aligns": ["left", "center", "right"],
        "rows": [],
        "indent": "",
    }
    assert serialize_table(model).split("\n")[1] == (
        "| :------ | :-----: | ------: |"
    )


def test_serialize_never_drops_below_three_dashes_even_with_colons():
    # A one-cell-wide centered column still needs ":---:", not ":-:".
    model = {"headers": ["a"], "aligns": ["center"], "rows": [], "indent": ""}
    assert serialize_table(model) == "| a     |\n| :---: |"


def test_serialize_escapes_literal_pipes_in_cells():
    model = {
        "headers": ["a"],
        "aligns": [""],
        "rows": [["x | y"]],
        "indent": "",
    }
    assert serialize_table(model).split("\n")[2] == "| x \\| y |"


def test_serialize_does_not_inflate_an_existing_backslash_escape():
    model = {"headers": ["a"], "aligns": [""], "rows": [["x\\\\"]], "indent": ""}
    assert serialize_table(model).split("\n")[2] == "| x\\\\ |"


def test_serialize_prefixes_every_line_with_the_indent():
    model = {
        "headers": ["a", "b"],
        "aligns": ["", ""],
        "rows": [["1", "2"]],
        "indent": "  ",
    }
    assert serialize_table(model).split("\n") == [
        "  | a   | b   |",
        "  | --- | --- |",
        "  | 1   | 2   |",
    ]


def test_serialize_returns_empty_string_for_a_table_with_no_headers():
    assert serialize_table({"headers": [], "aligns": [], "rows": [], "indent": ""}) == ""
    assert serialize_table({}) == ""


def test_serialize_fits_ragged_rows_to_the_header_count():
    model = {"headers": ["a", "b"], "aligns": ["", ""], "rows": [["1"]], "indent": ""}
    assert serialize_table(model).split("\n")[2] == "| 1   |     |"


# --------------------------------------------------------------------------
# The invariant the module exists for
# --------------------------------------------------------------------------


ROUND_TRIP_CASES = [
    pytest.param(SIMPLE, id="standard"),
    pytest.param("a | b\n--- | ---\n1 | 2", id="no-outer-pipes"),
    pytest.param(
        "| a | b | c | d |\n| --- | :--- | :---: | ---: |\n| 1 | 2 | 3 | 4 |",
        id="all-alignments",
    ),
    pytest.param("| a | b |\n| --- | --- |", id="header-only"),
    pytest.param("| a |\n| - |\n| 1 |", id="single-column"),
    pytest.param("| a |  | c |\n|---|---|---|\n| 1 |  | 3 |", id="empty-cells"),
    pytest.param("| a | b |\n|---|---|\n| x \\| y | z |", id="escaped-pipe"),
    pytest.param("| a | b |\n|---|---|\n| x\\\\ | \\*lit\\* |", id="backslashes"),
    pytest.param("  | a | b |\n  |---|---|\n  | 1 | 2 |", id="indented"),
    pytest.param("| a | b | c |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |", id="ragged"),
    pytest.param("| 名稱 | 說明 |\n|---|---|\n| 校正 | 讀回 4 bytes |", id="cjk"),
    pytest.param("| a | b |\n|---|---|\n| 😀 | **粗體** |", id="emoji"),
    pytest.param(REAL_WORLD, id="real-world"),
]


@pytest.mark.parametrize("src", ROUND_TRIP_CASES)
def test_parse_of_serialize_returns_the_same_model(src):
    model = parse_table(src)
    assert model is not None
    assert parse_table(serialize_table(model)) == model


@pytest.mark.parametrize("src", ROUND_TRIP_CASES)
def test_serialization_is_idempotent(src):
    once = serialize_table(parse_table(src))
    assert serialize_table(parse_table(once)) == once


def test_real_world_table_survives_the_round_trip_with_content_intact():
    model = parse_table(REAL_WORLD)
    assert model["headers"] == ["讀回的前 4 bytes", "代表"]
    assert model["aligns"] == ["", ""]
    assert model["rows"][0] == [
        "`FF FF FF FF`",
        "**這片板子從未校正過**（韌體實際跑 a=1.000, b=0）",
    ]
    assert len(model["rows"]) == 3
    rendered = serialize_table(model)
    assert parse_table(rendered) == model
    # And the normalized output really is aligned for a human reader.
    assert len({display_width(line) for line in rendered.split("\n")}) == 1


def test_round_trip_survives_a_cell_ending_in_a_backslash():
    # The space before the closing pipe is what stops "\\|" from swallowing it.
    model = parse_table("| a | b |\n|---|---|\n| x\\ | y |")
    assert model["rows"] == [["x\\", "y"]]
    assert parse_table(serialize_table(model)) == model
