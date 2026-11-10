"""Stopping the embedded Agent Watchdog before an upgrade or an uninstall.

HTTP and the process table are injected fakes: no request leaves the test and
no foreign process is ever signalled.  The one real-process test only looks
at (and ends) a child this test started itself.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import agent_stop as st
from app import update_flow
from tests.test_update_flow import (  # noqa: F401
    _destroy_test_windows,
    _patch_launcher,
    _ready,
    _settings_factory,
    _Window,
)

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
OWN = r"C:\Apps\Viewer\MarkdownViewer.exe"
PID = 4321
BOOT = "boot-abc"
URL = "http://127.0.0.1:18080"


EMBEDDED_ARGS = ["--agent-watchdog"]


class FakeProcesses(st.Processes):
    def __init__(self, exe=OWN, created=1000.0, dies_on_kill=True, args=EMBEDDED_ARGS):
        self.table = {PID: {"exe": exe, "created": created, "args": args}}
        self.dies_on_kill = dies_on_kill
        self.kills = []

    def alive(self, pid):
        return pid in self.table

    def exe_path(self, pid):
        return self.table[pid]["exe"] if pid in self.table else None

    def started_at(self, pid):
        return self.table[pid]["created"] if pid in self.table else None

    def arguments(self, pid):
        args = self.table[pid]["args"] if pid in self.table else None
        return None if args is None else list(args)

    def kill(self, pid):
        self.kills.append(pid)
        if self.dies_on_kill:
            self.table.pop(pid, None)
        return self.dies_on_kill


class FakeHttp:
    def __init__(self, processes, boot=BOOT, obeys=True, accepts=True):
        self.processes = processes
        self.boot = boot
        self.obeys = obeys
        self.accepts = accepts
        self.calls = []

    def boot_id(self, base):
        self.calls.append(("boot_id", base))
        return self.boot

    def shutdown(self, base):
        self.calls.append(("shutdown", base))
        if self.accepts and self.obeys:
            self.processes.table.pop(PID, None)
        return self.accepts


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def endpoint(**changes):
    record = {"schema": 1, "port": 18080, "pid": PID, "boot_id": BOOT,
              "started_at": 1002.5, "machine": "m", "url": URL, "embedded": True}
    record.update(changes)
    return {k: v for k, v in record.items() if v is not ...}


def write(home, content):
    home.mkdir(exist_ok=True)
    path = home / st.ENDPOINT_NAME
    path.write_text(content if isinstance(content, str) else json.dumps(content),
                    encoding="utf-8")
    return path


def run(home, processes=None, http=None, own_exe=OWN, own_pid=1):
    processes = FakeProcesses() if processes is None else processes
    http = FakeHttp(processes) if http is None else http
    clock = Clock()
    code = st.stop_embedded(home, own_exe=own_exe, http=http, processes=processes,
                            clock=clock, sleep=clock.sleep, own_pid=own_pid,
                            embedded_command=st.first_argument_is_flag)
    return code, processes, http, clock


# --- endpoint.json decides whether anything happens ------------------------------


def test_no_endpoint_file_is_a_noop(tmp_path):
    code, processes, http, _ = run(tmp_path / "nowhere")
    assert code == st.STOPPED and http.calls == [] and processes.kills == []


@pytest.mark.parametrize("content", [
    "{not json", "[1, 2]", '"text"', "", "[" * 50000,
    json.dumps(dict(endpoint(), pad="x" * (st.ENDPOINT_MAX_BYTES + 1))),
], ids=["corrupt", "list", "string", "empty", "deeply-nested", "oversized"])
def test_unreadable_endpoint_file_is_a_noop(tmp_path, content):
    write(tmp_path, content)
    code, processes, http, _ = run(tmp_path)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []


@pytest.mark.parametrize("embedded", [..., False, None, "true", 1, 0],
                         ids=["missing", "false", "null", "string", "one", "zero"])
def test_only_embedded_true_is_ever_touched(tmp_path, embedded):
    """Today's Watchdog (and one the user runs with their own Python) does not
    say "embedded": it is never asked to stop and never ended."""
    write(tmp_path, endpoint(embedded=embedded))
    code, processes, http, _ = run(tmp_path)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []
    assert PID in processes.table


@pytest.mark.parametrize("pid", [..., None, "4321", True, 0, -5, 4321.0, 2**40])
def test_unusable_pid_is_a_noop(tmp_path, pid):
    write(tmp_path, endpoint(pid=pid))
    code, processes, http, _ = run(tmp_path)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []


def test_never_stops_itself(tmp_path):
    write(tmp_path, endpoint())
    code, processes, http, _ = run(tmp_path, own_pid=PID)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []


def test_stale_record_of_a_dead_process_is_a_noop(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses()
    processes.table.clear()
    code, _, http, _ = run(tmp_path, processes)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []


def test_reused_pid_of_a_younger_process_is_left_alone(tmp_path):
    write(tmp_path, endpoint(started_at=1002.5))
    processes = FakeProcesses(created=1002.5 + st.START_SLACK_S + 60)  # same exe!
    code, _, http, _ = run(tmp_path, processes)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []
    assert PID in processes.table


def test_endpoint_file_is_only_read(tmp_path):
    path = write(tmp_path, endpoint())
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    run(tmp_path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [st.ENDPOINT_NAME]


# --- the clean way out ------------------------------------------------------------


def test_shutdown_request_then_the_process_is_gone(tmp_path):
    write(tmp_path, endpoint(url=URL + "/"))
    code, processes, http, _ = run(tmp_path)
    assert code == st.STOPPED
    assert http.calls == [("boot_id", URL), ("shutdown", URL)]
    assert processes.kills == []  # it left by itself


@pytest.mark.parametrize("url", [
    "http://192.168.1.5:8787", "https://127.0.0.1:8787", "http://example.com:8787",
    "http://127.0.0.1.example.com:8787", "http://127.0.0.1:8787/x", "http://127.0.0.1",
    "http://127.0.0.1:0", "http://127.0.0.1:99999", "http://user@127.0.0.1:8787",
    "file:///c:/x", "", None, 5, ...,
])
def test_only_a_loopback_address_is_ever_contacted(tmp_path, url):
    write(tmp_path, endpoint(url=url))
    code, processes, http, _ = run(tmp_path)
    assert http.calls == []  # not even a GET
    # the process is this application's own executable, so it may still be ended
    assert code == st.STOPPED and processes.kills == [PID]


@pytest.mark.parametrize("url", ["http://127.0.0.1:18080", "http://localhost:18080/"])
def test_loopback_addresses_are_accepted(url):
    assert st.is_loopback_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18080\n", "http://127.0.0.1:18080/\n", "http://127.0.0.1:18080\r\n",
    "\nhttp://127.0.0.1:18080", " http://127.0.0.1:18080", "http://127.0.0.1:18080 ",
    "http://127.0.0.1:18080\nHost: evil", "HTTP://127.0.0.1:18080//",
])
def test_loopback_check_matches_the_whole_string(tmp_path, url):
    assert not st.is_loopback_url(url)
    write(tmp_path, endpoint(url=url))
    _, _, http, _ = run(tmp_path)
    assert http.calls == []


@pytest.mark.parametrize("answer", ["another-boot", None, ""])
def test_another_service_on_that_port_is_never_asked_to_stop(tmp_path, answer):
    """The port may belong to another program by now, or to the Watchdog the
    user runs themselves: only the service that wrote the record is asked."""
    write(tmp_path, endpoint())
    processes = FakeProcesses(exe=r"C:\Python\pythonw.exe")
    http = FakeHttp(processes, boot=answer)
    code, _, _, _ = run(tmp_path, processes, http)
    assert http.calls == [("boot_id", URL)]
    assert processes.kills == [] and PID in processes.table
    assert code == st.STOPPED  # a stale record: nothing of ours is running


def test_missing_boot_id_means_no_request(tmp_path):
    write(tmp_path, endpoint(boot_id=...))
    code, processes, http, _ = run(tmp_path)
    assert http.calls == [] and processes.kills == [PID]


# --- force, only against this application's own executable ---------------------------


def test_timeout_then_force_when_the_executable_is_ours(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses(exe=OWN.upper())  # same file, different spelling
    http = FakeHttp(processes, obeys=False)
    code, _, _, clock = run(tmp_path, processes, http)
    assert http.calls[-1] == ("shutdown", URL)
    assert clock.now >= st.EXIT_WAIT_S  # it was given its time first
    assert processes.kills == [PID] and code == st.STOPPED


@pytest.mark.parametrize("exe", [r"C:\Python313\pythonw.exe",
                                 r"C:\Other\Viewer\MarkdownViewer.exe", None, ""])
def test_timeout_without_force_when_the_executable_is_not_ours(tmp_path, exe):
    write(tmp_path, endpoint())
    processes = FakeProcesses(exe=exe)
    http = FakeHttp(processes, obeys=False)
    code, _, _, _ = run(tmp_path, processes, http)
    assert processes.kills == [] and PID in processes.table
    assert code == st.FAILED


def test_unknown_own_executable_never_forces(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses()
    http = FakeHttp(processes, obeys=False)
    code, _, _, _ = run(tmp_path, processes, http, own_exe="")
    assert processes.kills == [] and code == st.FAILED


class LostReplyHttp(FakeHttp):
    """The request arrives and is obeyed, but reading the reply fails."""

    def shutdown(self, base):
        self.calls.append(("shutdown", base))
        self.processes.table.pop(PID, None)
        return False


def test_lost_reply_to_an_obeyed_shutdown_is_still_a_clean_stop(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses()
    code, _, http, _ = run(tmp_path, processes, LostReplyHttp(processes))
    assert http.calls[-1] == ("shutdown", URL)
    assert code == st.STOPPED and processes.kills == []


@pytest.mark.parametrize("exe, args", [
    (r"C:\Python313\pythonw.exe", EMBEDDED_ARGS),   # verified service, foreign exe
    (OWN, None),                                    # command line unreadable
    (None, EMBEDDED_ARGS),                          # executable unreadable
])
def test_failed_shutdown_of_a_verified_service_is_never_reported_as_stopped(
        tmp_path, exe, args):
    """0 means "no embedded Watchdog runs any more" and nothing else."""
    write(tmp_path, endpoint())
    processes = FakeProcesses(exe=exe, args=args)
    http = FakeHttp(processes, accepts=False)
    code, _, _, clock = run(tmp_path, processes, http)
    assert clock.now >= st.EXIT_WAIT_S  # the request may have arrived: it waits
    assert processes.kills == [] and code == st.FAILED


def test_refused_shutdown_request_waits_and_then_forces_our_own_process(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses()
    http = FakeHttp(processes, accepts=False)
    code, _, _, clock = run(tmp_path, processes, http)
    assert clock.now >= st.EXIT_WAIT_S
    assert processes.kills == [PID] and code == st.STOPPED


# --- force needs every proof ---------------------------------------------------------


@pytest.mark.parametrize("started", [..., None, "1002.5", True, [1002.5]],
                         ids=["missing", "null", "string", "bool", "list"])
@pytest.mark.parametrize("obeys", [False, None], ids=["ignores-shutdown", "not-asked"])
def test_no_force_without_a_usable_start_time(tmp_path, started, obeys):
    """Without started_at a reused pid cannot be ruled out: the same executable
    may be one of the application's windows."""
    record = endpoint(started_at=started)
    if obeys is None:
        record.pop("boot_id")  # the service is never asked
    write(tmp_path, record)
    processes = FakeProcesses()
    http = FakeHttp(processes, obeys=False)
    code, _, _, _ = run(tmp_path, processes, http)
    assert processes.kills == [] and PID in processes.table
    assert code == st.FAILED


