"""Search, previews, tags and links share fresh bounded decoded content."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app import document_metadata as metadata
from app import global_search, library_content, links, md_converter, search_index, tag_flow
from app.document_metadata import DocumentMetadata
from app.library_content import DerivedContentIndex, content_key, read_source
from app.search_index import SearchCancelled, default_index


@pytest.fixture(autouse=True)
def clean_indexes():
    default_index.invalidate()
    library_content.derived_index.clear()
    metadata.invalidate()
    md_converter._CONVERT_CACHE.clear()
    yield
    default_index.invalidate()
    library_content.derived_index.clear()
    metadata.invalidate()
    md_converter._CONVERT_CACHE.clear()


def test_search_links_metadata_and_preview_decode_each_source_once(tmp_path, monkeypatch):
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("#tag [[b]]", encoding="utf-8")
    second.write_text("#other", encoding="utf-8")
    reads_before = default_index.reads
    report = global_search.search_document_files([tmp_path], "tag", index=default_index)
    assert len(report.results) == 1
    docs = links.read_docs([first, second])
    index = links.LinkIndex()
    index.build(docs)
    assert index.forward[str(first)] == {str(second)}
    monkeypatch.setattr(md_converter, "body_hashtags", lambda *a, **k: pytest.fail("repeat scan"))
    assert metadata.read_metadata(first).body_tags == ("tag",)
    # Rendering may scan tags as part of its HTML work, but source decoding is shared.
    monkeypatch.undo()
    assert "tag" in md_converter.convert(first)[0]
    assert default_index.reads - reads_before == 2


def test_preview_first_also_warms_search_and_link_source_cache(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("#shared [[target]]", encoding="utf-8")
    reads_before = default_index.reads
    md_converter.convert(path)
    monkeypatch.setattr(md_converter, "read_text", lambda *_: pytest.fail("repeat source read"))
    assert links.read_docs([path])[0][1] == "#shared [[target]]"
    assert global_search.search_document_files([tmp_path], "shared", index=default_index).results
    assert metadata.read_metadata(path).body_tags == ("shared",)
    assert default_index.reads - reads_before == 1


def test_link_parsing_is_incremental_and_resolution_tracks_deleted_and_moved_paths(
    tmp_path, monkeypatch,
):
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("#tag [[b]]", encoding="utf-8")
    second.write_text("target", encoding="utf-8")
    calls = []
    extract = links.extract_markdown_links

    def counted(text, **kwargs):
        calls.append(text)
        return extract(text, **kwargs)

    monkeypatch.setattr(links, "extract_markdown_links", counted)
    index = links.LinkIndex()
    index.build(links.read_docs([first, second]))
    index.build(links.read_docs([first, second]))
    assert len(calls) == 2
    renamed = second.with_name("c.md")
    second.rename(renamed)
    index.build(links.read_docs([first, renamed]))
    assert index.forward[str(first)] == set()
    assert str(second) not in index.forward
    assert str(second) not in index.tags
    assert len(calls) == 2
    first.write_text("#changed [[c]]", encoding="utf-8")
    index.build(links.read_docs([first, renamed]))
    assert index.forward[str(first)] == {str(renamed)}
    assert index.tags[str(first)] == {"changed"}
    assert len(calls) == 3
    first.unlink()
    index.build(links.read_docs([first, renamed]))
    assert str(first) not in index.forward
    assert index.backlinks(renamed) == []


def test_restored_mtime_rewrite_invalidates_metadata_preview_and_links(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("#one [[a]]", encoding="utf-8")
    original = path.stat()
    first_signature = metadata.signature(path)
    assert metadata.read_metadata(path).body_tags == ("one",)
    assert "one" in md_converter.convert(path)[0]
    old_docs = links.read_docs([path])
    time.sleep(0.01)
    path.write_text("#two [[b]]", encoding="utf-8")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_size == original.st_size
    assert metadata.signature(path) != first_signature
    assert md_converter._body_signature(path) == metadata.signature(path)
    assert metadata.cached_metadata(path) is None
    assert metadata.read_metadata(path).body_tags == ("two",)
    html = md_converter.convert(path)[0]
    assert "two" in html
    assert metadata.cached_metadata(path).body_tags == ("two",)
    assert old_docs[0][1] != links.read_docs([path])[0][1]


def test_shared_read_coalesces_concurrent_library_and_metadata_requests(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("#once", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    original = md_converter.read_text
    calls = []

    def delayed(path):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(md_converter, "read_text", delayed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(links.read_docs, [path])
        assert entered.wait(5)
        second = executor.submit(metadata.read_metadata, path)
        release.set()
        assert first.result()[0][1] == "#once"
        assert second.result().body_tags == ("once",)
    assert calls == [path]
    assert library_content._READING == {}


def test_precancelled_library_scan_never_reads_or_returns_partial_index(tmp_path):
    with pytest.raises(SearchCancelled):
        links.read_docs([tmp_path / "missing.md"], should_cancel=lambda: True)
    with pytest.raises(SearchCancelled):
        links.LinkIndex().build([(tmp_path / "a.md", "#tag")], should_cancel=lambda: True)
    with pytest.raises(SearchCancelled):
        read_source(tmp_path / "missing.md", should_cancel=lambda: True)


def test_derived_cache_budget_eviction_and_identical_content_reuse():
    index = DerivedContentIndex(max_bytes=1500, max_entries=2)
    for number in range(10):
        index.remember("links", content_key(str(number)), (("a" * 100,), ("target",)))
        assert index.retained_bytes <= 1500
        assert len(index._entries) <= 2
    assert index.get("links", content_key("0")) is None
    assert index.get("links", content_key("9")) is not None
    index.remember("links", content_key("oversize"), ("x" * 10000,))
    assert index.get("links", content_key("oversize")) is None
    index.clear()
    assert index.retained_bytes == 0


def test_same_tick_same_size_rewrite_is_seen_by_source_reads_and_tags(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("#old", encoding="utf-8")
    frozen = search_index.file_version(path)
    monkeypatch.setattr(search_index, "file_version", lambda _path: frozen)
    monkeypatch.setattr(library_content, "file_version", lambda _path: frozen)
    stamp = frozen.modified_ns
    monkeypatch.setattr(search_index, "_now_ns", lambda: stamp)
    old = metadata.read_metadata(path)
    assert old.body_tags == ("old",)
    path.write_text("#new", encoding="utf-8")  # same size, same reported version
    assert metadata.signature(path) != old.signature
    assert read_source(path) == "#new"
    assert metadata.read_metadata(path).body_tags == ("new",)


def test_racy_signature_stays_equal_for_unchanged_content_after_the_window(tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("#same", encoding="utf-8")
    first = metadata.signature(path)
    assert first[-1] is not None  # fresh file: content hash recorded
    now = search_index._now_ns()
    monkeypatch.setattr(search_index, "_now_ns", lambda: now + 2 * search_index.RACY_WINDOW_NS)
    assert metadata.signature(path) == first
    monkeypatch.setattr(search_index, "_now_ns", lambda: now + 10**12)
    assert metadata.signature(path)[-1] is None  # settled and no longer remembered


def test_saved_tag_request_ignores_previous_generation_and_external_rewrite(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("#old", encoding="utf-8")
    old = metadata.read_metadata(path)
    updates = []
    window = SimpleNamespace(
        _metadata_generation=7, _current_file=path, _current_kind="markdown",
        _current_front_tags=[], _current_body_tags=["known"], _front_tags_pending=True,
        _doc_annotations=None, _tag_index=SimpleNamespace(update=lambda *a, **k: updates.append(k)),
        _refresh_tags_panel=lambda: None,
    )
    tag_flow.apply_metadata(window, 6, path, old)
    path.write_text("#new", encoding="utf-8")
    tag_flow.apply_metadata(window, 7, path, old)
    assert updates == []
    assert window._front_tags_pending
    assert window._current_body_tags == ["known"]
    tag_flow.apply_metadata(window, 7, path, metadata.read_metadata(path))
    assert not window._front_tags_pending
    assert updates[-1]["body_tags"] == ["new"]
    tag_flow.apply_metadata(window, 6, path, DocumentMetadata(body_tags=("stale",)))
    assert updates[-1]["body_tags"] == ["new"]
