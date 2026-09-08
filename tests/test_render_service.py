"""The killable out-of-process Markdown renderer (spec 3.B).

These tests start real child processes: the whole point of the design is that
an in-flight parse can be stopped, which cannot be shown with a fake.
"""

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


def test_conversion_falls_back_in_process_when_the_child_fails(monkeypatch, tmp_path):
    path = tmp_path / "big.md"
    path.write_text("# Fallback\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(mc, "SUBPROCESS_MIN_BYTES", 1)

    def boom(**_kw):
        raise render_service.RenderWorkerError("child died")

    monkeypatch.setattr(render_service, "render_remote", boom)

    body = mc.convert_body(path)

    assert body is not None and "Fallback" in body.body


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

    cmd, env = render_service._worker_command(4321, "ab" * 32)

    assert cmd == [r"C:\App\MarkdownViewer.exe", "--render-worker", "4321", "ab" * 32]
    assert env[render_service.DISABLE_ENV] == "1"  # no grandchildren


def test_source_worker_command_runs_the_module():
    cmd, env = render_service._worker_command(4321, "cd" * 32)

    assert cmd[:4] == [sys.executable, "-X", "utf8", "-m"]
    assert cmd[4] == "app.render_worker"
    assert str(ROOT) in env["PYTHONPATH"]


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
         "--render-worker", str(port), token.hex()],
        cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
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
