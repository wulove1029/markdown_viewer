"""Auto-start of the AI Agent Watchdog as a hidden background process.

Implements the client side of the Watchdog's auto-start contract.  Pure
logic, no Qt: the watcher (:mod:`app.agent_watchdog`) decides *when*, this
module decides *whether* (install.json validation, attempt budget) and *how*
(detached spawn).  install.json is only ever read, never written.  The
spawned process is never tracked, killed or asked to shut down.

When the registration cannot be used and the application ships its own copy
(:mod:`app.agent_bundle`), :func:`resolve_launch` falls back to that copy,
unless install.json explicitly turned auto-start off.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from . import agent_bundle

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA = 1
MAX_ATTEMPTS = 3               # per run, until the user retries by hand
COOLDOWN_S = 60.0              # minimum gap between two attempts
FAST_PROBE_INTERVAL_MS = 2000  # probe period right after a launch
FAST_PROBE_WINDOW_S = 20.0     # how long the fast probing lasts

# Windows process-creation flags (literals keep this importable anywhere).
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
BASE_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

_PYTHON_NAMES = ("python.exe", "pythonw.exe")
_REGISTER_HINT = "請在 Watchdog 的新位置執行一次 aiwatch.py --register，或手動啟動一次。"


def aiwatch_home() -> Path:
    """AIWATCH_HOME if set, else ~/.aiwatch (endpoint.json and install.json)."""
    override = os.environ.get("AIWATCH_HOME", "").strip()
    return Path(override) if override else Path.home() / ".aiwatch"


@dataclass
class InstallCheck:
    ok: bool
    reason: str = ""
    launch: list[str] = field(default_factory=list)
    cwd: str = ""
    opted_out: bool = False  # install.json says "autostart": false


def _bad(reason: str) -> InstallCheck:
    return InstallCheck(False, reason)


def _is_abs(path: str) -> bool:
    return PureWindowsPath(path).is_absolute()


def _name(path: str) -> str:
    return PureWindowsPath(path).name.lower()


def load_install(home: Path) -> InstallCheck:
    """Read and strictly validate ``home/install.json`` (read-only)."""
    path = home / "install.json"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _bad(f"找不到 {path}，Watchdog 尚未登記。"
                    "請在 Watchdog 資料夾執行 aiwatch.py --register。")
    except (OSError, ValueError):
        return _bad(f"無法讀取 {path}（檔案損毀）。請重新執行 aiwatch.py --register。")
    if not isinstance(data, dict):
        return _bad("install.json 格式不符（不是物件）。")
    check = _validate_install(data)
    # Whatever else is wrong with the file, an explicit "off" is the user's
    # decision; it also keeps the bundled copy from being started.
    check.opted_out = data.get("autostart") is False
    return check


def _validate_install(data: dict[str, Any]) -> InstallCheck:
    schema = data.get("schema")
    # Strictly the integer 1, like the Watchdog: 1.0, True and "1" are refused.
    if not (type(schema) is int and schema == SUPPORTED_SCHEMA):
        return _bad(f"install.json 的 schema 是 {schema!r}，本程式只支援 1，不自動啟動。")
    if data.get("autostart") is not True:
        return _bad("install.json 的 autostart 為關閉（可用 aiwatch.py --autostart on 開啟）。")
    launch = data.get("launch")
    if (not isinstance(launch, list) or len(launch) != 6
            or not all(isinstance(x, str) for x in launch)):
        return _bad("install.json 的 launch 形狀不符（需為 6 個字串），不自動啟動。")
    python, x_flag, utf8, script, serve, background = launch
    if (x_flag, utf8, serve, background) != ("-X", "utf8", "--serve", "--background"):
        return _bad("install.json 的 launch 參數不符合合約，不自動啟動。")
    if not _is_abs(python) or _name(python) not in _PYTHON_NAMES:
        return _bad("install.json 的 launch[0] 不是 python.exe / pythonw.exe 的絕對路徑。")
    if not _is_abs(script) or _name(script) != "aiwatch.py":
        return _bad("install.json 的 launch[3] 不是 aiwatch.py 的絕對路徑。")
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not _is_abs(cwd):
        return _bad("install.json 的 cwd 不是絕對路徑。")
    if not os.path.isfile(python):
        return _bad(f"找不到 {python}（隨身碟沒插或磁碟代號改了？）。{_REGISTER_HINT}")
    if not os.path.isfile(script):
        return _bad(f"找不到 {script}（隨身碟沒插或磁碟代號改了？）。{_REGISTER_HINT}")
    if not os.path.isdir(cwd):
        return _bad(f"找不到工作目錄 {cwd}。{_REGISTER_HINT}")
    return InstallCheck(True, "", list(launch), cwd)


def clean_environment(
    environ: Mapping[str, str] | None = None,
    frozen: bool | None = None,
    meipass: str | None = None,
) -> dict[str, str]:
    """Copy of the environment without packaging leftovers (PyInstaller)."""
    import sys

    source = os.environ if environ is None else environ
    env: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if (upper.startswith("_MEI") or upper.startswith("_PYI_")
                or upper in ("PYTHONHOME", "PYTHONPATH")):
            continue
        env[key] = value
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    root = getattr(sys, "_MEIPASS", None) if meipass is None else meipass
    if is_frozen and root:
        prefix = os.path.normcase(os.path.normpath(root))
        for key in list(env):
            if key.upper() != "PATH":
                continue
            kept = [
                p for p in env[key].split(os.pathsep)
                if p and not os.path.normcase(os.path.normpath(p)).startswith(prefix)
            ]
            env[key] = os.pathsep.join(kept)
    return env


def spawn_detached(
    launch: list[str],
    cwd: str,
    popen: Callable[..., Any] = subprocess.Popen,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Start ``launch`` detached and hidden.  Returns "" or an error text.

    Tries with CREATE_BREAKAWAY_FROM_JOB first; if the OS refuses (OSError),
    once more without it.  The process object is dropped at once: nothing is
    waited for, stored or cleaned up, and the child is never killed.
    """
    env = clean_environment(environ)
    last: OSError | None = None
    for flags in (BASE_FLAGS | CREATE_BREAKAWAY_FROM_JOB, BASE_FLAGS):
        try:
            proc = popen(
                launch, cwd=cwd, shell=False, env=env, creationflags=flags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
            )
        except OSError as exc:
            last = exc
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            del proc
        return ""
    return f"無法啟動 Watchdog：{last}"


