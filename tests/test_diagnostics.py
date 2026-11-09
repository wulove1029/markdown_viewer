"""Privacy, bounds, export failure safety, and product instrumentation."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app import diagnostics, global_search, render_service
from app.search_index import SearchIndex


@pytest.fixture
def recorder(monkeypatch):
    result = diagnostics.Diagnostics(limit=3)
    monkeypatch.setattr(diagnostics, "recorder", result)
    return result


def test_ring_is_bounded_while_aggregates_retain_evicted_operations(recorder):
    for number in range(10):
        assert diagnostics.record(
            "library_search", duration_ms=number, work_items=100, cache_hits=90,
            cache_misses=10, cancelled=number == 9,
        )
    snapshot = recorder.snapshot()
    assert len(snapshot["events"]) == 3
    assert snapshot["omitted_events"] == 7
    assert [event["duration_ms"] for event in snapshot["events"]] == [7, 8, 9]
    assert snapshot["aggregates"]["library_search"] == {
        "events": 10, "duration_total_ms": 45, "duration_max_ms": 9,
        "work_items": 1000, "cache_hits": 900, "cache_misses": 100,
        "cancelled": 1, "fallback_requested": 0, "failed": 0,
    }
    snapshot["events"][0]["operation"] = "private mutation"
    snapshot["aggregates"]["library_search"]["events"] = -1
    assert recorder.snapshot()["aggregates"]["library_search"]["events"] == 10
    assert recorder.snapshot()["events"][0]["operation"] == "library_search"


def test_untrusted_fields_and_nonfinite_values_cannot_enter_export(recorder):
    private = "C:/Users/private/secret note.md"
    assert not diagnostics.record(private)
    assert not diagnostics.record("library_search", text="private query")
    assert not diagnostics.record("library_search", work_items=private)
    assert not diagnostics.record("library_search", duration_ms="private content")
    for duration in (float("nan"), float("inf"), -1, 86_400_001):
        assert not diagnostics.record("library_search", duration_ms=duration)
    assert not diagnostics.record("library_search", cancelled="private query")
    assert not diagnostics.record("library_search", cache_hits=True)
    assert recorder.snapshot()["recorded_events"] == 0
    assert private not in json.dumps(diagnostics.snapshot())


def test_parallel_recorders_do_not_lose_aggregates(recorder):
    def work(_index):
        for _event in range(100):
            diagnostics.record("markdown_render", duration_ms=2, work_items=2)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(work, range(8)))
    snapshot = recorder.snapshot()
    assert snapshot["recorded_events"] == 800
    assert snapshot["aggregates"]["markdown_render"]["work_items"] == 1600
    assert snapshot["aggregates"]["markdown_render"]["duration_total_ms"] == 1600


def test_export_is_readable_and_keeps_unowned_temporary_file(tmp_path, recorder):
    target = tmp_path / "診斷.json"
    foreign = tmp_path / "診斷.json.tmp"
    foreign.write_bytes(b"foreign content")
    diagnostics.record("markdown_render", duration_ms=1.25, fallback_requested=True)
    assert diagnostics.export(target) == target
    output = json.loads(target.read_text(encoding="utf-8"))
    assert output["storage"] == "memory_until_explicit_export"
    assert output["contains_document_content"] is False
    assert output["aggregates"]["markdown_render"]["fallback_requested"] == 1
    assert all(isinstance(value, int) for value in output["runtime_counters"].values())
    assert foreign.read_bytes() == b"foreign content"
    assert not list(tmp_path.glob(".mdv-diagnostics-*.tmp"))
    assert str(tmp_path) not in target.read_text(encoding="utf-8")


def test_failed_publish_preserves_existing_export(tmp_path, recorder, monkeypatch):
    target = tmp_path / "diagnostics.json"
    target.write_bytes(b"previous diagnostic export")

    def deny(*_args):
        raise PermissionError("write denied")

    monkeypatch.setattr(diagnostics.os, "replace", deny)
    with pytest.raises(PermissionError):
        diagnostics.export(target)
    assert target.read_bytes() == b"previous diagnostic export"
    assert not list(tmp_path.glob(".mdv-diagnostics-*.tmp"))


def test_cancelled_export_does_not_write(qapp, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: ("", ""))
    monkeypatch.setattr(diagnostics, "export", lambda *_args: pytest.fail("Unexpected write"))
    assert diagnostics.export_dialog() is None


def test_export_failure_is_reported_in_chinese(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_args: (str(tmp_path / "a.json"), ""),
    )

    def deny(*_args):
        raise OSError("disk full")

    messages = []
    monkeypatch.setattr(diagnostics, "export", deny)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append(_args))
    assert diagnostics.export_dialog() is None
    assert messages[0][1] == "無法匯出診斷"
    assert "無法寫入診斷檔" in messages[0][2]


def test_search_task_records_work_without_query_or_path(qapp, tmp_path, recorder, monkeypatch):
    monkeypatch.setattr(global_search, "load_excluded_folders", lambda: [])
    note = tmp_path / "private-note.md"
    note.write_text("private-query private-body", encoding="utf-8")
    task = global_search._SearchTask(
        1, [tmp_path], "private-query", Event(), SearchIndex(), {},
        on_progress=lambda *_args: None, on_finished=lambda *_args: None,
    )
    task.run()
    output = recorder.snapshot()
    assert output["aggregates"]["library_search"]["work_items"] == 1
    serialized = json.dumps(output)
    for private in ("private-query", "private-body", "private-note", str(tmp_path)):
        assert private not in serialized
    task.cancel_event.set()
    task.run()
    assert recorder.snapshot()["aggregates"]["library_search"]["cancelled"] == 1


@pytest.mark.parametrize("outcome", ["ok", "cancelled", "fallback"])
def test_render_records_outcomes_and_preserves_exceptions(recorder, monkeypatch, outcome):
    service = render_service.RenderService()

    def render(**_kwargs):
        if outcome == "cancelled":
            raise render_service.RenderWorkerCancelled()
        if outcome == "fallback":
            raise render_service.RenderWorkerError("private exception data")
        return "private render body", None

    monkeypatch.setattr(service, "_render", render)
    if outcome == "ok":
        assert service.render(text="private source") == ("private render body", None)
    else:
        error = (render_service.RenderWorkerCancelled if outcome == "cancelled"
                 else render_service.RenderWorkerError)
        with pytest.raises(error):
            service.render(text="private source")
    output = recorder.snapshot()
    entry = output["aggregates"]["markdown_render"]
    assert entry["cancelled"] == (outcome == "cancelled")
    assert entry["fallback_requested"] == (outcome == "fallback")
    assert entry["failed"] == (outcome == "fallback")
    assert "private" not in json.dumps(output)