def test_no_force_when_the_process_start_time_is_unknown(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses(created=None)
    code, _, _, _ = run(tmp_path, processes, FakeHttp(processes, obeys=False))
    assert processes.kills == [] and code == st.FAILED


@pytest.mark.parametrize("args", [
    [], [r"C:\docs\note.md"], [r"C:\docs\ --agent-watchdog .md"],
    ["--render-worker"], ["--agent-watchdog-stop"], [r"C:\x.md", "--agent-watchdog"],
    ["--agent-watchdogs"],
])
def test_no_force_against_this_applications_other_processes(tmp_path, args):
    """Same executable, same start-time window, but not the Watchdog: a window,
    a render worker, another stop command."""
    write(tmp_path, endpoint())
    processes = FakeProcesses(args=args)
    # verified service that ignores the request
    code, _, _, _ = run(tmp_path, processes, FakeHttp(processes, obeys=False))
    assert processes.kills == [] and code == st.FAILED
    # nothing answers as the recorded service: the record is simply stale
    code, _, _, _ = run(tmp_path, processes, FakeHttp(processes, boot=None))
    assert processes.kills == [] and code == st.STOPPED and PID in processes.table


def test_no_force_when_the_command_line_is_unreadable(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses(args=None)
    code, _, _, _ = run(tmp_path, processes, FakeHttp(processes, boot=None))
    assert processes.kills == [] and code == st.FAILED


def test_force_accepts_the_flag_in_any_case_with_further_arguments(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses(args=["--Agent-Watchdog", "--serve", "--background"])
    code, _, _, _ = run(tmp_path, processes, FakeHttp(processes, obeys=False))
    assert processes.kills == [PID] and code == st.STOPPED


def test_embedded_command_rules(monkeypatch):
    assert st.first_argument_is_flag(["--agent-watchdog"])
    assert not st.first_argument_is_flag([])
    assert not st.first_argument_is_flag(["x", "--agent-watchdog"])
    main_py = str(MAIN)
    # from source: python [options] main.py --agent-watchdog
    assert st.is_embedded_command(["-X", "utf8", main_py, "--agent-watchdog"])
    assert st.is_embedded_command(["-B", main_py.upper(), "--AGENT-WATCHDOG", "--serve"])
    assert not st.is_embedded_command(["--agent-watchdog"])
    assert not st.is_embedded_command([main_py, "doc.md", "--agent-watchdog"])
    assert not st.is_embedded_command([r"C:\other\main.py", "--agent-watchdog"])
    assert not st.is_embedded_command([main_py, "--agent-watchdog-stop"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert st.is_embedded_command(["--agent-watchdog", "--serve"])
    assert not st.is_embedded_command(["-X", "utf8", main_py, "--agent-watchdog"])


def test_the_whole_sequence_is_bounded():
    assert st.MAX_TOTAL_S <= 5.0
    assert st.MAX_TOTAL_S == 2 * st.HTTP_TIMEOUT_S + st.EXIT_WAIT_S + st.KILL_WAIT_S


def test_worst_case_takes_no_longer_than_the_bound(tmp_path):
    class SlowHttp(FakeHttp):
        def __init__(self, processes, clock):
            super().__init__(processes, obeys=False)
            self.clock = clock

        def boot_id(self, base):
            self.clock.sleep(st.HTTP_TIMEOUT_S)
            return super().boot_id(base)

        def shutdown(self, base):
            self.clock.sleep(st.HTTP_TIMEOUT_S)
            return super().shutdown(base)

    write(tmp_path, endpoint())
    processes = FakeProcesses(dies_on_kill=False)
    clock = Clock()
    code = st.stop_embedded(
        tmp_path, own_exe=OWN, http=SlowHttp(processes, clock), processes=processes,
        clock=clock, sleep=clock.sleep, own_pid=1,
        embedded_command=st.first_argument_is_flag)
    assert code == st.FAILED and processes.kills == [PID]
    assert clock.now <= st.MAX_TOTAL_S + 2 * st.POLL_S


def test_process_that_survives_force_is_reported(tmp_path):
    write(tmp_path, endpoint())
    processes = FakeProcesses(dies_on_kill=False)
    http = FakeHttp(processes, obeys=False)
    code, _, _, clock = run(tmp_path, processes, http)
    assert processes.kills == [PID] and code == st.FAILED
    assert clock.now >= st.EXIT_WAIT_S + st.KILL_WAIT_S


# --- nothing escapes ----------------------------------------------------------------


class Exploding:
    def __getattr__(self, name):
        def boom(*args, **kwargs):
            raise RuntimeError(f"{name} exploded")
        return boom


def test_exceptions_never_reach_the_caller(tmp_path):
    write(tmp_path, endpoint())
    assert run(tmp_path, processes=Exploding())[0] == st.FAILED
    processes = FakeProcesses()
    assert run(tmp_path, processes, http=Exploding())[0] == st.FAILED
    assert processes.kills == []


def test_main_swallows_everything(monkeypatch):
    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(st, "stop_embedded", boom)
    assert st.main() == st.FAILED


def test_real_http_helper_fails_quietly_on_a_dead_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    http = st.Http()
    assert http.boot_id(base) is None and http.shutdown(base) is False


# --- the real process table, on a child of this test only -----------------------------


def test_real_process_queries_on_our_own_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    processes = st.default_processes()
    try:
        assert processes.alive(child.pid)
        if sys.platform == "win32":
            assert st.same_file(processes.exe_path(child.pid), sys.executable)
            assert abs(processes.started_at(child.pid) - time.time()) < 60
            assert processes.arguments(child.pid) == ["-c", "import time; time.sleep(120)"]
        assert processes.kill(child.pid)
        child.wait(timeout=20)
        assert not processes.alive(child.pid)
        assert processes.exe_path(child.pid) is None or sys.platform != "win32"
        assert processes.arguments(child.pid) is None or sys.platform != "win32"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=20)


def test_same_file_ignores_case_and_rejects_unknowns(tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_text("", encoding="utf-8")
    assert st.same_file(str(exe), str(exe))
    if sys.platform == "win32":
        assert st.same_file(str(exe).upper(), str(exe).lower())
    assert not st.same_file(str(exe), str(tmp_path / "Other.exe"))
    assert not st.same_file(None, str(exe)) and not st.same_file(str(exe), "")


# --- entry points ---------------------------------------------------------------------


def _run_main(home):
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    env["AIWATCH_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(MAIN), "--agent-watchdog-stop"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)


def test_main_py_stop_is_headless_and_a_noop_without_an_embedded_watchdog(tmp_path):
    result = _run_main(tmp_path / "empty")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    # a record like today's Watchdog writes (no "embedded"), naming a live process
    write(tmp_path, endpoint(embedded=..., pid=os.getpid(), url="http://127.0.0.1:9"))
    result = _run_main(tmp_path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_main_py_handles_the_stop_flag_before_qt():
    source = MAIN.read_text(encoding="utf-8")
    assert source.index('"--agent-watchdog-stop"') < source.index("from PySide6")
    assert source.index('"--agent-watchdog-stop"') < source.index("app.cli_export")
    stop_source = (ROOT / "app" / "agent_stop.py").read_text(encoding="utf-8")
    assert "PySide6" not in stop_source and "psutil" not in stop_source


# --- the in-app updater stops it before the installer starts ---------------------------


def test_updater_stops_the_embedded_watchdog_before_the_installer(
        qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch)
    order = []
    cursors = []

    def stop():
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        cursor = QGuiApplication.overrideCursor()
        cursors.append(cursor is not None
                       and cursor.shape() == Qt.CursorShape.WaitCursor)
        order.append("stop")
        return st.STOPPED

    monkeypatch.setattr(update_flow.agent_stop, "stop_embedded", stop)
    window = _Window()
    window._agent_watch_pause = lambda: order.append("pause")
    window._agent_watch_resume = lambda: order.append("resume")
    _ready(window, tmp_path, monkeypatch)
    real_start = update_flow.QProcess.startDetached
    update_flow.QProcess.startDetached = staticmethod(
        lambda *a: order.append("installer") or real_start(*a))
    try:
        update_flow.install_ready_update(window)
        assert order == ["pause", "stop", "installer"]
        assert len(launches) == 1 and quits == [1]
        from PySide6.QtGui import QGuiApplication

        assert cursors == [True]                           # busy while it waits
        assert QGuiApplication.overrideCursor() is None    # and restored after
    finally:
        window.close()


def test_updater_resumes_watching_when_the_installer_did_not_start(
        qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    launches, quits = _patch_launcher(monkeypatch, started=False)
    monkeypatch.setattr(update_flow.QMessageBox, "warning", lambda *a: None)
    order = []
    # a stop that failed must not keep the installer from being started
    monkeypatch.setattr(update_flow.agent_stop, "stop_embedded",
                        lambda: order.append("stop") or st.FAILED)
    window = _Window()
    window._agent_watch_pause = lambda: order.append("pause")
    window._agent_watch_resume = lambda: order.append("resume")
    _ready(window, tmp_path, monkeypatch)
    try:
        update_flow.install_ready_update(window)
        assert order == ["pause", "stop", "resume"]
        assert len(launches) == 1 and quits == []
    finally:
        window.close()


def test_updater_does_not_stop_anything_when_the_installer_is_invalid(
        qapp, tmp_path, monkeypatch):
    _settings_factory(tmp_path, monkeypatch)
    launches, _ = _patch_launcher(monkeypatch)
    monkeypatch.setattr(update_flow.QMessageBox, "warning", lambda *a: None)
    order = []
    monkeypatch.setattr(update_flow.agent_stop, "stop_embedded",
                        lambda: order.append("stop") or st.STOPPED)
    window = _Window()
    _ready(window, tmp_path, monkeypatch, valid=False)
    try:
        update_flow.install_ready_update(window)
        assert order == [] and launches == []
    finally:
        window.close()