@dataclass
class LaunchPlan:
    """What to start, or why nothing is started (``launch`` empty)."""

    launch: list[str] = field(default_factory=list)
    cwd: str = ""
    bundled: bool = False  # the copy shipped with the application
    reason: str = ""


def resolve_launch(home: Path, port: int | None = None) -> LaunchPlan:
    """The user's registered copy first, else the bundled one.

    1. A valid install.json wins: the copy the user registered.
    2. install.json with ``"autostart": false`` starts nothing at all.
    3. Anything else (missing, corrupt, wrong shape, a launch outside the
       contract, files that are gone) starts the bundled copy when there is one.
    4. No bundled copy: nothing is started and the registration's problem is
       the reason, exactly as without this fallback.

    *port* (a user-configured non-default port) is appended as ``--port N``;
    None leaves the argv exactly as before.
    """
    check = load_install(home)
    if check.ok:
        launch = list(check.launch)
        if port is not None:
            launch += ["--port", str(port)]
        return LaunchPlan(launch, check.cwd)
    bundled = None if check.opted_out else agent_bundle.bundled_launch()
    if bundled is None:
        return LaunchPlan(reason=check.reason)
    launch, cwd = bundled
    if port is not None:
        launch = [*launch, *agent_bundle.DEFAULT_ARGS, "--port", str(port)]
    return LaunchPlan(launch, cwd, bundled=True)


class AttemptPolicy:
    """At most MAX_ATTEMPTS launches per run, COOLDOWN_S apart."""

    def __init__(self) -> None:
        self.attempts = 0
        self.last_at: float | None = None

    def verdict(self, now: float) -> str:
        """'ok', 'cooldown' or 'exhausted'."""
        if self.attempts >= MAX_ATTEMPTS:
            return "exhausted"
        if self.last_at is not None and now - self.last_at < COOLDOWN_S:
            return "cooldown"
        return "ok"

    def record(self, now: float) -> None:
        self.attempts += 1
        self.last_at = now

    def reset(self) -> None:
        self.attempts = 0
        self.last_at = None
