"""Tests for the grouped two-line recent-files panel."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings as QtQSettings, Qt

from app import recent_files as recent_mod
from app.recent_files import (
    _FILE_ROW_HEIGHT,
    _KIND_EMPTY,
    _KIND_FILE,
    _KIND_HEADER,
    _KIND_ROLE,
    _META_ROLE,
    _MISSING_ROLE,
    _OPENED_AT_ROLE,
    _PARENT_ROLE,
    _PATH_ROLE,
    RecentFilesView,
    _timestamp_ms,
)
from app.theme import DARK


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    settings = QtQSettings(
        str(tmp_path / "recent-files.ini"), QtQSettings.Format.IniFormat
    )
    settings.clear()
    monkeypatch.setattr(recent_mod, "QSettings", lambda *_args, **_kwargs: settings)
    yield settings
    settings.clear()


@pytest.fixture
def now():
    # Naive input intentionally exercises conversion to the machine's local zone.
    return datetime(2026, 8, 25, 14, 30).astimezone()


def _items(view: RecentFilesView, kind: str):
    return [
        view.item(index)
        for index in range(view.count())
        if view.item(index).data(_KIND_ROLE) == kind
    ]


def _action(menu, text: str):
    return next(action for action in menu.actions() if action.text() == text)


def test_empty_and_legacy_rows_preserve_old_settings(
    qapp, tmp_path, isolated_settings, now
):
    opened = []
    empty_view = RecentFilesView(opened.append, clock=lambda: now)
    try:
        assert empty_view.count() == 1
        placeholder = empty_view.item(0)
        assert placeholder.text() == "尚無最近開啟的檔案"
        assert placeholder.data(_KIND_ROLE) == _KIND_EMPTY
        assert not (placeholder.flags() & Qt.ItemFlag.ItemIsEnabled)
        assert not (placeholder.flags() & Qt.ItemFlag.ItemIsSelectable)
    finally:
        empty_view.close()

    legacy = tmp_path / "legacy.md"
    legacy.write_text("# legacy", encoding="utf-8")
    RecentFilesView._save([str(legacy.resolve())])

    view = RecentFilesView(opened.append, clock=lambda: now)
    try:
        assert [item.text() for item in _items(view, _KIND_HEADER)] == ["更早"]
        file_item = _items(view, _KIND_FILE)[0]
        assert file_item.text() == "legacy.md"
        assert file_item.data(_PATH_ROLE) == str(legacy.resolve())
        assert file_item.data(_PARENT_ROLE) == str(legacy.parent.resolve())
        assert file_item.data(_META_ROLE) == "較早開啟"
        assert file_item.data(_OPENED_AT_ROLE) is None
        assert str(legacy.resolve()) in file_item.toolTip()
        assert view.paths() == [str(legacy.resolve())]

        view.itemClicked.emit(file_item)
        assert opened == [str(legacy.resolve())]
    finally:
        view.close()


def test_add_promotes_deduplicates_caps_and_updates_timestamp(
    qapp, tmp_path, isolated_settings, now
):
    paths = []
    for index in range(12):
        path = tmp_path / f"note-{index}.md"
        path.write_text(str(index), encoding="utf-8")
        paths.append(path)

    view = RecentFilesView(lambda _path: None, clock=lambda: now)
    try:
        for index, path in enumerate(paths):
            view.add(path, opened_at=now - timedelta(minutes=11 - index))

        expected = [str(path.resolve()) for path in reversed(paths[2:])]
        assert view._load() == expected
        assert len(view._load_times()) == 10

        promoted_at = now + timedelta(minutes=5)
        view.add(paths[5], opened_at=promoted_at)
        assert view._load()[0] == str(paths[5].resolve())
        assert view._load().count(str(paths[5].resolve())) == 1
        assert view._load_times()[str(paths[5].resolve())] == _timestamp_ms(promoted_at)
        assert view.currentItem().data(_PATH_ROLE) == str(paths[5].resolve())
    finally:
        view.close()


def test_time_groups_same_names_tooltips_and_narrow_render(
    qapp, tmp_path, isolated_settings, now
):
    folder_a = tmp_path / "project-a"
    folder_b = tmp_path / "project-b"
    folder_a.mkdir()
    folder_b.mkdir()
    today_a = folder_a / "readme.md"
    today_b = folder_b / "readme.md"
    yesterday = folder_a / "manual.pdf"
    earlier = folder_b / "archive.md"
    for path in (today_a, today_b, yesterday, earlier):
        path.write_text(path.name, encoding="utf-8")

    view = RecentFilesView(lambda _path: None, clock=lambda: now)
    try:
        view.add(earlier, opened_at=now - timedelta(days=9))
        view.add(yesterday, opened_at=now - timedelta(days=1))
        view.add(today_a, opened_at=now - timedelta(minutes=8))
        view.add(today_b, opened_at=now - timedelta(seconds=20))

        assert [item.text() for item in _items(view, _KIND_HEADER)] == [
            "今天",
            "昨天",
            "更早",
        ]
        files = _items(view, _KIND_FILE)
        readmes = [item for item in files if item.text() == "readme.md"]
        assert len(readmes) == 2
        assert {item.data(_PARENT_ROLE) for item in readmes} == {
            str(folder_a.resolve()),
            str(folder_b.resolve()),
        }
        assert {item.data(_META_ROLE) for item in readmes} == {
            "剛剛",
            "8 分鐘前",
        }
        assert all(item.data(_PATH_ROLE) in item.toolTip() for item in files)

        view.resize(180, 320)
        view.show()
        qapp.processEvents()
        assert all(
            view.visualItemRect(item).height() == _FILE_ROW_HEIGHT for item in files
        )
        assert view.viewport().grab().isNull() is False
        assert view.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
    finally:
        view.close()


def test_missing_file_is_visible_removable_and_not_openable(
    qapp, tmp_path, isolated_settings, now
):
    missing = tmp_path / "removed.md"
    path = str(missing.resolve())
    RecentFilesView._save([path])
    RecentFilesView._save_times({path: _timestamp_ms(now - timedelta(hours=2))})
    opened = []

    view = RecentFilesView(opened.append, clock=lambda: now)
    try:
        item = _items(view, _KIND_FILE)[0]
        assert item.text() == "removed.md"
        assert item.data(_MISSING_ROLE) is True
        assert item.data(_META_ROLE) == "位置不存在"
        assert "位置不存在" in item.toolTip()

        view.itemClicked.emit(item)
        assert opened == []

        menu = view._build_context_menu(item)
        assert _action(menu, "開啟文件").isEnabled() is False
        assert _action(menu, "在檔案總管中顯示").isEnabled() is False
        assert _action(menu, "從最近清單移除").isEnabled() is True
        assert _action(menu, "清除最近清單").isEnabled() is True

        _action(menu, "從最近清單移除").trigger()
        assert view._load() == []
        assert view._load_times() == {}
        assert view.item(0).data(_KIND_ROLE) == _KIND_EMPTY
    finally:
        view.close()


def test_context_menu_clear_all_works_from_empty_area(
    qapp, tmp_path, isolated_settings, now
):
    note = tmp_path / "note.md"
    note.write_text("# note", encoding="utf-8")
    view = RecentFilesView(lambda _path: None, clock=lambda: now)
    try:
        view.add(note, opened_at=now)
        menu = view._build_context_menu(None)
        visible_actions = [action.text() for action in menu.actions() if action.text()]
        assert visible_actions == ["清除最近清單"]

        _action(menu, "清除最近清單").trigger()
        assert view._load() == []
        assert view._load_times() == {}
        assert view.item(0).text() == "尚無最近開啟的檔案"
    finally:
        view.close()


def test_context_menu_actions_survive_periodic_item_refresh(
    qapp, tmp_path, isolated_settings, now, monkeypatch
):
    note = tmp_path / "note.md"
    note.write_text("# note", encoding="utf-8")
    path = str(note.resolve())
    opened = []
    revealed = []
    view = RecentFilesView(opened.append, clock=lambda: now)
    try:
        view.add(note, opened_at=now)
        item = _items(view, _KIND_FILE)[0]
        open_menu = view._build_context_menu(item)
        reveal_menu = view._build_context_menu(item)
        remove_menu = view._build_context_menu(item)
        monkeypatch.setattr(view, "_open_location_path", revealed.append)

        # The minute timer rebuilds every QListWidgetItem while a modal menu can
        # remain open. Actions must retain only the path, never the deleted item.
        view._refresh()
        _action(open_menu, "開啟文件").trigger()
        _action(reveal_menu, "在檔案總管中顯示").trigger()
        _action(remove_menu, "從最近清單移除").trigger()

        assert opened == [path]
        assert revealed == [path]
        assert view._load() == []
        assert view._load_times() == {}
    finally:
        view.close()


def test_migrate_and_remove_keep_timestamp_metadata_in_sync(
    qapp, tmp_path, isolated_settings, now
):
    old = tmp_path / "old.md"
    target = tmp_path / "target.md"
    old.write_text("old", encoding="utf-8")
    target.write_text("target", encoding="utf-8")
    old_path = str(old.resolve())
    target_path = str(target.resolve())
    recent_time = _timestamp_ms(now - timedelta(minutes=2))
    older_time = _timestamp_ms(now - timedelta(days=3))
    RecentFilesView._save([old_path, target_path])
    RecentFilesView._save_times(
        {old_path: recent_time, target_path: older_time}
    )

    view = RecentFilesView(lambda _path: None, clock=lambda: now)
    try:
        view.migrate_paths({old: target})
        assert view._load() == [target_path]
        assert view._load_times() == {target_path: recent_time}

        view.remove_paths([target])
        assert view._load() == []
        assert view._load_times() == {}
    finally:
        view.close()


def test_theme_and_minute_refresh_preserve_selected_path(
    qapp, tmp_path, isolated_settings, now
):
    note = tmp_path / "note.md"
    note.write_text("# note", encoding="utf-8")
    current_time = [now]
    view = RecentFilesView(lambda _path: None, clock=lambda: current_time[0])
    try:
        view.add(note, opened_at=now)
        selected_path = view.currentItem().data(_PATH_ROLE)

        view.apply_theme(DARK)
        assert view._delegate._theme == DARK
        assert view.currentItem().data(_PATH_ROLE) == selected_path

        current_time[0] = now + timedelta(days=1, minutes=1)
        view._refresh()
        assert [item.text() for item in _items(view, _KIND_HEADER)] == ["昨天"]
        assert view.currentItem().data(_PATH_ROLE) == selected_path
        assert view.currentItem().data(_META_ROLE) == "昨天"
    finally:
        view.close()
