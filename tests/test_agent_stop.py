"""Stopping the embedded Agent Watchdog before an upgrade or an uninstall.

HTTP and the process table are injected fakes: no request leaves the test and
no foreign process is ever signalled.  The one real-process test only looks
at (and ends) a child this test started itself.
"""

import json
import os
import socket
import subprocess
import sys
import threading
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
    """The recorded service.  With *leaves* it no longer answers once it has
    obeyed a shutdown request; by default it keeps answering (only its
    process goes away)."""

    def __init__(self, processes, boot=BOOT, obeys=True, accepts=True, leaves=False):
        self.processes = processes
        self.boot = boot
        self.obeys = obeys
        self.accepts = accepts
        self.leaves = leaves
        self.gone = False
        self.calls = []

    def boot_id(self, base):
        self.calls.append(("boot_id", base))
        return None if self.gone else self.boot

    def shutdown(self, base):
        self.calls.append(("shutdown", base))
        if self.accepts and self.obeys:
            self.processes.table.pop(PID, None)
            self.gone = self.leaves
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


def run(home, processes=None, http=None, own_exe=OWN, own_pid=1, now=None):
    processes = FakeProcesses() if processes is None else processes
    http = FakeHttp(processes) if http is None else http
    clock = Clock()
    extra = {} if now is None else {"now": now}
    code = st.stop_embedded(home, own_exe=own_exe, http=http, processes=processes,
                            clock=clock, sleep=clock.sleep, own_pid=own_pid,
                            embedded_command=st.first_argument_is_flag, **extra)
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


def test_never_stops_itself(tmp_path):
    write(tmp_path, endpoint())
    code, processes, http, _ = run(tmp_path, own_pid=PID)
    assert code == st.STOPPED and http.calls == [] and processes.kills == []


# --- a record that names no usable process ---------------------------------------
#
# No valid pid, a process that is gone, or a pid now owned by a younger
# process.  Nothing is ever ended by force then; only the service at the
# recorded address, if it still answers with the recorded boot_id, is asked
# to leave.  The process table always holds PID as this application's own
# Watchdog, so a wrong use of the pid would end it.

NO_PROCESS_PIDS = {"pid-missing": ..., "pid-null": None, "pid-string": "4321",
                   "pid-bool": True, "pid-zero": 0, "pid-negative": -5,
                   "pid-float": 4321.0, "pid-huge": 2**40}
NO_PROCESS = [*NO_PROCESS_PIDS, "dead", "reused"]


def no_process(kind, **changes):
    """(record, process table) for a record of the *kind* above."""
    if kind in NO_PROCESS_PIDS:
        return endpoint(pid=NO_PROCESS_PIDS[kind], **changes), FakeProcesses()
    if kind == "dead":
        processes = FakeProcesses()
        processes.table.clear()
        return endpoint(**changes), processes
    # the pid now belongs to a younger process, with the very same executable
    changes.setdefault("started_at", 1002.5)
    return endpoint(**changes), FakeProcesses(created=1002.5 + st.START_SLACK_S + 60)


@pytest.mark.parametrize("kind", NO_PROCESS)
def test_no_usable_process_and_nothing_answering_is_a_noop(tmp_path, kind):
    record, processes = no_process(kind)
    write(tmp_path, record)
    alive = PID in processes.table
    code, _, http, _ = run(tmp_path, processes, FakeHttp(processes, boot=None))
    assert code == st.STOPPED
    assert http.calls == [("boot_id", URL)]  # no shutdown request
    assert processes.kills == [] and (PID in processes.table) == alive


@pytest.mark.parametrize("kind", NO_PROCESS)
def test_no_usable_process_but_its_service_answers_it_is_asked_to_leave(tmp_path, kind):
    record, processes = no_process(kind)
    write(tmp_path, record)
    code, _, http, _ = run(tmp_path, processes, FakeHttp(processes, leaves=True))
    assert http.calls[:2] == [("boot_id", URL), ("shutdown", URL)]
    # stopped means: that boot_id no longer answers there
    assert http.calls[2:] == [("boot_id", URL)]
    assert code == st.STOPPED and processes.kills == []


