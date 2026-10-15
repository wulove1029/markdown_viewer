"""Shared tags, bounded metadata memory and cancellable source-only work."""

import pickle
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import document_metadata as dm
from app import md_converter as mc
from app import render_service


@pytest.fixture(autouse=True)
def isolated_caches():
    dm.invalidate()
    mc._CONVERT_CACHE.clear()
    yield
    dm.invalidate()
    mc._CONVERT_CACHE.clear()
    render_service.shutdown()


def test_complete_render_reuses_its_source_read_and_populates_metadata(tmp_path, monkeypatch):
    path = tmp_path / "tags.md"
    path.write_text("---\ntags: [front]\n---\n# Heading\n\n#body `#hidden`\n", encoding="utf-8")
    reads = []
    original = mc.read_text

    def counted_read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(mc, "read_text", counted_read)
    mc.convert(path)
    metadata = dm.cached_metadata(path)
    assert metadata.front_tags == ("front",)
    assert metadata.body_tags == ("body",)
    assert dm.read_metadata(path) is metadata
    mc.convert(path)
    assert reads == [path]


def test_body_cache_repopulates_evicted_metadata_without_scanning(tmp_path, monkeypatch):
    path = tmp_path / "tags.md"
    path.write_text("#tag\n", encoding="utf-8")
    mc.convert(path)
    dm.invalidate()
    monkeypatch.setattr(mc, "body_hashtags", lambda *a, **k: pytest.fail("repeat scan"))
    mc.convert(path)
    assert dm.cached_metadata(path).body_tags == ("tag",)


def test_partial_render_does_not_publish_incomplete_metadata(tmp_path):
    path = tmp_path / "tags.md"
    path.write_text("#early\n\n" + "text\n\n" * 20_000 + "#late\n", encoding="utf-8")
    assert mc.render_file_partial(path, path.stat().st_size) is not None
    assert dm.cached_metadata(path) is None


def test_disk_change_invalidates_metadata_and_buffer_never_poison_disk_cache(tmp_path):
    path = tmp_path / "tags.md"
    path.write_text("#disk\n", encoding="utf-8")
    first = dm.read_metadata(path)
    dirty = dm.read_metadata(path, text="#unsaved\n")
    assert dirty.body_tags == ("unsaved",)
    assert dirty.signature is None
    assert dm.cached_metadata(path) is first
    path.write_text("#changed\n", encoding="utf-8")
    assert dm.cached_metadata(path) is None
    assert dm.read_metadata(path).body_tags == ("changed",)
    assert len(dm._CACHE) == 1


def test_same_size_file_replacement_invalidates_metadata(tmp_path):
    import os

    path = tmp_path / "tags.md"
    path.write_text("#one\n", encoding="utf-8")
    dm.read_metadata(path)
    old = path.stat()
    replacement = tmp_path / "replacement.md"
    replacement.write_text("#two\n", encoding="utf-8")
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(replacement, path)
    assert dm.cached_metadata(path) is None
    assert dm.read_metadata(path).body_tags == ("two",)


def test_metadata_cache_has_entry_and_byte_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "_CACHE_MAX", 2)
    paths = [tmp_path / f"{i}.md" for i in range(3)]
    for index, path in enumerate(paths):
        path.write_text(f"#tag{index}\n", encoding="utf-8")
        dm.read_metadata(path)
    assert len(dm._CACHE) == 2
    assert dm.cached_metadata(paths[0]) is None
    assert dm.cached_metadata(paths[1]) is not None
    monkeypatch.setattr(dm, "_CACHE_BYTES", 1)
    dm.read_metadata(paths[0])
    assert dm.cached_metadata(paths[0]) is None


