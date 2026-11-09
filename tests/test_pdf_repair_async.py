"""The PDF ToUnicode repair runs on a worker thread in GUI exports."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app import export_actions, pdf_repair_async, pdf_tounicode, renderer
from tests.test_pdf_tounicode import SPAN_ONE, _pdf, _tounicode_text


def _wait_for(done: list, timeout_ms: int = 10000) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if done else None)
    poll.start(10)
    loop.exec()
    poll.stop()
    timer.stop()
    assert done, "repair callback never ran"


def _radical_pdf() -> bytes:
    return _pdf(
        b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"ET",
        "1 beginbfchar\n<0001> <2F00>\nendbfchar\n",
    )


def test_repair_runs_off_the_main_thread_and_reports_on_it(qapp, tmp_path, monkeypatch):
    path = tmp_path / "out.pdf"
    path.write_bytes(_radical_pdf())
    seen = {}
    done: list = []

    real = pdf_tounicode.repair_radical_tounicode_strict

    def spy(target):
        seen["worker"] = threading.current_thread()
        return real(target)

    def finished(ok):
        seen["callback"] = threading.current_thread()
        done.append(ok)

    monkeypatch.setattr(pdf_tounicode, "repair_radical_tounicode_strict", spy)
    pdf_repair_async.repair_async(str(path), finished)
    _wait_for(done)

    assert seen["worker"] is not threading.main_thread()
    assert seen["callback"] is threading.main_thread()
    assert done == [True]
    assert _tounicode_text(path.read_bytes()) == "一"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.pdf"]


def test_repair_failure_keeps_the_original_pdf(qapp, tmp_path, monkeypatch):
    path = tmp_path / "out.pdf"
    original = _radical_pdf()
    path.write_bytes(original)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    # The swap itself fails: the temp-file write must not touch the target.
    monkeypatch.setattr(pdf_tounicode, "atomic_write_bytes", boom)
    done: list = []
    pdf_repair_async.repair_async(str(path), done.append)
    _wait_for(done)

    assert done == [False]
    assert path.read_bytes() == original


def test_repair_skips_a_file_replaced_while_it_was_analysed(tmp_path, monkeypatch):
    """A newer export to the same path must never be overwritten."""
    path = tmp_path / "out.pdf"
    path.write_bytes(_radical_pdf())
    newer = _radical_pdf() + b"% newer export\n"
    real = pdf_tounicode.repair_radical_tounicode_bytes

    def slow(data):
        result = real(data)
        path.write_bytes(newer)  # another export lands during analysis
        return result

    monkeypatch.setattr(pdf_tounicode, "repair_radical_tounicode_bytes", slow)

    assert pdf_tounicode.repair_radical_tounicode_strict(path) == 0
    assert path.read_bytes() == newer


def test_two_repairs_of_one_path_leave_a_valid_file(qapp, tmp_path):
    path = tmp_path / "out.pdf"
    path.write_bytes(_radical_pdf())
    done: list = []
    pdf_repair_async.repair_async(str(path), done.append)
    pdf_repair_async.repair_async(str(path), done.append)
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if len(done) >= 2 else None)
    timer.start(10)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    timer.stop()

    assert done == [True, True]
    assert _tounicode_text(path.read_bytes()) == "一"


def _async_view(callback, on_failed=None):
    view = SimpleNamespace(
        _pdf_printing=True,
        _deferred_navigation=None,
        _pdf_callback=callback,
        _pdf_async_repair=(True, on_failed),
    )
    view._finish_pdf_export = (
        lambda *a, **k: renderer.RendererView._finish_pdf_export(view, *a, **k)
    )
    return view


def test_renderer_reports_only_after_async_repair(monkeypatch):
    pending = []
    monkeypatch.setattr(renderer, "repair_async", lambda p, cb: pending.append(cb))
    events = []
    view = _async_view(lambda path, ok: events.append((path, ok)))

    renderer.RendererView._on_pdf_finished(view, "out.pdf", True)

    assert events == [] and view._pdf_printing is True  # still "in progress"
    pending[0](True)
    assert events == [("out.pdf", True)] and view._pdf_printing is False


def test_renderer_keeps_unrepaired_pdf_and_warns_on_repair_failure(monkeypatch):
    pending = []
    monkeypatch.setattr(renderer, "repair_async", lambda p, cb: pending.append(cb))
    events = []
    view = _async_view(
        lambda path, ok: events.append(("done", path, ok)),
        lambda path: events.append(("failed", path)),
    )

    renderer.RendererView._on_pdf_finished(view, "out.pdf", True)
    pending[0](False)

    assert events == [("failed", "out.pdf"), ("done", "out.pdf", True)]


def test_renderer_print_failure_skips_repair(monkeypatch):
    monkeypatch.setattr(
        renderer, "repair_async", lambda *_: pytest.fail("must not repair")
    )
    events = []
    view = _async_view(lambda path, ok: events.append((path, ok)))

    renderer.RendererView._on_pdf_finished(view, "out.pdf", False)

    assert events == [("out.pdf", False)]


def test_second_export_is_refused_while_repair_is_running(monkeypatch):
    """_pdf_printing stays set until the repair ends, so export_pdf re-entry
    is rejected instead of printing over a file being rewritten."""
    pending = []
    monkeypatch.setattr(renderer, "repair_async", lambda p, cb: pending.append(cb))
    refused = []
    view = _async_view(lambda *_: None)

    renderer.RendererView._on_pdf_finished(view, "out.pdf", True)
    renderer.RendererView.export_pdf(
        view, "out.pdf", lambda path, ok: refused.append((path, ok))
    )

    assert refused == [("out.pdf", False)]


def test_destroyed_view_gets_no_late_callback(monkeypatch):
    pending = []
    monkeypatch.setattr(renderer, "repair_async", lambda p, cb: pending.append(cb))
    events = []
    view = _async_view(lambda path, ok: events.append((path, ok)))

    renderer.RendererView._on_pdf_finished(view, "out.pdf", True)
    monkeypatch.setattr(renderer, "isValid", lambda _obj: False)
    pending[0](True)

    assert events == []


def test_on_pdf_exported_warns_once_after_repair_failure(monkeypatch):
    from tests.test_pdf_export import _Window

    window = _Window()
    window._pdf_progress = None
    window._statusbar = SimpleNamespace(
        showMessage=lambda *a: None, clearMessage=lambda: None
    )
    window.statusBar = lambda: window._statusbar
    warnings, completes = [], []
    monkeypatch.setattr(
        export_actions.QMessageBox, "warning", lambda *a: warnings.append(a)
    )
    monkeypatch.setattr(
        export_actions, "show_export_complete", lambda w, p: completes.append(p)
    )

    export_actions._repair_failed_handler(window)("out.pdf")
    export_actions.on_pdf_exported(window, "out.pdf", True)
    export_actions.on_pdf_exported(window, "out.pdf", True)

    assert len(warnings) == 1 and completes == ["out.pdf", "out.pdf"]