@pytest.mark.parametrize("kind", NO_PROCESS)
@pytest.mark.parametrize("http_kwargs", [{"obeys": False}, {"accepts": False}],
                         ids=["ignores-shutdown", "refuses-shutdown"])
def test_no_usable_process_and_a_service_that_stays_is_reported(
        tmp_path, kind, http_kwargs):
    record, processes = no_process(kind)
    write(tmp_path, record)
    alive = PID in processes.table
    code, _, http, clock = run(tmp_path, processes, FakeHttp(processes, **http_kwargs))
    assert code == st.FAILED
    assert http.calls.count(("shutdown", URL)) == 1
    assert clock.now >= st.EXIT_WAIT_S  # it was waited for until the end
    assert processes.kills == [] and (PID in processes.table) == alive


@pytest.mark.parametrize("kind", NO_PROCESS)
def test_no_usable_process_and_another_service_is_never_asked(tmp_path, kind):
    record, processes = no_process(kind)
    write(tmp_path, record)
    alive = PID in processes.table
    http = FakeHttp(processes, boot="another-boot")
    code, _, _, _ = run(tmp_path, processes, http)
    assert http.calls == [("boot_id", URL)]
    assert code == st.STOPPED  # nothing of ours answers there
    assert processes.kills == [] and (PID in processes.table) == alive


@pytest.mark.parametrize("kind", NO_PROCESS)
def test_no_usable_process_and_an_unclear_answer_is_reported(tmp_path, kind):
    record, processes = no_process(kind)
    write(tmp_path, record)
    http = FakeHttp(processes, boot=st.NO_REPLY)
    code, _, _, _ = run(tmp_path, processes, http)
    assert http.calls == [("boot_id", URL)]  # never asked to leave
    assert code == st.FAILED and processes.kills == []


@pytest.mark.parametrize("kind", NO_PROCESS)
@pytest.mark.parametrize("changes", [{"boot_id": ...}, {"url": "http://192.168.1.5:18080"}],
                         ids=["no-boot-id", "not-loopback"])
def test_no_usable_process_and_no_address_to_ask_is_a_noop(tmp_path, kind, changes):
    record, processes = no_process(kind, **changes)
    write(tmp_path, record)
    alive = PID in processes.table
    code, _, http, _ = run(tmp_path, processes)
    assert code == st.STOPPED and http.calls == []
    assert processes.kills == [] and (PID in processes.table) == alive


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


def _digits(zero, text="18080"):
    """*text* written with the digits whose zero is the code point *zero*."""
    return "".join(chr(zero + int(digit)) for digit in text)


@pytest.mark.parametrize("port", [
    _digits(0xFF10), _digits(0x0660), _digits(0x0966), _digits(0x1D7CE),
    "1" + _digits(0xFF10, "8080"),
], ids=["full-width", "arabic-indic", "devanagari", "mathematical-bold", "mixed"])
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_port_counts_only_in_ascii_digits(tmp_path, host, port):
    """int() reads every kind of decimal digit, and so did a "\\d" pattern."""
    url = f"http://{host}:{port}"
    assert not st.is_loopback_url(url) and st.connect_target(url) is None
    write(tmp_path, endpoint(url=url))
    _, _, http, _ = run(tmp_path)
    assert http.calls == []


def test_connections_always_go_to_127_0_0_1():
    """"localhost" would be resolved to ::1 first, and that attempt waited for."""
    assert st.connect_target("http://localhost:18080") == (
        "127.0.0.1", 18080, "localhost:18080")
    assert st.connect_target("http://127.0.0.1:18080/") == (
        "127.0.0.1", 18080, "127.0.0.1:18080")
    for base in ("http://[::1]:18080", "http://0.0.0.0:18080", "http://localhost:0",
                 "http://localhost", "https://localhost:18080", None):
        assert st.connect_target(base) is None


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


WALL = 2000.0  # the wall clock of the start-time tests


