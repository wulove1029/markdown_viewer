"""Stop the Agent Watchdog this application started from its bundled copy.

Used before an upgrade or an uninstall (``--agent-watchdog-stop``, run by the
installer) and before the in-app updater starts the installer: the bundled
Watchdog is this very executable running in the background, so it keeps the
installation folder locked.

Standard library only, no Qt.  Nothing here ever raises to the caller.

Only an *embedded* Watchdog is touched: endpoint.json must say
``"embedded": true``.  A Watchdog the user runs with their own Python never
writes that, so it is left alone, whatever port it listens on.

Steps: read endpoint.json (read-only) -> confirm the service at the recorded
loopback address is the one that wrote the file (same boot_id) -> POST
/api/shutdown -> wait for the process to go -> only if it does not, end that
process by force.  Force needs all of: the record's start time proves the pid
was not reused, the executable is this application's own, and the command
line is the bundled Watchdog's (``--agent-watchdog`` as the first argument).

When the record names no usable process (no valid pid, a dead one, a reused
one), nothing is ever ended by force; but a service that still answers at the
recorded address with the recorded boot_id is asked to leave all the same, and
"stopped" then means that this boot_id no longer answers there.  A record
that names the calling process itself is left alone.

The whole sequence is bounded by :data:`MAX_TOTAL_S`: one deadline runs
through every stage.  A request is cut off at its own deadline however slowly
the other side sends (the limit is on the request, not on each socket
operation), and the few file and process look-ups that could stall on a dead
network location are given up on when the time is over.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import logging
import math
import os
import re
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import agent_bundle

log = logging.getLogger(__name__)

ENDPOINT_NAME = "endpoint.json"
ENDPOINT_MAX_BYTES = 64 * 1024
KEY_EMBEDDED = "embedded"
KEY_PID = "pid"
KEY_URL = "url"
KEY_BOOT_ID = "boot_id"
KEY_STARTED_AT = "started_at"
EVENTS_PATH = "/api/events"
SHUTDOWN_PATH = "/api/shutdown"
HTTP_TIMEOUT_S = 1.0     # per request, from connecting to the last byte read
#: Largest reply body that is read; a longer one counts as no usable answer.
EVENTS_MAX_BYTES = 1024 * 1024
SHUTDOWN_MAX_BYTES = 4096
EXIT_WAIT_S = 2.0        # after the shutdown request was sent
KILL_WAIT_S = 1.0        # after the process was ended by force
#: Upper bound of one stop_embedded() call (it may run on the GUI thread).
MAX_TOTAL_S = 2 * HTTP_TIMEOUT_S + EXIT_WAIT_S + KILL_WAIT_S
POLL_S = 0.1
#: ``--agent-watchdog-stop`` gives up this long after the bound at the latest.
MAIN_GRACE_S = 0.5
COMMAND_LINE_MAX_BYTES = 1024 * 1024
#: A process created this long after endpoint.json was written cannot be the
#: one that wrote it: the pid was reused.
START_SLACK_S = 5.0

STOPPED = 0              # no embedded Watchdog is running (any more)
FAILED = 1               # one is still running

#: ASCII digits only: "\d" would also accept full-width and other digits.
_LOOPBACK_URL = re.compile(r"http://(127\.0\.0\.1|localhost):([0-9]{1,5})/?")
#: The one address a connection is ever opened to.
CONNECT_ADDRESS = "127.0.0.1"


def data_home() -> Path:
    override = os.environ.get("AIWATCH_HOME", "").strip()
    return Path(override) if override else Path.home() / ".aiwatch"


def is_loopback_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    match = _LOOPBACK_URL.fullmatch(url)  # "$" would let a trailing newline through
    return match is not None and 0 < int(match.group(2)) < 65536


def read_endpoint(home: Path) -> dict[str, Any] | None:
    """endpoint.json as a dict; None when missing, too big or not an object."""
    path = home / ENDPOINT_NAME
    try:
        # One bounded read: the size cannot change between a check and the read.
        with open(path, "rb") as handle:
            raw = handle.read(ENDPOINT_MAX_BYTES + 1)
        if len(raw) > ENDPOINT_MAX_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _valid_pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value < 2**32 else None


def _usable_start(value: object, wall_now: float) -> float | None:
    """The recorded start time when it can be compared with a process's
    creation time: a finite number that does not lie in the future."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number > wall_now + START_SLACK_S:
        return None
    return number


