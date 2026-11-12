"""Change the Agent Watchdog's port (檢視 →「變更 Watchdog 連接埠…」).

The port belongs to the Watchdog, not to the Viewer: the Viewer runs its own
bundled copy as ``--agent-watchdog --set-port N`` (the same launch as the
bundled auto-start, see :func:`app.agent_bundle.set_port_launch`).  Watchdog
1.4.0+ then saves N in its settings.json, moves a running Watchdog there and
prints one line; every host (this Viewer included) follows on its next
discovery.  Nothing here writes settings.json itself.

The mapping from exit code to message and the command line are pure
functions; the process runs asynchronously in a :class:`QProcess` behind a
modal busy dialog, and only that process is ever killed (on time-out).
"""

from __future__ import annotations

import locale
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from . import agent_autostart, agent_bundle, agent_stop
from .agent_watchdog import (
    DEFAULT_PORT,
    AgentWatcher,
    connection_text,
    forget_legacy_base_url,
    host_port,
    url_port,
)
from .agent_watchdog_state import is_loopback_url

log = logging.getLogger(__name__)

TITLE = "變更 Watchdog 連接埠"
SET_PORT_TIMEOUT_MS = 60_000
#: The first Watchdog release that knows --set-port.
MIN_SET_PORT_VERSION = (1, 4, 0)

TOO_OLD_BUNDLE = "這個 Viewer 內建的 Watchdog 版本太舊，還不能變更連接埠。"
GENERIC_FAILURE = "無法變更連接埠。"

# --set-port exit codes (Watchdog 1.4.0).  Each has exactly one meaning; the
# printed line is never needed to tell them apart (a windowed host may not
# even receive it).
EXIT_DONE = 0              # on N, or none running (the next start uses N)
EXIT_STILL_ELSEWHERE = 1   # saved, a Watchdog still runs on another port
EXIT_USAGE = 2
EXIT_PORT_IN_USE = 3       # N belongs to another program: nothing changed
EXIT_SETTINGS_UNWRITABLE = 5
EXIT_EMBEDDED_ELSEWHERE = 6  # saved, moves when its host restarts it
EXIT_UNEXPECTED = 7        # state unknown
EXIT_INSTALLED_TOO_OLD = 8
EXIT_NEWER_SCHEMA = 9      # settings.json written by a newer Watchdog
EXIT_RESTART_FAILED = 10   # saved and stopped, but not started on N
#: The codes after which settings.json holds N: the 1.39.3 address can go.
SAVED_CODES = frozenset({EXIT_DONE, EXIT_STILL_ELSEWHERE, EXIT_EMBEDDED_ELSEWHERE,
                         EXIT_RESTART_FAILED})

# SetPortResult.failure: why the command did not run to its end.
NO_BUNDLE = "no_bundle"
TOO_OLD = "too_old"
LAUNCH = "launch"
TIMEOUT = "timeout"
CRASHED = "crashed"

_INSTALLED_VERSION = re.compile(r"installed watchdog is v?(\d+(?:\.\d+)*)", re.IGNORECASE)


@dataclass(frozen=True)
class SetPortResult:
    """How ``--set-port`` ended: the exit code and the Watchdog's one line."""

    code: int | None        # None: it did not run to its end (see failure)
    line: str = ""
    failure: str = ""


@dataclass(frozen=True)
class PortStatus:
    """What the files say after the run: the remembered port (settings.json)
    and where a Watchdog runs (endpoint.json whose process is alive)."""

    remembered: int | None = None
    running: str | None = None  # "127.0.0.1:6868"; None: none running


@dataclass(frozen=True)
class SetPortOutcome:
    """What to tell the user."""

    text: str
    detail: str = ""
    saved: bool = False     # the Watchdog remembers the port: drop the 1.39.3 address
    warning: bool = True


# --- pure helpers -------------------------------------------------------------