@pytest.mark.parametrize("started", [
    float("nan"), float("inf"), float("-inf"), 1e308, 10**400,
    WALL + st.START_SLACK_S + 1,
], ids=["nan", "infinity", "minus-infinity", "huge", "beyond-float", "future"])
@pytest.mark.parametrize("obeys", [False, None], ids=["ignores-shutdown", "not-asked"])
def test_no_force_with_a_start_time_that_proves_nothing(tmp_path, started, obeys):
    """NaN is neither older nor younger than anything, and a start in the
    future is "older" than every process: either would let a reused pid pass
    as the recorded process.  (json reads NaN and Infinity back.)"""
    record = endpoint(started_at=started)
    if obeys is None:
        record.pop("boot_id")  # the service is never asked
    write(tmp_path, record)
    processes = FakeProcesses(created=1000.0)
    http = FakeHttp(processes, obeys=False)
    code, _, _, _ = run(tmp_path, processes, http, now=lambda: WALL)
    assert processes.kills == [] and PID in processes.table
    assert code == st.FAILED


def test_start_time_within_the_slack_of_now_still_proves_the_age(tmp_path):
    write(tmp_path, endpoint(started_at=WALL + st.START_SLACK_S - 1))
    processes = FakeProcesses(created=1000.0)
    http = FakeHttp(processes, obeys=False)
    code, _, _, _ = run(tmp_path, processes, http, now=lambda: WALL)
    assert processes.kills == [PID] and code == st.STOPPED


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
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    http = st.Http()
    assert http.boot_id(base) is None and http.shutdown(base) is False


# --- real requests, to a server of this test only ------------------------------------

DRIP_GAP_S = 0.05
#: Far beyond any bound: a stop that waited for such a reply would fail.
DRIP_FOR_S = 30.0


def http_reply(body, status="200 OK"):
    head = (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
    return head.encode("ascii") + body


BOOT_REPLY = http_reply(json.dumps({"boot_id": BOOT}).encode("ascii"))
#: A reply whose header never ends, and one whose body never ends.
DRIP_HEAD = ("drip", b"HTTP/1.1 200 OK\r\nX-Pad: ")
DRIP_BODY = ("drip", b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")


class LoopbackServer:
    """A raw HTTP server on a random 127.0.0.1 port, for one test.

    ``reply(index, request_line)`` decides the answer to each connection in
    turn: bytes are sent at once; a DRIP_* reply sends its head and then one
    byte every DRIP_GAP_S, for DRIP_FOR_S or until the server is closed.
    """

    def __init__(self, reply):
        self.reply = reply
        self.requests = []  # (request line without the version, Host header)
        self._count = 0
        self._closed = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.1)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}"
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._closed.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            index, self._count = self._count, self._count + 1
            threading.Thread(target=self._handle, args=(conn, index), daemon=True).start()

    def _handle(self, conn, index):
        with conn:
            try:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                lines = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
                host = next((line.split(":", 1)[1].strip() for line in lines[1:]
                             if line.lower().startswith("host:")), None)
                self.requests.append((lines[0].rsplit(" ", 1)[0], host))
                answer = self.reply(index, lines[0])
                if isinstance(answer, tuple):
                    conn.sendall(answer[1])
                    end = time.monotonic() + DRIP_FOR_S
                    while time.monotonic() < end and not self._closed.wait(DRIP_GAP_S):
                        conn.sendall(b"a")
                else:
                    conn.sendall(answer)
            except OSError:
                pass  # the client gave up

    def close(self):
        self._closed.set()
        self._sock.close()
        self._thread.join(5)


@pytest.fixture
def loopback_server():
    servers = []

    def start(reply):
        server = LoopbackServer(reply)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


@pytest.mark.parametrize("drip", [DRIP_HEAD, DRIP_BODY], ids=["head", "body"])
@pytest.mark.parametrize("kind", ["process", "no-process"])
def test_a_slowly_answering_service_cannot_stretch_the_stop(
        tmp_path, loopback_server, drip, kind):
    """It confirms its boot_id at once and then sends every other reply one
    byte at a time: a timeout per socket operation never runs out on that."""
    server = loopback_server(lambda index, line: BOOT_REPLY if index == 0 else drip)
    changes = {"url": server.url}
    if kind == "no-process":
        changes["pid"] = None
    write(tmp_path, endpoint(**changes))
    processes = FakeProcesses(dies_on_kill=False)  # never goes: the longest way
    started = time.monotonic()
    code = st.stop_embedded(tmp_path, own_exe=OWN, processes=processes, own_pid=1,
                            embedded_command=st.first_argument_is_flag)
    elapsed = time.monotonic() - started
    assert elapsed <= st.MAX_TOTAL_S + 0.5
    assert code == st.FAILED
    assert [line for line, _ in server.requests[:2]] == [
        "GET " + st.EVENTS_PATH, "POST " + st.SHUTDOWN_PATH]
    assert processes.kills == ([PID] if kind == "process" else [])


