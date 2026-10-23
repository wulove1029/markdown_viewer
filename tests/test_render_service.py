"""The killable out-of-process Markdown renderer (spec 3.B).

These tests start real child processes: the whole point of the design is that
an in-flight parse can be stopped, which cannot be shown with a fake.
"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app import md_converter as mc
from app import render_service

ROOT = Path(__file__).resolve().parents[1]
BIG_DOC = "".join(f"## Heading {i}\n\nSome prose {i} with `code` and *emphasis*.\n\n"
                  for i in range(20_000))


@pytest.fixture
def service():
    svc = render_service.RenderService()
    try:
        yield svc
    finally:
        svc.shutdown()


@pytest.fixture(autouse=True)
def _clean_global_service():
    yield
    render_service.shutdown()
    mc._CONVERT_CACHE.clear()


def test_child_render_matches_in_process_render(service):
    text = "# Title\n\n```python\nx = 1\n```\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"

    body, error = service.render(text=text)

    assert error is None
    assert body.body == mc.render_body(text).body
    assert body.headings == mc.render_body(text).headings
    assert service.spawned == 1


def test_child_process_is_reused_between_requests(service):
    service.render(text="# one")
    service.render(text="# two")

    assert service.spawned == 1
    assert service.remote_renders == 2


def test_worker_is_recycled_after_its_request_budget(service, monkeypatch):
    monkeypatch.setattr(render_service, "_MAX_REQUESTS_PER_WORKER", 1)

    service.render(text="# one")
    service.render(text="# two")

    assert service.spawned >= 2
    assert service.killed >= 1


def test_cancel_kills_the_running_child_and_raises(service):
    cancel = threading.Event()
    service.render(text="# warm up")  # pay the spawn cost first
    worker = service._idle
    assert worker is not None
    threading.Timer(0.2, cancel.set).start()

    t0 = time.monotonic()
    with pytest.raises(render_service.RenderWorkerCancelled):
        service.render(text=BIG_DOC, cancel=cancel)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0  # did not wait for the parse to finish
    assert worker.proc.poll() is not None  # the parse really was stopped
    assert service.killed >= 1


def test_cancel_before_the_request_never_starts_a_parse(service):
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(render_service.RenderWorkerCancelled):
        service.render(text=BIG_DOC, cancel=cancel)

    assert service.remote_renders == 0


def test_request_timeout_reports_a_worker_error(service, monkeypatch):
    service.render(text="# warm up")
    monkeypatch.setattr(render_service, "_REQUEST_TIMEOUT_S", 0.001)

    with pytest.raises(render_service.RenderWorkerError):
        service.render(text=BIG_DOC)


def test_a_crashed_child_is_replaced_on_the_next_request(service):
    service.render(text="# one")
    service._idle.proc.kill()
    service._idle.proc.wait(timeout=5)

    body, error = service.render(text="# two")

    assert error is None and body is not None
    assert service.spawned >= 2


def test_shutdown_leaves_no_child_process_behind(service):
    service.render(text="# one")
    proc = service._idle.proc

    service.shutdown()

    assert proc.poll() is not None
    assert service._live == set()


def test_unreadable_file_reports_an_error_instead_of_crashing(service, tmp_path):
    path = tmp_path / "missing.md"

    body, error = service.render(path=path)

    assert body is None
    assert "無法讀取檔案" in error


def test_disabled_by_environment_keeps_everything_in_process(monkeypatch, tmp_path):
    monkeypatch.setenv(render_service.DISABLE_ENV, "1")
    path = tmp_path / "big.md"
    path.write_text("# Heading\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)
    calls = []
    monkeypatch.setattr(render_service, "render_remote",
                        lambda **kw: calls.append(kw))

    assert mc.convert_body(path) is not None
    assert calls == []


def test_large_file_conversion_goes_through_the_child_process(monkeypatch, tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# Remote\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)
    monkeypatch.setattr(mc, "render_body", lambda *a, **kw: pytest.fail(
        "a large document must not be parsed in the GUI process"))

    body = mc.convert_body(path)

    assert body is not None and "Remote" in body.body
    assert render_service.service().remote_renders >= 1


def test_small_file_conversion_stays_in_process(monkeypatch, tmp_path):
    path = tmp_path / "small.md"
    path.write_text("# Local\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(render_service, "render_remote",
                        lambda **kw: pytest.fail("small files must not spawn"))

    assert mc.convert_body(path) is not None


def test_conversion_falls_back_in_process_when_the_child_fails(monkeypatch, tmp_path, caplog):
    path = tmp_path / "big.md"
    path.write_text("# Fallback\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)

    def boom(**_kw):
        raise render_service.RenderWorkerError("child died")

    monkeypatch.setattr(render_service, "render_remote", boom)

    log_path = tmp_path / "fallback.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    mc.log.addHandler(handler)
    try:
        body = mc.convert_body(path)
    finally:
        mc.log.removeHandler(handler)
        handler.close()

    assert body is not None and "Fallback" in body.body
    assert "child died" in log_path.read_text(encoding="utf-8")
    assert "falling back to in-process parsing" in caplog.text
    assert any(record.exc_info for record in caplog.records)


def test_cancelled_child_render_surfaces_as_render_cancelled(monkeypatch, tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# Stale\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)

    def cancelled(**_kw):
        raise render_service.RenderWorkerCancelled()

    monkeypatch.setattr(render_service, "render_remote", cancelled)

    with pytest.raises(mc.RenderCancelled):
        mc.convert(path)


def test_worker_command_uses_the_frozen_executable_flag(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\App\MarkdownViewer.exe")

    cmd, env = render_service._worker_command(4321)

    assert cmd == [r"C:\App\MarkdownViewer.exe", "--render-worker", "4321"]
    assert env[render_service.DISABLE_ENV] == "1"  # no grandchildren


def test_source_worker_command_runs_the_module():
    cmd, env = render_service._worker_command(4321)

    assert cmd[:4] == [sys.executable, "-X", "utf8", "-m"]
    assert cmd[4] == "app.render_worker"
    assert str(ROOT) in env["PYTHONPATH"]


def test_the_token_never_appears_in_the_child_command_line():
    cmd, _env = render_service._worker_command(4321)

    # Any local process can read a command line, so only the port is public.
    assert all(len(arg) < 32 or not _looks_like_hex(arg) for arg in cmd)


def _looks_like_hex(value: str) -> bool:
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def test_a_wrong_token_is_rejected_without_unpickling_anything(monkeypatch):
    """A stranger that connects first must be killed before any pickle.loads."""
    svc = render_service.RenderService()
    impostor = (
        "import socket,sys;"
        "port=int(sys.argv[1]);"
        "s=socket.create_connection(('127.0.0.1', port));"
        "s.sendall(bytes(32));"          # 32 bytes that are not the token
        "s.recv(1)"
    )

    def fake_command(port):
        return [sys.executable, "-c", impostor, str(port)], dict(os.environ)

    monkeypatch.setattr(render_service, "_worker_command", fake_command)
    monkeypatch.setattr(render_service, "recv_frame", lambda *a, **kw: pytest.fail(
        "the parent must not read a frame from an unauthenticated peer"))
    try:
        with pytest.raises(render_service.RenderWorkerError) as excinfo:
            svc._spawn()
        assert "token mismatch" in str(excinfo.value)
        assert svc._live == set()
    finally:
        svc.shutdown()


def test_the_child_reads_its_token_from_stdin(service):
    body, error = service.render(text="# handshake")

    assert error is None and body is not None
    assert service._idle is not None  # the handshake succeeded


def test_frozen_style_argv_entry_point_serves_a_render():
    """`MarkdownViewer.exe --render-worker <port> <token>` is the frozen path.

    Packaging is not exercised here (see the upgrade note); this runs the same
    argv through main.py, which is what the frozen bootloader executes.
    """
    token = bytes(range(32))
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(60)
    port = server.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
         "--render-worker", str(port)],
        cwd=str(ROOT), stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        proc.stdin.write(token)
        proc.stdin.flush()
        proc.stdin.close()
        sock, _addr = server.accept()
        server.close()
        sock.settimeout(60)
        greeting = render_service._recv_exactly(sock, 32, None,
                                                time.monotonic() + 60)
        assert greeting == token
        render_service.send_frame(sock, {"op": "render_text", "text": "# Frozen"})
        reply = render_service.recv_frame(sock, None, time.monotonic() + 60)
        assert reply["ok"]
        assert reply["body"].headings == [(1, "Frozen", "frozen")]
        sock.close()
    finally:
        proc.kill()
        proc.wait(timeout=30)


SYNTAX_ZOO = (
    "---\ntitle: Zoo\ntags: [a, b]\n---\n\n"
    "# H1 dup\n\n## H1 dup\n\n"
    "Text with [[wikilink]], a [ref link][r], a footnote[^1] and $x^2$ math.\n\n"
    "> [!warning] Careful\n> callout body\n\n"
    "```python\ndef f():\n    return 1\n```\n\n"
    "```mermaid\ngraph TD;\nA-->B;\n```\n\n"
    "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
    "- [ ] task\n- [x] done\n\n"
    "$$\n\\int_0^1 x dx\n$$\n\n"
    "<details>\n<summary>more</summary>\n\nhidden\n\n</details>\n\n"
    "Term\n: definition\n\n"
    "[r]: https://example.invalid\n\n[^1]: the footnote body\n"
)


def test_child_output_is_identical_for_the_syntax_stress_document(service):
    body, error = service.render(text=SYNTAX_ZOO)
    local = mc.render_body(SYNTAX_ZOO)

    assert error is None
    assert body.body == local.body
    assert body.headings == local.headings
    assert (body.mermaid, body.code_copy, body.math) == (
        local.mermaid, local.code_copy, local.math)


def test_stress_file_rendered_through_the_child_matches_in_process(
    service, tmp_path, monkeypatch
):
    path = tmp_path / "zoo.md"
    path.write_text(SYNTAX_ZOO * 40, encoding="utf-8")
    expected = mc.render_body(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)
    mc._CONVERT_CACHE.clear()

    body = mc.convert_body(path)

    assert body.body == expected.body
    assert body.headings == expected.headings


def test_repeated_large_opens_recycle_worker_processes(tmp_path, monkeypatch):
    path = tmp_path / "doc.md"
    path.write_text("# Repeat\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)
    monkeypatch.setattr(render_service, "_MAX_REQUESTS_PER_WORKER", 5)
    svc = render_service.service()
    spawned_before = svc.spawned

    for i in range(20):
        mc._CONVERT_CACHE.clear()
        path.write_text(f"# Repeat {i}\n\nbody\n", encoding="utf-8")
        assert mc.convert_body(path) is not None

    # Bounded, not unbounded: one live worker at a time, recycled on budget.
    assert len(svc._live) <= 2
    assert 3 <= svc.spawned - spawned_before <= 8
    render_service.shutdown()
    assert svc._live == set()


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def test_busy_workers_and_prewarm_share_one_capacity_limit():
    svc = render_service.RenderService(max_workers=2)
    held = []
    results, failures = [], []
    try:
        held.extend((svc._take(), svc._take()))
        svc.prewarm()
        assert svc.spawned == 2
        assert len(svc._live) == 2
        assert not svc._prewarming

        def render_waiting():
            try:
                results.append(svc.render(text="# waiting"))
            except Exception as exc:
                failures.append(exc)

        pending = threading.Thread(target=render_waiting)
        pending.start()
        _wait_for(lambda: svc._waiting_foreground == 1)
        assert svc.spawned == 2
        assert not results
        svc._release(held.pop())
        pending.join(5)
        assert not pending.is_alive()
        assert not failures
        assert results[0][1] is None
        assert svc.spawned == 2
        assert len(svc._live) <= 2
    finally:
        for worker in held:
            svc._release(worker)
        svc.shutdown()


def test_waiting_request_cancels_without_starting_another_child():
    svc = render_service.RenderService(max_workers=1)
    cancel = threading.Event()
    caught = []
    try:
        held = svc._take()

        def request():
            try:
                svc.render(text="# stale", cancel=cancel)
            except render_service.RenderWorkerCancelled:
                caught.append("cancelled")

        thread = threading.Thread(target=request)
        thread.start()
        _wait_for(lambda: svc._waiting_foreground == 1)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert caught == ["cancelled"]
        assert svc.spawned == 1
        assert svc.remote_renders == 0
        assert svc.fallbacks == 0
        svc._release(held)
    finally:
        svc.shutdown()


def test_visible_request_takes_capacity_before_waiting_metadata():
    svc = render_service.RenderService(max_workers=1)
    order, failures = [], []
    try:
        held = svc._take()

        def request(label, metadata_only):
            try:
                worker = svc._take(metadata_only=metadata_only)
                order.append(label)
                svc._release(worker)
            except Exception as exc:
                failures.append(exc)

        metadata = threading.Thread(target=request, args=("metadata", True))
        visible = threading.Thread(target=request, args=("visible", False))
        metadata.start()
        visible.start()
        _wait_for(lambda: svc._waiting_foreground == 1)
        svc._release(held)
        visible.join(5)
        metadata.join(5)
        assert not failures
        assert order == ["visible", "metadata"]
    finally:
        svc.shutdown()


def test_shutdown_cancels_in_progress_prewarm_without_resurrecting_child(monkeypatch):
    svc = render_service.RenderService(max_workers=1)
    processes = []
    original_popen = subprocess.Popen

    def track_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(render_service.subprocess, "Popen", track_process)
    monkeypatch.setattr(render_service, "_worker_command", lambda _port: (
        [sys.executable, "-c", "import sys,time;sys.stdin.buffer.read(32);time.sleep(30)"],
        dict(os.environ),
    ))
    try:
        svc.prewarm()
        _wait_for(lambda: bool(processes))
        svc.shutdown()
        _wait_for(lambda: not svc._prewarming)
        assert processes[0].poll() is not None
        assert svc._live == set()
        assert svc._idle is None
        assert svc._spawning == 0
    finally:
        svc.shutdown()
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