def version_tuple(text: str | None) -> tuple[int, ...] | None:
    """(1, 3, 0) for "1.3.0" (also "1.3", "v1.4.0rc1"); None when unreadable."""
    match = re.match(r"\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())


def bundle_too_old(version: str | None) -> bool:
    """True only when the bundled copy is known to predate --set-port."""
    parsed = version_tuple(version)
    return parsed is not None and parsed < MIN_SET_PORT_VERSION


def version_from_line(line: str) -> str | None:
    """The version in "... the installed watchdog is 1.3.0, which ..."."""
    match = _INSTALLED_VERSION.search(line)
    return match.group(1) if match else None


def decode_output(data: bytes) -> str:
    """UTF-8 (a stream the helper attached), else the ANSI code page that a
    windowed build's own pipe streams use (even while this process runs in
    UTF-8 mode, so not getpreferredencoding())."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        encoding = locale.getencoding() or "utf-8"
        return data.decode(encoding, errors="replace")


def pick_line(code: int | None, stdout: str, stderr: str) -> str:
    """The Watchdog's line: stdout on exit 0, stderr otherwise (the last
    non-empty line, so argparse's usage block yields its error line)."""
    first, second = (stdout, stderr) if code == 0 else (stderr, stdout)
    for text in (first, second):
        lines = [agent_bundle._one_line(raw) for raw in text.splitlines()]
        lines = [line for line in lines if line]
        if lines:
            return lines[-1]
    return ""


def set_port_command(port: int) -> tuple[list[str], str] | None:
    """(argv, cwd) for the Viewer's own bundled copy; None without one."""
    return agent_bundle.set_port_launch(port)


def remembered_text(remembered: int | None) -> str:
    return (f"記住的連接埠：{remembered}" if remembered is not None
            else f"記住的連接埠：尚未設定（預設 {DEFAULT_PORT}）")


def dialog_label(connection: str, remembered: int | None) -> str:
    return (f"{connection}\n{remembered_text(remembered)}\n\n"
            "Watchdog 會記住新的連接埠並馬上換過去，其他程式也會跟著換。\n"
            "新的連接埠（1–65535）：")


def dialog_default(remembered: int | None, connected: int | None) -> int:
    for port in (remembered, connected):
        if isinstance(port, int) and 1 <= port <= 65535:
            return port
    return DEFAULT_PORT


def status_text(status: PortStatus) -> str:
    """「記住的連接埠：6868；目前 Watchdog：127.0.0.1:6868」 and the like."""
    running = (f"目前 Watchdog：{status.running}" if status.running
               else "目前沒有 Watchdog 在執行")
    return f"{remembered_text(status.remembered)}；{running}"


def read_status(home: Path, processes: agent_stop.Processes | None = None) -> PortStatus:
    """Re-read settings.json and endpoint.json (read-only, never raises).

    endpoint.json counts only while the process it names is alive and is not
    a younger process that got the same pid (cheap: no network request).
    """
    remembered = agent_autostart.load_port_setting(home)
    running = None
    try:
        record = agent_stop.read_endpoint(home)
        url = record.get("url") if record else None
        pid = agent_stop._valid_pid(record.get("pid")) if record else None
        if record and pid is not None and isinstance(url, str) and is_loopback_url(url):
            processes = processes or agent_stop.default_processes()
            if processes.alive(pid):
                started = agent_stop._usable_start(record.get("started_at"), time.time())
                created = processes.started_at(pid)
                reused = (started is not None and created is not None
                          and created > started + agent_stop.START_SLACK_S)
                if not reused:
                    running = host_port(url)
    except Exception:  # a status line must never break the dialog
        log.exception("could not read the Watchdog status")
    return PortStatus(remembered, running)


def outcome_for(
    result: SetPortResult,
    port: int,
    bundled_version: str | None = None,
    installed_version: str | None = None,
    status: PortStatus | None = None,
) -> SetPortOutcome:
    """The message for how ``--set-port N`` ended (plain Traditional Chinese).

    Decided by the exit code alone; the Watchdog's line is only the detail
    text (and a second source for the version in exit 8).  *status*, when
    given, adds what the files say now.
    """
    outcome = _outcome(result, port, bundled_version, installed_version, status)
    if status is None or result.failure in (NO_BUNDLE, TOO_OLD):
        return outcome  # nothing ran: the dialog already showed the state
    return SetPortOutcome(f"{outcome.text}\n\n{status_text(status)}", outcome.detail,
                          outcome.saved, outcome.warning)


def _outcome(
    result: SetPortResult,
    port: int,
    bundled_version: str | None,
    installed_version: str | None,
    status: PortStatus | None,
) -> SetPortOutcome:
    detail = result.line
    if result.failure == NO_BUNDLE:
        return SetPortOutcome("這個 Viewer 沒有內建 Watchdog，無法變更連接埠。", detail)
    if result.failure == TOO_OLD:
        return SetPortOutcome(TOO_OLD_BUNDLE, detail)
    if result.failure == LAUNCH:
        return SetPortOutcome("無法執行內建的 Watchdog，沒有變更連接埠。", detail)
    if result.failure == TIMEOUT:
        seconds = SET_PORT_TIMEOUT_MS // 1000
        return SetPortOutcome(
            f"變更連接埠沒有在 {seconds} 秒內完成，已停止等待，結果不明。"
            "請稍後在 AI Agents 面板確認 Watchdog 的狀態。", detail)
    if result.failure == CRASHED or result.code is None:
        return SetPortOutcome("內建的 Watchdog 意外結束，無法確定是否已變更。", detail)
    code = result.code
    if code == EXIT_DONE:
        if status is not None and status.running is None:
            text = (f"已改成連接埠 {port}。目前沒有 Watchdog 在執行，"
                    f"下次啟動時會使用 {port}，其他程式也會跟著換過去。")
        else:
            text = (f"已改成連接埠 {port}。Watchdog 現在在 {port} 上執行，"
                    "其他程式也會跟著換過去。")
        return SetPortOutcome(text, detail, saved=True, warning=False)
    if code == EXIT_STILL_ELSEWHERE:
        return SetPortOutcome(
            f"連接埠 {port} 已記住，但目前的 Watchdog 仍在其他連接埠執行，沒能搬過去。",
            detail, saved=True)
    if code == EXIT_USAGE:
        too_old = bundle_too_old(bundled_version) or "unrecognized arguments" in detail
        return SetPortOutcome(TOO_OLD_BUNDLE if too_old else GENERIC_FAILURE, detail)
    if code == EXIT_PORT_IN_USE:
        return SetPortOutcome(f"連接埠 {port} 已被其他程式使用，沒有做任何變更。", detail)
    if code == EXIT_SETTINGS_UNWRITABLE:
        return SetPortOutcome("無法寫入 Watchdog 的設定檔，沒有做任何變更。", detail)
    if code == EXIT_EMBEDDED_ELSEWHERE:
        return SetPortOutcome(
            f"連接埠 {port} 已記住。目前的 Watchdog 是由其他程式啟動的，"
            "會在它下次重新啟動時換過去。", detail, saved=True, warning=False)
    if code == EXIT_UNEXPECTED:
        return SetPortOutcome("發生未預期的錯誤，無法確定是否已變更。", detail)
    if code == EXIT_INSTALLED_TOO_OLD:
        version = installed_version or version_from_line(detail) or "1.4.0 以前的版本"
        return SetPortOutcome(
            f"電腦上安裝的 Watchdog 版本太舊（{version}），無法記住連接埠。"
            "請先把它更新到 1.4.0 以上，再設定一次。沒有做任何變更。", detail)
    if code == EXIT_NEWER_SCHEMA:
        return SetPortOutcome("Watchdog 的設定檔是較新版本寫的，沒有做任何變更。", detail)
    if code == EXIT_RESTART_FAILED:
        return SetPortOutcome(
            f"連接埠 {port} 已記住，但 Watchdog 停下後無法在 {port} 重新啟動，"
            "目前沒有 Watchdog 在執行。", detail, saved=True)
    # 4 and anything else: not in the table, so nothing is assumed.
    return SetPortOutcome(f"{GENERIC_FAILURE}無法確定是否已變更（結束碼 {code}）。", detail)


# --- the process ----------------------------------------------------------------


class SetPortRun(QObject):
    """Runs one ``--set-port`` command asynchronously; emits done(result) once."""

    done = Signal(object)

    def __init__(
        self,
        argv: list[str],
        cwd: str,
        timeout_ms: int = SET_PORT_TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.result: SetPortResult | None = None
        process = QProcess(self)
        process.setProgram(argv[0])
        process.setArguments(list(argv[1:]))
        process.setWorkingDirectory(cwd)
        env = QProcessEnvironment()
        for key, value in agent_autostart.clean_environment().items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setStandardInputFile(QProcess.nullDevice())
        process.errorOccurred.connect(self._on_error)
        process.finished.connect(self._on_finished)
        self._process = process
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._on_timeout)

    @property
    def process(self) -> QProcess:
        return self._process

    def start(self) -> None:
        self._timer.start()
        self._process.start()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._finish(SetPortResult(None, self._process.errorString(), LAUNCH))

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        stdout = decode_output(bytes(self._process.readAllStandardOutput().data()))
        stderr = decode_output(bytes(self._process.readAllStandardError().data()))
        line = pick_line(code, stdout, stderr)
        if status == QProcess.ExitStatus.CrashExit:
            self._finish(SetPortResult(None, line, CRASHED))
        elif (code == agent_bundle.EXIT_CRASHED
              and "Traceback (most recent call last)" in stderr):
            self._finish(SetPortResult(None, line, CRASHED))  # run_bundled caught it
        else:
            self._finish(SetPortResult(code, line))

    def _on_timeout(self) -> None:
        if self.result is not None:
            return
        self._finish(SetPortResult(None, "", TIMEOUT))  # first: ignore its finished()
        # Only the process this run started; nothing else is ever ended.
        self._process.kill()
        self._process.waitForFinished(3000)

    def _finish(self, result: SetPortResult) -> None:
        if self.result is not None:
            return
        self.result = result
        self._timer.stop()
        log.info("watchdog --set-port ended: %s", result)
        self.done.emit(result)


class _BusyDialog(QDialog):
    """Modal, cannot be cancelled (no button, Esc and the close box do nothing)."""

    def __init__(self, parent: QWidget | None, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint
                            | Qt.WindowType.WindowTitleHint)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(text))
        bar = QProgressBar()
        bar.setRange(0, 0)  # busy indicator
        bar.setTextVisible(False)
        layout.addWidget(bar)
        self._closable = False

    def finish(self) -> None:
        self._closable = True
        self.accept()

    def reject(self) -> None:
        if self._closable:
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._closable:
            super().closeEvent(event)
        else:
            event.ignore()


