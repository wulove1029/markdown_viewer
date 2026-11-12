"""The Agent Watchdog copy that may ship inside the application.

Standard library only and free of Qt: ``main.py`` imports this before
anything heavy to serve ``--agent-watchdog``, which runs the bundled
``aiwatch.py`` with the application's own interpreter (a packaged build has no
python.exe to hand it to).

The bundle is optional.  Every function treats "the directory is missing or
holds no aiwatch.py" as the normal case and reports it with ``None``.

Embedding interface (the Watchdog side may be older and ignore all of it):

* environment set before the Watchdog runs: ``AIWATCH_EMBEDDED=1``,
  ``AIWATCH_RESOURCES`` (the bundle directory) and ``AIWATCH_SELF_LAUNCH`` (a
  JSON array: the command that starts this entry point again).  AIWATCH_HOME
  is left exactly as the caller set it.
* ``aiwatch.main(argv)`` is called when the bundled file defines a ``main``
  that takes arguments; otherwise the file is run as a script with the same
  arguments in ``sys.argv``.
* only the arguments in :func:`arguments_allowed` are passed on; anything else
  (stopping, registering, switching auto-start, log paths ...) is refused, so
  this entry point can serve, or remember a new port with ``--set-port N``
  (alone; the Watchdog then moves a running copy there itself), and nothing
  more.
* when the Watchdog cannot start it may leave ``last_error.json`` in its data
  directory; :func:`read_last_error` reads it for the panel's diagnostics.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import runpy
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BUNDLE_DIR_NAME = "agent_watchdog"
SCRIPT_NAME = "aiwatch.py"
MODULE_NAME = "aiwatch"
ENTRY_FUNCTION = "main"
CLI_FLAG = "--agent-watchdog"
#: What the entry point runs when it is given no further arguments.
DEFAULT_ARGS = ("--serve", "--background")
#: Development / test / build-time only: a directory that holds aiwatch.py.
#: A packaged build ignores it and only ever runs the copy it ships.
ENV_OVERRIDE = "MDV_AGENT_WATCHDOG_DIR"
ENV_EMBEDDED = "AIWATCH_EMBEDDED"
ENV_RESOURCES = "AIWATCH_RESOURCES"
ENV_SELF_LAUNCH = "AIWATCH_SELF_LAUNCH"
#: Exit code of ``--agent-watchdog`` when there is no bundled copy.
EXIT_NO_BUNDLE = 3
#: Exit code when the bundled script died with an unexpected exception.
EXIT_CRASHED = 1
#: Exit code when an argument outside the allowlist was given.
EXIT_BAD_ARGUMENTS = 2
#: The Watchdog options this entry point passes on (exact spelling only: the
#: Watchdog's parser would also accept abbreviations of its other options).
_FLAG_ARGS = ("--serve", "--background")
_PORT_ARG = "--port"            # integer 0..65535
_IDLE_EXIT_ARG = "--idle-exit"  # seconds, a non-negative number
#: Only ever on its own: ``--set-port N`` or ``--set-port=N``, N in 1..65535.
SET_PORT_ARG = "--set-port"
#: ``__version__ = "1.3.0"`` near the top of the bundled aiwatch.py.
_VERSION_LINE = re.compile(r"""^__version__\s*=\s*["']([^"'\r\n]{1,40})["']""", re.MULTILINE)
#: Written here (not by the Watchdog) when the bundled code raised.
ERROR_LOG_NAME = "agent-watchdog-launch-error.log"
#: Written by the Watchdog when it could not start; removed once it does.
LAST_ERROR_NAME = "last_error.json"
LAST_ERROR_MAX_BYTES = 64 * 1024
LAST_ERROR_MAX_TEXT = 2000
#: Plain-language explanations of the Watchdog's start-up exit codes, shown
#: next to the raw code.  ``{home}`` is the resolved data directory.  Only
#: integer codes are explained; anything else is shown as it was reported.
LAST_ERROR_HINTS: dict[int, str] = {
    3: "預設的連接埠被其他程式佔用（不是 Agent 監看程式）。",
    4: "內建的 Agent 監看檔案不完整，請重新安裝 Markdown Viewer。",
    5: "無法寫入 Agent 監看的資料資料夾（{home}）。",
    6: "內建啟動參數不正確，請回報此問題。",
    7: "啟動時發生未預期的錯誤，請附上診斷資訊回報。",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Where the bundled copy would live (the directory may not exist)."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        root = Path(base) if base else Path(sys.executable).resolve().parent
        return root / BUNDLE_DIR_NAME
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override)
    return _REPO_ROOT / BUNDLE_DIR_NAME


def bundled_script() -> Path | None:
    """Absolute path of the bundled aiwatch.py, or None when there is none."""
    try:
        script = bundle_dir() / SCRIPT_NAME
        if script.is_file():
            return script.resolve()
    except OSError:
        pass
    return None


def self_launch() -> list[str]:
    """The command that reaches :func:`run_bundled` again (no further arguments)."""
    if is_frozen():
        return [str(Path(sys.executable).resolve()), CLI_FLAG]
    return [str(Path(sys.executable).resolve()), str(_REPO_ROOT / "main.py"), CLI_FLAG]


def bundled_launch() -> tuple[list[str], str] | None:
    """(argv, cwd) that starts the bundled copy in the background, or None.

    Only the flag is passed: what the Watchdog is asked to do is decided by
    the entry point (:data:`DEFAULT_ARGS`), not by the caller.
    """
    script = bundled_script()
    if script is None:
        return None
    if is_frozen():
        launch = [sys.executable, CLI_FLAG]
    else:
        launch = [sys.executable, "-X", "utf8", str(_REPO_ROOT / "main.py"), CLI_FLAG]
    return launch, str(script.parent)


def set_port_launch(port: int) -> tuple[list[str], str] | None:
    """(argv, cwd) that asks the bundled copy to remember *port*, or None.

    The same command as :func:`bundled_launch` followed by ``--set-port N``:
    ``<exe> --agent-watchdog --set-port N`` packaged, ``python -X utf8 main.py
    --agent-watchdog --set-port N`` from source.
    """
    launch = bundled_launch()
    if launch is None:
        return None
    argv, cwd = launch
    return [*argv, SET_PORT_ARG, str(int(port))], cwd


def script_version(script: Path | str) -> str | None:
    """The ``__version__ = "x.y.z"`` line of an aiwatch.py, read from its
    source: the file is never imported or run.  None when unreadable."""
    try:
        with open(script, encoding="utf-8-sig", errors="replace") as fh:
            head = fh.read(64 * 1024)
    except (OSError, ValueError):
        return None
    match = _VERSION_LINE.search(head)
    return match.group(1).strip() if match else None


def bundled_version() -> str | None:
    """The bundled copy's ``__version__`` (see :func:`script_version`)."""
    script = bundled_script()
    return None if script is None else script_version(script)


