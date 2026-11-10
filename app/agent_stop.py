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

The whole sequence is bounded by :data:`MAX_TOTAL_S`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
import urllib.request
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
HTTP_TIMEOUT_S = 1.0     # per request; there are at most two
EXIT_WAIT_S = 2.0        # after the shutdown request was sent
KILL_WAIT_S = 1.0        # after the process was ended by force
#: Upper bound of one stop_embedded() call (it may run on the GUI thread).
MAX_TOTAL_S = 2 * HTTP_TIMEOUT_S + EXIT_WAIT_S + KILL_WAIT_S
POLL_S = 0.1
COMMAND_LINE_MAX_BYTES = 1024 * 1024
#: A process created this long after endpoint.json was written cannot be the
#: one that wrote it: the pid was reused.
START_SLACK_S = 5.0

STOPPED = 0              # no embedded Watchdog is running (any more)
FAILED = 1               # one is still running

_LOOPBACK_URL = re.compile(r"http://(127\.0\.0\.1|localhost):(\d{1,5})/?")


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
        if path.stat().st_size > ENDPOINT_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _valid_pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value < 2**32 else None


# --- HTTP (loopback only, never through a proxy) ------------------------------


class Http:
    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def boot_id(self, base: str) -> str | None:
        """boot_id the service at *base* reports, or None."""
        try:
            with self._opener.open(base + EVENTS_PATH, timeout=HTTP_TIMEOUT_S) as resp:
                data = json.loads(resp.read(ENDPOINT_MAX_BYTES * 16).decode("utf-8"))
        except Exception:
            return None
        boot = data.get(KEY_BOOT_ID) if isinstance(data, dict) else None
        return boot if isinstance(boot, str) and boot else None

    def shutdown(self, base: str) -> bool:
        request = urllib.request.Request(
            base + SHUTDOWN_PATH, data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=HTTP_TIMEOUT_S) as resp:
                resp.read(4096)
                return resp.status == 200
        except Exception:
            return False


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
        if clock() >= deadline:
            return False
        sleep(POLL_S)


def _stop(home: Path, own_exe: str, http: Any, processes: Processes,
          clock: Callable[[], float], sleep: Callable[[float], None],
          own_pid: int, embedded_command: Callable[[list[str]], bool]) -> int:
    record = read_endpoint(home)
    if record is None or record.get(KEY_EMBEDDED) is not True:
        return STOPPED  # nothing embedded is registered: not ours to touch
    pid = _valid_pid(record.get(KEY_PID))
    if pid is None or pid == own_pid or not processes.alive(pid):
        return STOPPED  # stale record
    started = record.get(KEY_STARTED_AT)
    created = processes.started_at(pid)
    # Only a comparable pair of times can rule out a reused pid.
    age_known = False
    if (isinstance(started, (int, float)) and not isinstance(started, bool)
            and created is not None):
        age_known = True
        if created > started + START_SLACK_S:
            return STOPPED  # the pid now belongs to a younger, unrelated process

    url, boot = record.get(KEY_URL), record.get(KEY_BOOT_ID)
    verified = False  # the recorded service itself answered
    if is_loopback_url(url) and isinstance(boot, str) and boot:
        base = str(url).rstrip("/")
        # Only the service that wrote this record is asked to leave: another
        # program (or another Watchdog) may own the port by now.
        if http.boot_id(base) == boot:
            verified = True
            # The reply may get lost while the service is already leaving, so
            # its result is not trusted either way: the wait below decides.
            http.shutdown(base)
    if verified and _wait_gone(pid, processes, EXIT_WAIT_S, clock, sleep):
        return STOPPED

    # It did not leave by itself (or could not be asked).
    ours = same_file(processes.exe_path(pid), own_exe)
    arguments = processes.arguments(pid)
    if not verified:
        # Nothing answered as the recorded service; is the pid even ours?
        if not ours and processes.exe_path(pid) and own_exe:
            return STOPPED  # another program: the record is stale
        if ours and arguments is not None and not embedded_command(arguments):
            return STOPPED  # this application, but not its Watchdog (a window)
    # Force needs every proof: not a reused pid, this application's own
    # executable, and the bundled Watchdog's command line.
    may_force = (age_known and ours and arguments is not None
                 and embedded_command(arguments))
    if not may_force:
        if not processes.alive(pid):
            return STOPPED
        log.warning("Embedded Agent Watchdog (pid %d) could not be identified "
                    "beyond doubt; left running", pid)
        return FAILED
    processes.kill(pid)
    return STOPPED if _wait_gone(pid, processes, KILL_WAIT_S, clock, sleep) else FAILED


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
) -> int:
    """STOPPED (0) when no embedded Watchdog runs any more, else FAILED.

    STOPPED means exactly that; anything uncertain is FAILED.  Never raises.
    Bounded by MAX_TOTAL_S.
    """
    try:
        return _stop(
            data_home() if home is None else home,
            sys.executable if own_exe is None else own_exe,
            Http() if http is None else http,
            default_processes() if processes is None else processes,
            clock, sleep,
            os.getpid() if own_pid is None else own_pid,
            is_embedded_command if embedded_command is None else embedded_command,
        )
    except Exception:
        log.exception("Stopping the embedded Agent Watchdog failed")
        return FAILED


def main() -> int:
    """``--agent-watchdog-stop``: headless, silent, exit code only."""
    try:
        return stop_embedded()
    except BaseException:
        return FAILED