@pytest.mark.parametrize("drip", [DRIP_HEAD, DRIP_BODY], ids=["head", "body"])
def test_one_request_ends_at_its_deadline_however_slow_the_reply(loopback_server, drip):
    server = loopback_server(lambda index, line: drip)
    started = time.monotonic()
    assert st.Http().boot_id(server.url) is st.NO_REPLY
    assert time.monotonic() - started < st.HTTP_TIMEOUT_S + 0.5
    # the deadline of the whole stop wins over the time of one request
    started = time.monotonic()
    assert st.Http(deadline=time.monotonic() + 0.2).shutdown(server.url) is False
    assert time.monotonic() - started < 0.2 + 0.4
    # and once it has passed nothing is sent at all
    count = len(server.requests)
    assert st.Http(deadline=time.monotonic() - 1).boot_id(server.url) is st.NO_REPLY
    assert st.Http(deadline=time.monotonic() - 1).shutdown(server.url) is False
    time.sleep(0.2)
    assert len(server.requests) == count


def test_an_oversized_reply_is_no_answer(loopback_server):
    body = json.dumps({"boot_id": BOOT}).encode("ascii")
    exact = body + b" " * (st.EVENTS_MAX_BYTES - len(body))  # still valid JSON
    replies = [http_reply(exact), http_reply(exact + b" "),
               http_reply(b"{}" + b" " * st.SHUTDOWN_MAX_BYTES)]
    server = loopback_server(lambda index, line: replies[index])
    http = st.Http()
    assert http.boot_id(server.url) == BOOT              # exactly at the limit
    assert http.boot_id(server.url) is st.NO_REPLY       # one byte more
    assert http.shutdown(server.url) is False            # a confirmation that long
    assert len(server.requests) == 3


def test_localhost_is_reached_at_127_0_0_1_without_trying_ipv6(loopback_server):
    server = loopback_server(lambda index, line: BOOT_REPLY)
    port = server.url.rsplit(":", 1)[1]
    started = time.monotonic()
    assert st.Http().boot_id(f"http://localhost:{port}") == BOOT
    assert time.monotonic() - started < 0.5  # no attempt at ::1 waited for first
    assert server.requests == [("GET " + st.EVENTS_PATH, f"localhost:{port}")]


def test_a_data_folder_that_does_not_answer_is_given_up_on(tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(st, "MAX_TOTAL_S", 0.3)
    monkeypatch.setattr(st, "read_endpoint",
                        lambda home: release.wait(DRIP_FOR_S) and endpoint())
    processes = FakeProcesses()
    http = FakeHttp(processes)
    started = time.monotonic()
    try:
        code = st.stop_embedded(tmp_path, own_exe=OWN, http=http, processes=processes,
                                own_pid=1, embedded_command=st.first_argument_is_flag)
        assert time.monotonic() - started < 0.3 + 0.5
    finally:
        release.set()
    assert code == st.FAILED and http.calls == [] and processes.kills == []


def test_main_gives_up_on_a_stop_that_hangs(monkeypatch):
    """The installer waits for --agent-watchdog-stop without a limit of its own."""
    release = threading.Event()
    monkeypatch.setattr(st, "MAX_TOTAL_S", 0.2)
    monkeypatch.setattr(st, "stop_embedded",
                        lambda: st.STOPPED if release.wait(DRIP_FOR_S) else st.STOPPED)
    started = time.monotonic()
    try:
        assert st.main() == st.FAILED
        assert time.monotonic() - started < 0.2 + st.MAIN_GRACE_S + 0.5
    finally:
        release.set()


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