def test_concurrent_metadata_only_reads_are_coalesced(tmp_path, monkeypatch):
    path = tmp_path / "tags.md"
    path.write_text("#once\n", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    original = mc.read_text
    calls = []

    def blocked_read(path):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr(mc, "read_text", blocked_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(dm.read_metadata, path)
        assert entered.wait(5)
        second = executor.submit(dm.read_metadata, path)
        release.set()
        assert first.result().body_tags == ("once",)
        assert second.result().body_tags == ("once",)
    assert calls == [path]


def test_changed_during_read_is_not_cached_or_returned(tmp_path, monkeypatch):
    path = tmp_path / "tags.md"
    path.write_text("#before\n", encoding="utf-8")

    def changed_read(path):
        path.write_text("#afterModification\n", encoding="utf-8")
        return "#before\n", "utf-8"

    monkeypatch.setattr(mc, "read_text", changed_read)
    with pytest.raises(OSError, match="changed while reading"):
        dm.read_metadata(path)
    assert dm.cached_metadata(path) is None
    assert dm._IN_FLIGHT == {}


def test_unreadable_metadata_does_not_replace_tags_with_empty_result(tmp_path, monkeypatch):
    path = tmp_path / "tags.md"
    path.write_bytes(b"\xff")
    monkeypatch.setattr(mc, "read_text", lambda path: None)
    with pytest.raises(OSError, match="Unable to decode"):
        dm.read_metadata(path)
    assert dm.cached_metadata(path) is None


def test_precancelled_metadata_does_not_read(tmp_path, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(mc, "read_text", lambda path: pytest.fail("stale read"))
    with pytest.raises(mc.RenderCancelled):
        dm.read_metadata(tmp_path / "missing.md", cancel=cancel)


def test_line_scan_checks_cancellation_between_batches():
    class CancelAfterFirstBatch:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    with pytest.raises(mc.RenderCancelled):
        mc.body_hashtags("#tag\n" * 1000, cancel=CancelAfterFirstBatch())


def test_large_metadata_only_uses_real_child_without_generating_html(tmp_path, monkeypatch):
    path = tmp_path / "large.md"
    path.write_text("---\ntags: [front]\n---\n" + "prose\n" * 50_000 + "#tail\n", encoding="utf-8")
    monkeypatch.delenv(render_service.DISABLE_ENV, raising=False)
    monkeypatch.setattr(mc, "read_text", lambda path: pytest.fail("parent read"))
    monkeypatch.setattr(mc, "render_body", lambda *a, **k: pytest.fail("parent render"))
    metadata = dm.read_metadata(path)
    assert isinstance(metadata, dm.DocumentMetadata)
    assert metadata.front_tags == ("front",)
    assert metadata.body_tags == ("tail",)
    assert render_service.service().remote_renders >= 1
    assert not hasattr(metadata, "body")
    assert not mc.has_cached_body(path)


def test_metadata_child_transport_and_full_body_serialization_are_compatible():
    service = render_service.RenderService()
    try:
        metadata, error = service.render(text="#tag\n", metadata_only=True)
        assert error is None
        assert metadata.body_tags == ("tag",)
        body, error = service.render(text="---\ntags: [front]\n---\n#tag\n")
        assert error is None
        assert body.front_tags == ("front",)
        assert body.body_tags == ("tag",)
        assert pickle.loads(pickle.dumps(body)) == body
        assert mc.RenderedBody("", [], False, False, False).front_tags == ()
    finally:
        service.shutdown()


def test_cancelling_metadata_request_really_stops_child():
    service = render_service.RenderService()
    cancel = threading.Event()
    timer = None
    try:
        service.render(text="#warm", metadata_only=True)
        worker = service._idle
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        with pytest.raises(render_service.RenderWorkerCancelled):
            service.render(text="#" + "a" * 5_000_000,
                           metadata_only=True, cancel=cancel)
        assert worker.proc.poll() is not None
        assert service.killed >= 1
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        service.shutdown()


def test_medium_code_heavy_preview_hint_reads_only_bounded_sample(tmp_path, monkeypatch):
    path = tmp_path / "code.md"
    path.write_text(("```python\nx = 1\n```\n\n" * 20_000), encoding="utf-8")
    calls = []
    original = mc._read_prefix

    def bounded_read(path, budget):
        calls.append(budget)
        return original(path, budget)

    monkeypatch.setattr(mc, "_read_prefix", bounded_read)
    assert mc.should_fast_preview(path, path.stat().st_size)
    partial = mc.render_file_partial(path, path.stat().st_size)
    assert partial is not None
    assert "partial-preview-notice" in partial[0].body
    assert calls == [64 * 1024, 128 * 1024]


def test_medium_plain_prose_does_not_request_extra_partial(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("plain prose\n\n" * 30_000, encoding="utf-8")
    assert not mc.should_fast_preview(path, path.stat().st_size)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp950", "gbk"])
def test_bounded_prefix_preserves_multibyte_encoding(tmp_path, encoding):
    path = tmp_path / "unicode.md"
    path.write_bytes(("中文內容\n\n" * 500).encode(encoding))
    sample = mc._read_prefix(path, 101)
    assert sample.startswith("中文內容\n\n")
    assert "�" not in sample


def test_partial_read_refuses_a_single_huge_fence(tmp_path):
    path = tmp_path / "fence.md"
    path.write_text("```python\n" + "value = 1\n" * 50_000 + "```\n", encoding="utf-8")
    assert mc.render_file_partial(path, path.stat().st_size) is None
