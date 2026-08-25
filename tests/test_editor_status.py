"""Tests for the independent editor status strip."""

import pytest

from app.editor_status import EditorStatus, count_writing_units
from app.theme import DARK, LIGHT


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("", 0),
        ("你好 world", 3),
        ("Hello, world! 2026", 3),
        ("note-taking isn't two", 3),
        ("**粗體** and `code`", 4),
        ("測試 OpenAI 筆記 123", 6),
        ("𠀀 extension", 2),
        ("😊 ，。---", 0),
    ),
)
def test_count_writing_units_has_defined_mixed_language_semantics(text, expected):
    assert count_writing_units(text) == expected


def test_set_document_state_displays_complete_markdown_status(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(760, status.height())
        status.show()
        status.set_document_state(
            line=12,
            column=8,
            text="你好 world",
            document_kind="markdown",
            encoding="utf-8",
            newline="\n",
        )
        qapp.processEvents()

        expected = "第 12 行，第 8 欄｜3 字｜Markdown｜UTF-8｜LF"
        assert status.full_status_text == expected
        assert status.label.text() == expected
        assert status.writing_unit_count == 3
        assert status.toolTip() == status.label.toolTip() == expected
        assert expected in status.accessibleName()
        assert status.accessibleName() == status.label.accessibleName()
    finally:
        status.close()


def test_markdown_status_counts_visible_labels_not_hidden_resource_paths(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.set_document_state(
            line=1,
            column=1,
            text=(
                "![說明](assets/coil%20geometry.png) "
                "[OpenAI](https://openai.com/docs)"
            ),
            document_kind="markdown",
            encoding="utf-8",
            newline="\n",
        )

        assert status.writing_unit_count == 3
    finally:
        status.close()


def test_text_crlf_and_encoding_are_normalized(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(760, status.height())
        status.show()
        status.set_document_state(
            line=0,
            column=-4,
            text="plain text",
            document_kind="txt",
            encoding="utf_16_le",
            newline="CRLF",
        )
        qapp.processEvents()

        assert status.full_status_text == (
            "第 1 行，第 1 欄｜2 字｜純文字｜UTF-16-LE｜CRLF"
        )
    finally:
        status.close()


def test_cursor_only_update_keeps_document_metadata_and_count(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(760, status.height())
        status.set_document_state(
            line=1,
            column=1,
            text="你好 note",
            document_kind="text",
            encoding="big5",
            newline="\r\n",
        )
        status.set_cursor_position(line=7, column=3)
        status.show()
        qapp.processEvents()

        assert status.full_status_text == (
            "第 7 行，第 3 欄｜3 字｜純文字｜BIG5｜CRLF"
        )
    finally:
        status.close()


def test_narrow_status_compacts_without_losing_full_tooltip(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(155, status.height())
        status.show()
        status.set_document_state(
            line=1234,
            column=88,
            text="你好 mixed English words 2026",
            document_kind="markdown",
            encoding="utf-8-sig",
            newline="\r\n",
        )
        qapp.processEvents()

        assert status.label.text() != status.full_status_text
        assert status.label.toolTip() == status.full_status_text
        assert (
            status.label.fontMetrics().horizontalAdvance(status.label.text())
            <= status.label.contentsRect().width()
        )
        assert status.label.geometry().right() <= status.rect().right()
    finally:
        status.close()


def test_light_and_dark_themes_render_and_retain_state(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(620, status.height())
        status.set_document_state(
            line=2,
            column=5,
            text="深淺 dark theme",
            document_kind="md",
            encoding="utf-8",
            newline="LF",
        )
        status.show()
        qapp.processEvents()
        light_style = status.styleSheet()
        assert LIGHT.surface_alt in light_style
        assert LIGHT.text_muted in light_style
        assert status.grab().isNull() is False

        full_text = status.full_status_text
        status.apply_theme(DARK)
        qapp.processEvents()
        dark_style = status.styleSheet()
        assert dark_style != light_style
        assert DARK.surface_alt in dark_style
        assert DARK.text_muted in dark_style
        assert status.full_status_text == full_text
        assert status.grab().isNull() is False
    finally:
        status.close()


def test_unknown_document_kind_is_rejected(qapp):
    status = EditorStatus(LIGHT)
    try:
        with pytest.raises(ValueError, match="Unsupported document kind"):
            status.set_document_state(
                line=1,
                column=1,
                text="",
                document_kind="pdf",
                encoding="utf-8",
                newline="LF",
            )
    finally:
        status.close()