def _bounded(call: Callable[[], Any], seconds: float) -> tuple[bool, Any]:
    """(True, result) when *call* finished within *seconds*, else (False, None).

    Only for look-ups without side effects: one that is given up on keeps
    running on its daemon thread and its result is dropped.  What *call*
    raises is raised here.
    """
    if seconds <= 0:
        return False, None
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # handed to the caller below
            box["error"] = exc

    thread = threading.Thread(target=work, name="agent-stop-lookup", daemon=True)
    thread.start()
    thread.join(seconds)
    if "error" in box:
        raise box["error"]
    if "value" in box:
        return True, box["value"]
    return False, None


# --- HTTP (loopback only, never through a proxy) ------------------------------

#: Something accepted the connection but gave no usable answer in time.  Not
#: a string, so it never equals a recorded boot_id.
NO_REPLY: Any = object()


def connect_target(base: str) -> tuple[str, int, str] | None:
    """(address, port, Host header) for a loopback *base*, else None.

    "localhost" is connected to as 127.0.0.1: resolving the name would try
    ::1 first and wait for that attempt to fail.  The Host header keeps the
    spelling of the record.
    """
    match = _LOOPBACK_URL.fullmatch(base) if isinstance(base, str) else None
    if match is None or not 0 < int(match.group(2)) < 65536:
        return None  # the same test as is_loopback_url()
    port = int(match.group(2))
    return CONNECT_ADDRESS, port, f"{match.group(1)}:{port}"


class _DeadlineSocket(socket.socket):
    """A socket whose every operation ends at one absolute time.

    A plain socket timeout starts again with each operation, so a peer that
    sends one byte now and then would never run into it.
    """

    ends_at = 0.0

    def _arm(self) -> None:
        left = self.ends_at - time.monotonic()
        if left <= 0:
            raise TimeoutError("request deadline reached")
        self.settimeout(left)

    def connect(self, address: Any) -> None:
        self._arm()
        super().connect(address)

    def sendall(self, *args: Any, **kwargs: Any) -> None:
        self._arm()
        super().sendall(*args, **kwargs)

    def recv(self, *args: Any, **kwargs: Any) -> bytes:
        self._arm()
        return super().recv(*args, **kwargs)

    def recv_into(self, *args: Any, **kwargs: Any) -> int:
        self._arm()
        return super().recv_into(*args, **kwargs)