#: GetStdHandle's STD_INPUT_HANDLE / STD_OUTPUT_HANDLE / STD_ERROR_HANDLE.
_STD_HANDLE_IDS = {"stdin": -10, "stdout": -11, "stderr": -12}


def _std_handle(name: str) -> int | None:
    """The Windows standard handle this process inherited for *name*, when it
    is a usable file, pipe or console; None otherwise (also off Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_std_handle = kernel32.GetStdHandle
    get_std_handle.argtypes = [wintypes.DWORD]
    get_std_handle.restype = wintypes.HANDLE
    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = [wintypes.HANDLE]
    get_file_type.restype = wintypes.DWORD
    handle = get_std_handle(_STD_HANDLE_IDS[name] & 0xFFFFFFFF)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None  # none at all (started from Explorer), or INVALID_HANDLE_VALUE
    if get_file_type(handle) == 0:  # FILE_TYPE_UNKNOWN: not something to write to
        return None
    return int(handle)


def _attach_std_stream(name: str, mode: str):
    """A UTF-8, line-buffered text stream on the inherited handle, or None."""
    try:
        handle = _std_handle(name)
        if handle is None:
            return None
        import msvcrt

        flags = (os.O_RDONLY if mode == "r" else os.O_WRONLY) | getattr(os, "O_BINARY", 0)
        fd = msvcrt.open_osfhandle(handle, flags)
        # closefd=False: the standard handle stays open for the process itself.
        return open(fd, mode, encoding="utf-8", errors="backslashreplace",
                    buffering=1, closefd=False)
    except Exception:
        return None


def _ensure_std_streams() -> None:
    """A windowed packaged build has no console: sys.stdout / sys.stderr /
    sys.stdin are None there and the first print() would raise.

    A host that started this process with pipes (QProcess does) still gets
    the output: the inherited Windows handles are used when there are any,
    os.devnull only when there are none.  Streams that already exist (a run
    from source) are left alone.  Never raises.
    """
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name, None) is not None:
            continue
        stream = _attach_std_stream(name, mode)
        if stream is None:
            try:
                stream = open(os.devnull, mode, encoding="utf-8")
            except OSError:
                continue
        setattr(sys, name, stream)


def _data_home() -> Path:
    override = os.environ.get("AIWATCH_HOME", "").strip()
    return Path(override) if override else Path.home() / ".aiwatch"


def _record_crash(text: str) -> None:
    """Best effort: keep the last crash where the Watchdog keeps its own log
    (a missing module fails before the Watchdog can open that log)."""
    try:
        home = _data_home()
        home.mkdir(parents=True, exist_ok=True)
        (home / ERROR_LOG_NAME).write_text(text, encoding="utf-8")
    except OSError:
        pass


def accepts_argv(script: Path) -> bool:
    """True when the file defines a top-level ``main`` that takes arguments.

    Decided from the source, without importing it: importing first and then
    falling back to running the file would execute its module body twice.
    """
    try:
        tree = ast.parse(script.read_text(encoding="utf-8-sig"), filename=str(script))
    except (OSError, SyntaxError, ValueError):
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == ENTRY_FUNCTION:
            args = node.args
            return bool(args.posonlyargs or args.args or args.vararg)
    return False


def _is_port(text: str) -> bool:
    return text.isascii() and text.isdigit() and len(text) <= 5 and int(text) <= 65535


def _is_seconds(text: str) -> bool:
    # As strict as _is_port: float() alone would also take full-width digits
    # and digit-group underscores ("1_0").
    if not text.isascii() or "_" in text:
        return False
    try:
        value = float(text)
    except ValueError:
        return False
    return 0 <= value < float("inf") and text.strip() == text and len(text) <= 16


def _is_set_port(items: Sequence[object]) -> bool:
    """Exactly ``["--set-port", N]`` or ``["--set-port=N"]``, N in 1..65535."""
    if len(items) == 2 and items[0] == SET_PORT_ARG:
        value = items[1]
    elif len(items) == 1 and isinstance(items[0], str):
        name, equals, value = items[0].partition("=")
        if name != SET_PORT_ARG or not equals:
            return False
    else:
        return False
    return isinstance(value, str) and _is_port(value) and int(value) >= 1


def arguments_allowed(args: Sequence[str]) -> bool:
    """True when *args* only serve (--serve, --background, --port N,
    --idle-exit S) or are exactly ``--set-port N`` with nothing else."""
    values = {_PORT_ARG: _is_port, _IDLE_EXIT_ARG: _is_seconds}
    items = list(args)
    if _is_set_port(items):
        return True
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if not isinstance(item, str):
            return False
        if item in _FLAG_ARGS:
            continue
        name, equals, value = item.partition("=")
        if name not in values:
            return False
        if not equals:
            if index >= len(items) or not isinstance(items[index], str):
                return False
            value = items[index]
            index += 1
        if not values[name](value):
            return False
    return True


def _exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return int(value)
    try:
        print(value, file=sys.stderr)
    except (AttributeError, OSError, ValueError):
        pass
    return 1


def run_bundled(args: Sequence[str]) -> int:
    """Run the bundled Watchdog with *args* (default: serve in the background).

    Never touches Qt, the single-instance socket or the application settings.
    Returns the Watchdog's exit code.
    """
    if not arguments_allowed(args):
        return EXIT_BAD_ARGUMENTS  # quietly: nothing is run
    script = bundled_script()
    if script is None:
        return EXIT_NO_BUNDLE
    _ensure_std_streams()
    argv = list(args) or list(DEFAULT_ARGS)
    folder = str(script.parent)
    os.environ[ENV_EMBEDDED] = "1"
    os.environ[ENV_RESOURCES] = folder
    os.environ[ENV_SELF_LAUNCH] = json.dumps(self_launch(), ensure_ascii=False)
    sys.argv = [str(script), *argv]
    sys.path.insert(0, folder)  # the Watchdog's own modules come first
    try:
        if accepts_argv(script):
            module = importlib.import_module(MODULE_NAME)
            return _exit_code(getattr(module, ENTRY_FUNCTION)(argv))
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return _exit_code(exc.code)
    except KeyboardInterrupt:
        return 130
    except BaseException:
        text = traceback.format_exc()
        _record_crash(text)
        try:
            sys.stderr.write(text)
        except (AttributeError, OSError, ValueError):
            pass
        return EXIT_CRASHED
    return 0


@dataclass(frozen=True)
class LastError:
    code: str
    message: str
    version: str = ""
    #: The code as the integer it was reported as (None for a text code).
    number: int | None = None
    #: When it was reported, seconds since the epoch (None when unusable).
    at: float | None = None


#: What is dropped from reported text: the C0 and C1 control characters that
#: are not white space (white space becomes a plain space, NEL U+0085
#: included), DEL, the marks that change the direction text is shown in, and
#: lone surrogates (a JSON "\ud800" escape), which no text widget can show.
_UNPRINTABLE = re.compile(
    "[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f"
    "\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069"
    "\ud800-\udfff]")


def _one_line(value: str) -> str:
    """*value* on one line, without control or text-direction characters."""
    return " ".join(_UNPRINTABLE.sub("", value).split())[:LAST_ERROR_MAX_TEXT]


def read_last_error(home: Path) -> LastError | None:
    """The Watchdog's own start-up failure report, or None.

    Read-only and defensive: a missing, oversized, corrupt or oddly typed file
    is simply not there.
    """
    path = home / LAST_ERROR_NAME
    try:
        if path.stat().st_size > LAST_ERROR_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    code, message = data.get("code"), data.get("message")
    if isinstance(code, bool) or not isinstance(code, (str, int)):
        return None
    if not isinstance(message, str):
        return None
    version = data.get("watchdog_version")
    at = data.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        at = None
    return LastError(_one_line(str(code)), _one_line(message),
                     _one_line(version) if isinstance(version, str) else "",
                     code if isinstance(code, int) else None,
                     at)


def last_error_hint(report: LastError, home: Path) -> str:
    """The plain-language explanation of a reported code; "" when unknown."""
    if report.number is None:
        return ""
    hint = LAST_ERROR_HINTS.get(report.number, "")
    return hint.replace("{home}", str(home))


def last_error_time(report: LastError) -> str:
    """When the failure was reported, in local time; "" when unusable."""
    if report.at is None:
        return ""
    try:
        return datetime.fromtimestamp(report.at).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def last_error_lines(report: LastError, home: Path) -> list[str]:
    """The diagnostics lines for *report*: the raw code and message first (for
    bug reports), then the explanation and the time when there are any."""
    version = f"（版本 {report.version}）" if report.version else ""
    lines = [f"Agent 監看回報{version}：[{report.code}] {report.message}"]
    hint = last_error_hint(report, home)
    if hint:
        lines.append(f"結束碼 {report.code} 的說明：{hint}")
    when = last_error_time(report)
    if when:
        lines.append(f"回報時間：{when}")
    return lines
