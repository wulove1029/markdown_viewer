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
  this entry point can serve and nothing more.
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


def _ensure_std_streams() -> None:
    """A windowed packaged build has no console: sys.stdout / sys.stderr /
    sys.stdin are None there and the first print() would raise."""
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, mode, encoding="utf-8"))
            except OSError:
                pass


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


def arguments_allowed(args: Sequence[str]) -> bool:
    """True when *args* only serve: --serve, --background, --port N, --idle-exit S."""
    values = {_PORT_ARG: _is_port, _IDLE_EXIT_ARG: _is_seconds}
    items = list(args)
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