# --- the dialogs ----------------------------------------------------------------


def _ask_port(parent: QWidget | None, label: str, default: int) -> int | None:
    port, ok = QInputDialog.getInt(parent, TITLE, label, default, 1, 65535, 1)
    return int(port) if ok else None


def _run_modal(parent: QWidget | None, argv: list[str], cwd: str, port: int) -> SetPortResult:
    seconds = SET_PORT_TIMEOUT_MS // 1000
    busy = _BusyDialog(parent, f"正在把 Watchdog 換到連接埠 {port}…\n（最多等 {seconds} 秒）")
    run = SetPortRun(argv, cwd, parent=busy)
    run.done.connect(lambda _result: busy.finish())
    QTimer.singleShot(0, run.start)  # inside the dialog's loop: done() can end it
    busy.exec()
    result = run.result or SetPortResult(None, "", LAUNCH)
    busy.deleteLater()
    return result


def _show_outcome(parent: QWidget | None, outcome: SetPortOutcome) -> None:
    icon = QMessageBox.Icon.Warning if outcome.warning else QMessageBox.Icon.Information
    box = QMessageBox(icon, TITLE, outcome.text, QMessageBox.StandardButton.Ok, parent)
    if outcome.detail:
        box.setDetailedText(outcome.detail)
    box.exec()


