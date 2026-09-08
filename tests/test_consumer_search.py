"""Consumer search coverage, diagnostics, and keyboard workflow regressions."""

import errno
import time

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from app import global_search
from app.global_search import GlobalSearchView, search_document_files, search_markdown_files
from app.md_converter import read_text_detailed


@pytest.fixture(autouse=True)
def isolated_search_settings(monkeypatch):
    monkeypatch.setattr(global_search, "load_excluded_folders", lambda: [])


def wait_for_search(qapp, view):
    deadline = time.monotonic() + 3
    while view._tasks and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert not view._tasks


def search_in_view(qapp, view, query="needle"):
    view._input.setText(query)
    view._search_now()
    wait_for_search(qapp, view)


@pytest.mark.parametrize("suffix", [".md", ".markdown", ".txt", ".MARKDOWN"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp950", "gbk"])
def test_supported_text_types_use_editor_decoding(tmp_path, suffix, encoding):
    note = tmp_path / ("note" + suffix)
    # GBK's common Chinese byte pairs may also be valid CP950. This character
    # exercises the shared decoder's GBK fallback without changing its policy.
    query = "\u4e02" if encoding == "gbk" else "中文"
    note.write_bytes(f"first\r\n{query} needle\r\n".encode(encoding))
    text, _encoding, _newline = read_text_detailed(note)

    report = search_document_files([tmp_path], query)

    assert report.searched_files == 1
    assert not report.issues
    assert len(report.results) == 1
    hit = report.results[0].hits[0]
    assert hit.line_number == 2
    assert hit.line == text.splitlines()[1]


def test_pdf_is_excluded_and_search_scope_is_visible(qapp, tmp_path):
    (tmp_path / "not-a-real.pdf").write_text("needle", encoding="utf-8")
    report = search_document_files([tmp_path], "needle")
    assert not report.results
    assert report.searched_files == 0
    view = GlobalSearchView(lambda: [tmp_path], lambda *_args: None)
    assert all(value in view._scope.text() for value in (".md", ".markdown", ".txt", "不含 PDF"))
    view.close()


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("no_roots", "尚未加入文件庫"),
        ("missing_root", "無法讀取文件庫"),
        ("no_text_files", "沒有可搜尋的文字文件"),
        ("no_matches", "找不到符合的內容"),
        ("provider_error", "無法取得文件庫來源"),
    ],
)
def test_search_distinguishes_empty_and_failed_sources(qapp, tmp_path, scenario, expected):
    if scenario == "no_matches":
        (tmp_path / "note.md").write_text("different content", encoding="utf-8")

    def roots_provider():
        if scenario == "provider_error":
            raise OSError("unavailable configuration")
        if scenario == "no_roots":
            return []
        return [tmp_path / "missing"] if scenario == "missing_root" else [tmp_path]

    view = GlobalSearchView(roots_provider, lambda *_args: None)
    search_in_view(qapp, view)
    assert expected in view._status.text()
    assert expected in view._list.item(0).text()
    view.close()


def test_partial_read_failure_keeps_matches_and_reports_skipped_file(qapp, tmp_path, monkeypatch):
    readable = tmp_path / "readable.md"
    locked = tmp_path / "locked.txt"
    readable.write_text("needle", encoding="utf-8")
    locked.write_text("needle", encoding="utf-8")

    def read_with_locked_file(path):
        if path == locked:
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return read_text_detailed(path)

    monkeypatch.setattr(global_search, "read_text_detailed", read_with_locked_file)
    view = GlobalSearchView(lambda: [tmp_path], lambda *_args: None)
    search_in_view(qapp, view)
    assert [result.path for result in view._results] == [readable]
    assert "共 1 筆，1 個檔案" in view._status.text()
    assert "已略過 1 個" in view._status.text()
    assert str(locked) in view._status.toolTip()
    view.close()


def test_walk_access_error_is_reported_as_unreadable_source(tmp_path, monkeypatch):
    def failed_walk(root, onerror):
        onerror(PermissionError(errno.EACCES, "permission denied", str(root)))
        return iter(())

    monkeypatch.setattr(global_search.os, "walk", failed_walk)
    report = search_document_files([tmp_path], "needle")
    assert report.readable_roots == 0
    assert report.searched_files == 0
    assert [issue.path for issue in report.issues] == [tmp_path]


def test_undecodable_and_real_replacement_characters_are_distinguished(tmp_path):
    broken = tmp_path / "broken.md"
    broken.write_bytes(b"needle\xff\xff")
    valid = tmp_path / "valid.md"
    valid.write_text("needle \ufffd", encoding="utf-8")

    report = search_document_files([tmp_path], "needle")

    assert [result.path for result in report.results] == [valid]
    assert [issue.path for issue in report.issues] == [broken]


def test_cancellation_during_long_document_discards_partial_results(tmp_path):
    (tmp_path / "note.md").write_text("needle\n" * 2000, encoding="utf-8")
    cancellation_checks = 0

    def cancel_during_scan():
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 5

    report = search_document_files([tmp_path], "needle", cancel_during_scan)
    assert report.cancelled
    assert not report.results
    assert search_markdown_files([tmp_path], "needle", lambda: True) == []


def test_keyboard_results_skip_headers_and_open_once(qapp, tmp_path):
    for name in ("first.md", "second.markdown"):
        (tmp_path / name).write_text("needle", encoding="utf-8")
    selected = []
    view = GlobalSearchView(lambda: [tmp_path], lambda *args: selected.append(args))
    view.show()
    search_in_view(qapp, view)
    view._input.setFocus()
    QTest.keyClick(view._input, Qt.Key.Key_Down)
    assert view._list.hasFocus()
    assert view._list.currentRow() == 1
    QTest.keyClick(view._list, Qt.Key.Key_Down)
    assert view._list.currentRow() == 3
    QTest.keyClick(view._list, Qt.Key.Key_Up)
    assert view._list.currentRow() == 1
    QTest.keyClick(view._list, Qt.Key.Key_Return)
    qapp.processEvents()
    assert selected == [(str(tmp_path / "first.md"), "needle", 1)]
    held_key = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "", True
    )
    qapp.sendEvent(view._list, held_key)
    assert len(selected) == 1
    rect = view._list.visualItemRect(view._list.item(3))
    QTest.mouseClick(view._list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert len(selected) == 2
    view.close()


def test_query_change_clears_old_results_and_rejects_late_report(qapp, tmp_path):
    (tmp_path / "note.md").write_text("needle", encoding="utf-8")
    selected = []
    view = GlobalSearchView(lambda: [tmp_path], lambda *args: selected.append(args))
    search_in_view(qapp, view)
    old_request_id = view._request_id
    old_report = view._report
    view._input.setText("missing")
    assert view._list.count() == 0
    view._on_search_finished(old_request_id, "needle", old_report)
    assert view._list.count() == 0
    search_in_view(qapp, view, "missing")
    assert view._status.text() == "找不到符合的內容"
    view._on_search_finished(old_request_id, "needle", old_report)
    assert view._status.text() == "找不到符合的內容"
    assert not selected
    view.close()
