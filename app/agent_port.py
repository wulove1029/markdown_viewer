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
from collections.abc import Callable
from dataclasses import dataclass

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

from . import agent_autostart, agent_bundle
from .agent_watchdog import (
    DEFAULT_PORT,
    AgentWatcher,
    connection_text,
    forget_legacy_base_url,
    url_port,
)

log = logging.getLogger(__name__)

TITLE = "變更 Watchdog 連接埠"
SET_PORT_TIMEOUT_MS = 60_000
#: The first Watchdog release that knows --set-port.
MIN_SET_PORT_VERSION = (1, 4, 0)

TOO_OLD_BUNDLE = "這個 Viewer 內建的 Watchdog 版本太舊，還不能變更連接埠。"
GENERIC_FAILURE = "無法變更連接埠。"

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


def installed_version(line: str) -> str | None:
    """The version in "... the installed watchdog is 1.3.0, which ..."."""
    match = _INSTALLED_VERSION.search(line)
    return match.group(1) if match else None


def decode_output(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")


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


def dialog_label(connection: str, remembered: int | None) -> str:
    memory = (f"記住的連接埠：{remembered}" if remembered is not None
              else f"記住的連接埠：尚未設定（預設 {DEFAULT_PORT}）")
    return (f"{connection}\n{memory}\n\n"
            "Watchdog 會記住新的連接埠並馬上換過去，其他程式也會跟著換。\n"
            "新的連接埠（1–65535）：")


def dialog_default(remembered: int | None, connected: int | None) -> int:
    for port in (remembered, connected):
        if isinstance(port, int) and 1 <= port <= 65535:
            return port
    return DEFAULT_PORT


def _newer_schema(line: str) -> bool:
    lowered = line.lower()
    return "newer" in lowered and "schema" in lowered


def outcome_for(
    result: SetPortResult, port: int, bundled_version: str | None = None,
) -> SetPortOutcome:
    """The message for how ``--set-port N`` ended (plain Traditional Chinese)."""
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
        return SetPortOutcome("內建的 Watchdog 意外結束，連接埠可能沒有變更。", detail)
    code = result.code
    if code == 0:
        return SetPortOutcome(
            f"已改成連接埠 {port}。Watchdog 現在在 {port} 上執行，其他程式也會跟著換過去。",
            detail, saved=True, warning=False)
    if code == 1:
        if _newer_schema(detail):  # settings.json from a newer Watchdog: untouched
            return SetPortOutcome(
                "Watchdog 的設定檔是較新版本寫入的，沒有做任何變更。", detail)
        return SetPortOutcome(f"連接埠 {port} 已記住，但 Watchdog 沒能搬過去。",
                              detail, saved=True)
    if code == 2:
        too_old = bundle_too_old(bundled_version) or "unrecognized arguments" in detail
        return SetPortOutcome(TOO_OLD_BUNDLE if too_old else GENERIC_FAILURE, detail)
    if code == 3:
        return SetPortOutcome(f"連接埠 {port} 已被其他程式使用，沒有做任何變更。", detail)
    if code in (4, 7):
        return SetPortOutcome(f"Watchdog 停下後無法在連接埠 {port} 重新啟動。", detail)
    if code == 5:
        return SetPortOutcome("無法寫入 Watchdog 的設定檔，沒有做任何變更。", detail)
    if code == 6:
        return SetPortOutcome(
            f"連接埠 {port} 已記住。目前的 Watchdog 是由其他程式啟動的，"
            "會在它下次重新啟動時換過去。", detail, saved=True, warning=False)
    if code == 8:
        version = installed_version(detail) or "1.4.0 以前的版本"
        return SetPortOutcome(
            f"電腦上安裝的 Watchdog 版本太舊（{version}），無法記住連接埠。"
            "請先把它更新到 1.4.0 以上，再設定一次。沒有做任何變更。", detail)
    return SetPortOutcome(f"{GENERIC_FAILURE}（結束碼 {code}）", detail)


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
    port (exit 0, 1 or 6); restart the watcher so it discovers the bases again."""
    outcome = outcome_for(result, port, bundled_version)
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