class Http:
    """Small requests, each finished or given up on within HTTP_TIMEOUT_S and
    never later than *deadline* (a value of *clock*).

    Everything happens on the calling thread: when a call returns, nothing is
    left behind that could still send a request later.
    """

    def __init__(self, deadline: float | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._deadline = deadline
        self._clock = clock

    def _budget(self) -> float:
        if self._deadline is None:
            return HTTP_TIMEOUT_S
        return min(HTTP_TIMEOUT_S, self._deadline - self._clock())

    def _request(self, base: str, method: str, path: str, body: bytes | None,
                 limit: int) -> Any:
        """(status, body); None when nothing listens there; else NO_REPLY."""
        target = connect_target(base)
        if target is None:
            return None
        budget = self._budget()
        if budget <= 0:
            return NO_REPLY
        address, port, host_header = target
        try:
            sock = _DeadlineSocket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return None
        response: http.client.HTTPResponse | None = None
        try:
            sock.ends_at = time.monotonic() + budget
            try:
                sock.connect((address, port))
            except OSError:
                # Refused, or not accepted in time: Windows reports a refused
                # loopback connection only after about two seconds of retries,
                # so that is what an address without a listener looks like.
                return None
            connection = http.client.HTTPConnection(address, port)
            connection.sock = sock
            headers = {"Host": host_header, "Connection": "close"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(limit + 1)
            if len(data) > limit:
                return NO_REPLY  # too long to be an answer of the Watchdog
            return response.status, data
        except Exception:
            return NO_REPLY
        finally:
            if response is not None:
                with contextlib.suppress(OSError):
                    response.close()
            with contextlib.suppress(OSError):
                sock.close()

    def boot_id(self, base: str) -> Any:
        """boot_id the service at *base* reports; None when nothing listens
        there or the answer has none; NO_REPLY when that cannot be told."""
        reply = self._request(base, "GET", EVENTS_PATH, None, EVENTS_MAX_BYTES)
        if reply is None or reply is NO_REPLY:
            return reply
        status, body = reply
        if status != 200:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError):
            return None
        boot = data.get(KEY_BOOT_ID) if isinstance(data, dict) else None
        return boot if isinstance(boot, str) and boot else None

    def shutdown(self, base: str) -> bool:
        """True when the service confirmed the request.  False does not mean
        that it was not sent: the caller waits for the outcome either way."""
        reply = self._request(base, "POST", SHUTDOWN_PATH, b"{}", SHUTDOWN_MAX_BYTES)
        return reply is not None and reply is not NO_REPLY and reply[0] == 200


# --- processes (no third-party dependency) --------------------------------------


class Processes:
    """Liveness, executable path, start time and forced end of one pid."""

    def alive(self, pid: int) -> bool:
        raise NotImplementedError

    def exe_path(self, pid: int) -> str | None:
        raise NotImplementedError

    def started_at(self, pid: int) -> float | None:
        """Creation time as a Unix timestamp, or None when unknown."""
        raise NotImplementedError

    def arguments(self, pid: int) -> list[str] | None:
        """The process's arguments after the program name, or None when unknown."""
        raise NotImplementedError

    def kill(self, pid: int) -> bool:
        raise NotImplementedError


class WindowsProcesses(Processes):
    _QUERY_LIMITED = 0x1000
    _SYNCHRONIZE = 0x00100000
    _TERMINATE = 0x0001
    _WAIT_TIMEOUT = 0x102
    _ERROR_ACCESS_DENIED = 5

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.LocalFree.argtypes = [wintypes.HLOCAL]
        self._k32 = k32
        ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long
        ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG)]
        self._ntdll = ntdll
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        shell32.CommandLineToArgvW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        self._shell32 = shell32

    def _open(self, pid: int, access: int) -> Any:
        return self._k32.OpenProcess(access, False, pid) or None

    def alive(self, pid: int) -> bool:
        handle = self._open(pid, self._QUERY_LIMITED | self._SYNCHRONIZE)
        if handle is None:
            # Access denied: it exists but is not ours to look at.
            return self._ctypes.get_last_error() == self._ERROR_ACCESS_DENIED
        try:
            return self._k32.WaitForSingleObject(handle, 0) == self._WAIT_TIMEOUT
        finally:
            self._k32.CloseHandle(handle)

    def exe_path(self, pid: int) -> str | None:
        handle = self._open(pid, self._QUERY_LIMITED)
        if handle is None:
            return None
        try:
            size = self._wintypes.DWORD(32768)
            buffer = self._ctypes.create_unicode_buffer(size.value)
            if not self._k32.QueryFullProcessImageNameW(
                    handle, 0, buffer, self._ctypes.byref(size)):
                return None
            return buffer.value or None
        finally:
            self._k32.CloseHandle(handle)

    def started_at(self, pid: int) -> float | None:
        handle = self._open(pid, self._QUERY_LIMITED)
        if handle is None:
            return None
        try:
            times = [self._wintypes.FILETIME() for _ in range(4)]
            if not self._k32.GetProcessTimes(
                    handle, *(self._ctypes.byref(t) for t in times)):
                return None
            created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return created / 10_000_000 - 11_644_473_600  # 1601 -> 1970
        finally:
            self._k32.CloseHandle(handle)

    _COMMAND_LINE_CLASS = 60  # ProcessCommandLineInformation

    def command_line(self, pid: int) -> str | None:
        import ctypes
        from ctypes import wintypes

        handle = self._open(pid, self._QUERY_LIMITED)
        if handle is None:
            return None
        try:
            size = wintypes.ULONG(0)
            self._ntdll.NtQueryInformationProcess(
                handle, self._COMMAND_LINE_CLASS, None, 0, ctypes.byref(size))
            if not 0 < size.value <= COMMAND_LINE_MAX_BYTES:
                return None
            buffer = ctypes.create_string_buffer(size.value)
            if self._ntdll.NtQueryInformationProcess(
                    handle, self._COMMAND_LINE_CLASS, buffer, size, ctypes.byref(size)):
                return None

            class UnicodeString(ctypes.Structure):
                _fields_ = [("Length", wintypes.USHORT),
                            ("MaximumLength", wintypes.USHORT),
                            ("Buffer", ctypes.c_void_p)]

            header = UnicodeString.from_buffer(buffer)
            if not header.Buffer or not header.Length:
                return None
            return ctypes.wstring_at(header.Buffer, header.Length // 2)
        finally:
            self._k32.CloseHandle(handle)

    def split_command_line(self, text: str) -> list[str] | None:
        count = self._ctypes.c_int(0)
        items = self._shell32.CommandLineToArgvW(text, self._ctypes.byref(count))
        if not items:
            return None
        try:
            return [items[i] for i in range(count.value)]
        finally:
            self._k32.LocalFree(items)

    def arguments(self, pid: int) -> list[str] | None:
        text = self.command_line(pid)
        if not text or not text.strip():
            return None
        argv = self.split_command_line(text)
        return None if not argv else argv[1:]

    def kill(self, pid: int) -> bool:
        handle = self._open(pid, self._TERMINATE)
        if handle is None:
            return False
        try:
            return bool(self._k32.TerminateProcess(handle, 1))
        finally:
            self._k32.CloseHandle(handle)


class PosixProcesses(Processes):
    def alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True  # exists, but not ours to signal
        return True

    def exe_path(self, pid: int) -> str | None:
        try:
            return os.readlink(f"/proc/{pid}/exe")  # Linux; elsewhere: unknown
        except OSError:
            return None

    def started_at(self, pid: int) -> float | None:
        try:
            return os.stat(f"/proc/{pid}").st_ctime
        except OSError:
            return None

    def arguments(self, pid: int) -> list[str] | None:
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        if not raw:
            return None
        return [part.decode("utf-8", "replace") for part in raw.rstrip(b"\0").split(b"\0")][1:]

    def kill(self, pid: int) -> bool:
        try:
            os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
        except OSError:
            return False
        return True


def default_processes() -> Processes:
    return WindowsProcesses() if sys.platform == "win32" else PosixProcesses()


def same_file(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return (os.path.normcase(os.path.realpath(a))
                == os.path.normcase(os.path.realpath(b)))
    except (OSError, ValueError):
        return False


def first_argument_is_flag(arguments: list[str]) -> bool:
    """The packaged application: ``MarkdownViewer.exe --agent-watchdog ...``."""
    return bool(arguments) and arguments[0].lower() == agent_bundle.CLI_FLAG


def is_embedded_command(arguments: list[str]) -> bool:
    """True when *arguments* are those of the bundled Watchdog entry point.

    Packaged: the flag is the first argument.  From source the interpreter
    comes first (``python [options] main.py --agent-watchdog``), so the flag
    must directly follow this checkout's main.py.
    """
    if agent_bundle.is_frozen():
        return first_argument_is_flag(arguments)
    lowered = [a.lower() for a in arguments]
    if agent_bundle.CLI_FLAG not in lowered:
        return False
    index = lowered.index(agent_bundle.CLI_FLAG)
    return index >= 1 and same_file(arguments[index - 1], agent_bundle.self_launch()[1])


# --- the stop sequence ------------------------------------------------------------


def _wait_gone(pid: int, processes: Processes, seconds: float,
               clock: Callable[[], float], sleep: Callable[[float], None]) -> bool:
    deadline = clock() + seconds
    while True:
        if not processes.alive(pid):
            return True
        left = deadline - clock()
        if left <= 0:
            return False
        sleep(min(POLL_S, left))


def _stop_service(base: str | None, boot: object, http: Any,
                  clock: Callable[[], float], sleep: Callable[[float], None],
                  deadline: float) -> int:
    """The record names no process that could be waited for (or ended).

    Nothing is ended by force here.  If the recorded service still answers it
    is asked to leave, and it is gone once its boot_id no longer answers.
    """
    if base is None:
        return STOPPED  # nowhere to ask: a stale record
    answer = http.boot_id(base)
    if answer is NO_REPLY:
        return FAILED  # something is there, and it may be the recorded service
    if answer != boot:
        return STOPPED  # nothing, or somebody else: a stale record
    http.shutdown(base)
    end = min(clock() + EXIT_WAIT_S, deadline)
    while True:
        answer = http.boot_id(base)
        if answer is not NO_REPLY and answer != boot:
            return STOPPED
        left = end - clock()
        if left <= 0:
            log.warning("Embedded Agent Watchdog at %s still answers; no process "
                        "of the record could be identified", base)
            return FAILED
        sleep(min(POLL_S, left))


def _stop(home: Path, own_exe: str, http: Any, processes: Processes,
          clock: Callable[[], float], sleep: Callable[[float], None],
          own_pid: int, embedded_command: Callable[[list[str]], bool],
          now: Callable[[], float], deadline: float) -> int:
    def left() -> float:
        return deadline - clock()

    read, record = _bounded(lambda: read_endpoint(home), left())
    if not read:
        return FAILED  # the data folder does not answer: nothing is known
    if record is None or record.get(KEY_EMBEDDED) is not True:
        return STOPPED  # nothing embedded is registered: not ours to touch
    url, boot = record.get(KEY_URL), record.get(KEY_BOOT_ID)
    base = None
    if is_loopback_url(url) and isinstance(boot, str) and boot:
        base = str(url).rstrip("/")
    pid = _valid_pid(record.get(KEY_PID))
    if pid is not None and pid == own_pid:
        # The record names this very process: either it wrote the record
        # itself, or the writer is gone and its pid was handed on to this
        # process.  No other process can answer with the recorded boot_id
        # then, and this one never asks itself to stop.
        return STOPPED
    if pid is None or not processes.alive(pid):
        return _stop_service(base, boot, http, clock, sleep, deadline)
    started = _usable_start(record.get(KEY_STARTED_AT), now())
    created = processes.started_at(pid)
    # Only a comparable pair of times can rule out a reused pid.
    age_known = started is not None and created is not None
    if started is not None and created is not None and created > started + START_SLACK_S:
        # The pid now belongs to a younger, unrelated process.
        return _stop_service(base, boot, http, clock, sleep, deadline)

    verified = False  # the recorded service itself answered
    # Only the service that wrote this record is asked to leave: another
    # program (or another Watchdog) may own the port by now.  A check that
    # ran out of time is not a match, so no request follows it.
    if base is not None and left() > 0 and http.boot_id(base) == boot:
        verified = True
        # The reply may get lost while the service is already leaving, and a
        # request that ran out of time may still have arrived, so its result
        # is not trusted either way: the wait below decides.
        http.shutdown(base)
    if verified and _wait_gone(pid, processes, min(EXIT_WAIT_S, left()), clock, sleep):
        return STOPPED

    # It did not leave by itself (or could not be asked).
    def identify() -> tuple[str | None, bool, list[str] | None, bool]:
        exe = processes.exe_path(pid)
        arguments = processes.arguments(pid)
        return (exe, same_file(exe, own_exe), arguments,
                arguments is not None and embedded_command(arguments))

    identified, facts = _bounded(identify, left())
    if not identified:
        if not processes.alive(pid):
            return STOPPED
        log.warning("Embedded Agent Watchdog (pid %d) could not be identified "
                    "in time; left running", pid)
        return FAILED
    exe, ours, arguments, embedded = facts
    if not verified:
        # Nothing answered as the recorded service; is the pid even ours?
        if not ours and exe and own_exe:
            return STOPPED  # another program: the record is stale
        if ours and arguments is not None and not embedded:
            return STOPPED  # this application, but not its Watchdog (a window)
    # Force needs every proof: not a reused pid, this application's own
    # executable, and the bundled Watchdog's command line.
    may_force = age_known and ours and embedded
    if not may_force:
        if not processes.alive(pid):
            return STOPPED
        log.warning("Embedded Agent Watchdog (pid %d) could not be identified "
                    "beyond doubt; left running", pid)
        return FAILED
    processes.kill(pid)
    wait = min(KILL_WAIT_S, max(left(), 0.0))
    return STOPPED if _wait_gone(pid, processes, wait, clock, sleep) else FAILED


def stop_embedded(
    home: Path | None = None,
    *,
    own_exe: str | None = None,
    http: Any = None,
    processes: Processes | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    own_pid: int | None = None,
    embedded_command: Callable[[list[str]], bool] | None = None,
    now: Callable[[], float] = time.time,
) -> int:
    """STOPPED (0) when no embedded Watchdog runs any more, else FAILED.

    STOPPED means exactly that; anything uncertain is FAILED.  Never raises.
    Returns within MAX_TOTAL_S of being called.
    """
    try:
        deadline = clock() + MAX_TOTAL_S
        return _stop(
            data_home() if home is None else home,
            sys.executable if own_exe is None else own_exe,
            Http(deadline, clock) if http is None else http,
            default_processes() if processes is None else processes,
            clock, sleep,
            os.getpid() if own_pid is None else own_pid,
            is_embedded_command if embedded_command is None else embedded_command,
            now, deadline,
        )
    except Exception:
        log.exception("Stopping the embedded Agent Watchdog failed")
        return FAILED


def main() -> int:
    """``--agent-watchdog-stop``: headless, silent, exit code only."""
    try:
        # The installer waits for this process without a limit of its own, so
        # the bound is enforced once more from outside: whatever the stop is
        # stuck in is left behind on a daemon thread and ends with the process.
        finished, code = _bounded(stop_embedded, MAX_TOTAL_S + MAIN_GRACE_S)
        return code if finished else FAILED
    except BaseException:
        return FAILED
