from pathlib import Path
import time

from app.global_search import GlobalSearchView, search_markdown_files


def _result_paths(results):
    return {result.path.resolve() for result in results}


def test_search_finds_matches_across_library_folders(tmp_path):
    first_root = tmp_path / "vault-a"
    second_root = tmp_path / "vault-b"
    first_root.mkdir()
    (second_root / "nested").mkdir(parents=True)
    first = first_root / "first.md"
    second = second_root / "nested" / "second.md"
    first.write_text("alpha\nShared needle\n", encoding="utf-8")
    second.write_text("needle in another vault\n", encoding="utf-8")
    (second_root / "ignored.txt").write_text("needle", encoding="utf-8")

    results = search_markdown_files([first_root, second_root], "needle")

    assert _result_paths(results) == {first.resolve(), second.resolve()}
    assert [hit.line_number for hit in results[0].hits] in ([2], [1])
    assert sum(result.match_count for result in results) == 2


def test_search_is_case_insensitive(tmp_path):
    note = tmp_path / "Mixed.md"
    note.write_text("Needle and nEeDlE\n", encoding="utf-8")

    results = search_markdown_files([tmp_path], "NEEDLE")

    assert len(results) == 1
    assert results[0].path == note
    assert results[0].hits[0].match_count == 2


def test_search_returns_no_results_for_missing_text(tmp_path):
    (tmp_path / "note.md").write_text("nothing here", encoding="utf-8")

    assert search_markdown_files([tmp_path], "absent") == []


def test_search_skips_invalid_utf8_file(tmp_path):
    corrupt = tmp_path / "corrupt.md"
    corrupt.write_bytes(b"needle\xffstill malformed")

    assert search_markdown_files([tmp_path], "needle") == []


def test_search_applies_user_directory_exclusions(tmp_path, monkeypatch):
    included = tmp_path / "docs"
    excluded = tmp_path / "app_flutter" / "ios"
    included.mkdir()
    excluded.mkdir(parents=True)
    keep = included / "keep.md"
    keep.write_text("needle", encoding="utf-8")
    (excluded / "hidden.md").write_text("needle", encoding="utf-8")
    monkeypatch.setattr(
        "app.global_search.load_excluded_folders",
        lambda: ["app_flutter/ios"],
    )

    assert _result_paths(search_markdown_files([tmp_path], "needle")) == {
        keep.resolve()
    }


def test_overlapping_roots_do_not_duplicate_results(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    note = nested / "note.md"
    note.write_text("needle", encoding="utf-8")

    results = search_markdown_files([tmp_path, nested], "needle")

    assert [result.path for result in results] == [note]


def test_search_view_renders_highlights_and_opens_selected_result(qapp, tmp_path):
    note = tmp_path / "note.md"
    note.write_text("A highlighted Needle result", encoding="utf-8")
    selected = []
    view = GlobalSearchView(
        roots_provider=lambda: [tmp_path],
        on_result_selected=lambda *args: selected.append(args),
    )

    view._input.setText("needle")
    view._search_now()
    deadline = time.monotonic() + 3
    while view._tasks and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    assert not view._tasks
    assert view._status.text() == "共 1 筆，1 個檔案"
    assert view._list.count() == 2
    hit_item = view._list.item(1)
    label = view._list.itemWidget(hit_item)
    assert "background-color" in label.text()

    view._on_item_clicked(hit_item)
    assert selected == [(str(note), "needle", 1)]

    view._render_results("absent", [])
    assert view._list.item(0).text() == "找不到符合的內容"
