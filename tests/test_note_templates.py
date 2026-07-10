"""Tests for deterministic daily notes and Markdown templates."""

from datetime import datetime

from app.note_templates import (
    find_templates,
    open_or_create_daily_note,
    render_template,
)


def test_render_template_replaces_supported_variables():
    now = datetime(2026, 7, 11, 9, 5)

    rendered = render_template(
        "# {{title}}\n{{date}} {{time}} {{unknown}}",
        "Meeting",
        now,
    )

    assert rendered == "# Meeting\n2026-07-11 09:05 {{unknown}}"


def test_daily_note_creates_from_template_then_reopens_without_overwrite(tmp_path):
    template = tmp_path / "daily-template.md"
    template.write_text("# {{title}}\n\nCreated {{date}} at {{time}}", encoding="utf-8")
    folder = tmp_path / "missing" / "Daily Notes"
    now = datetime(2026, 7, 11, 8, 7)

    path, created = open_or_create_daily_note(folder, template, now)
    assert created is True
    assert path == folder / "2026-07-11.md"
    assert path.read_text(encoding="utf-8") == (
        "# 2026-07-11\n\nCreated 2026-07-11 at 08:07"
    )

    path.write_text("keep existing", encoding="utf-8")
    reopened, created_again = open_or_create_daily_note(folder, template, now)
    assert reopened == path
    assert created_again is False
    assert path.read_text(encoding="utf-8") == "keep existing"


def test_find_templates_handles_missing_folder_and_lists_nested_md(tmp_path):
    assert find_templates(tmp_path / "missing") == []

    folder = tmp_path / "Templates"
    nested = folder / "work"
    nested.mkdir(parents=True)
    first = folder / "Daily.md"
    second = nested / "Meeting.MD"
    first.write_text("daily", encoding="utf-8")
    second.write_text("meeting", encoding="utf-8")
    (folder / "ignore.txt").write_text("no", encoding="utf-8")

    assert find_templates(folder) == [first, second]