def finish_set_port(
    result: SetPortResult, port: int, bundled_version: str | None,
    watcher: AgentWatcher | None,
) -> SetPortOutcome:
    """Map the result; drop the 1.39.3 address once the Watchdog remembers the
    port (exit 0, 1, 6 or 10); restart the watcher so it discovers the bases
    again.  settings.json and endpoint.json are re-read whatever the code."""
    home = agent_autostart.aiwatch_home()
    installed = (agent_autostart.installed_version(home)
                 if result.code == EXIT_INSTALLED_TOO_OLD else None)
    outcome = outcome_for(result, port, bundled_version, installed, read_status(home))
    if outcome.saved:
        forget_legacy_base_url()
    if watcher is not None:
        watcher.start()
    return outcome


def change_watchdog_port(
    parent: QWidget | None,
    watcher: AgentWatcher | None,
    ask: Callable[[QWidget | None, str, int], int | None] = _ask_port,
    run: Callable[[QWidget | None, list[str], str, int], SetPortResult] = _run_modal,
    show: Callable[[QWidget | None, SetPortOutcome], None] = _show_outcome,
) -> SetPortOutcome | None:
    """The menu command.  Returns the outcome shown, None when cancelled."""
    remembered = agent_autostart.load_port_setting(agent_autostart.aiwatch_home())
    connected = None
    if watcher is not None and watcher.available:
        connected = url_port(watcher.base_url)
    label = dialog_label(connection_text(watcher), remembered)
    port = ask(parent, label, dialog_default(remembered, connected))
    if port is None:
        return None
    version = agent_bundle.bundled_version()
    command = set_port_command(port)
    if command is None:
        outcome = outcome_for(SetPortResult(None, "", NO_BUNDLE), port, version)
    elif bundle_too_old(version):
        # Not even run: 1.3.0 would refuse the option and leave a bogus
        # "bad command line" report in the Watchdog's data directory.
        outcome = outcome_for(
            SetPortResult(None, f"內建的 Watchdog 版本：{version}", TOO_OLD), port, version)
    else:
        argv, cwd = command
        if watcher is not None:
            watcher.pause()  # no polling or auto-start while the Watchdog moves
        try:
            result = run(parent, argv, cwd, port)
        except Exception as exc:  # the watcher must not stay paused
            log.exception("watchdog --set-port could not be run")
            result = SetPortResult(None, str(exc), LAUNCH)
        outcome = finish_set_port(result, port, version, watcher)
    show(parent, outcome)
    return outcome
