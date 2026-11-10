"""Qt side of the AI_Agent_Watchdog integration.

Asynchronous polling (QNetworkAccessManager, never blocking the GUI thread),
back-off, and OS toasts through a QSystemTrayIcon.  All decisions live in
:mod:`app.agent_watchdog_state`.  When the Watchdog cannot be reached it may be
started as a hidden background process: the copy registered in install.json,
or else the one shipped with the application (see :mod:`app.agent_autostart`);
it is never stopped from here.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

from . import agent_autostart, settings_store
from .agent_watchdog_state import (
    BASELINE,
    INVALID,
    OK,
    RESYNC,
    Backoff,
    Toast,
    WatchdogState,
    dump_last_toast,
    is_loopback_url,
    load_last_toast,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8787"
DEFAULT_START_BAT = ""  # unset: user points to their own start.bat
EVENTS_INTERVAL_MS = 5000
STATUS_INTERVAL_MS = 60000
LEGACY_INTERVAL_MS = 15000
TRANSFER_TIMEOUT_MS = 3000
FAILURES_BEFORE_UNAVAILABLE = 2   # consecutive failures before "not running"
RETRY_AFTER_FAILURE_MS = 1000     # quick retry after a single failure
LEGACY_REPROBE_S = 300.0          # legacy mode re-checks /api/events this often

KEY_ENABLED = "watchdog/enabled"
KEY_BASE_URL = "watchdog/base_url"
KEY_START_BAT = "watchdog/start_bat"
KEY_AUTOSTART = "watchdog/autostart"
KEY_TOAST_WAITING = "watchdog/toast_waiting"
KEY_TOAST_STUCK = "watchdog/toast_stuck"
KEY_LAST_TOAST = "watchdog/last_toast"


def default_endpoint_path() -> Path:
    return agent_autostart.aiwatch_home() / "endpoint.json"


def _is_status_snapshot(payload: dict[str, Any]) -> bool:
    """Shape of /api/status: lists 'sessions' and 'attention'."""
    return (isinstance(payload.get("sessions"), list)
            and isinstance(payload.get("attention"), list))


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


@dataclass
class WatchdogConfig:
    enabled: bool = True
    base_url: str = DEFAULT_BASE_URL
    start_bat: str = DEFAULT_START_BAT
    toast_waiting: bool = True
    toast_stuck: bool = True
    last_toast: str = ""
    autostart: bool = True

    @classmethod
    def load(cls) -> WatchdogConfig:
        store = settings_store.settings()
        base_url = str(store.value(KEY_BASE_URL, DEFAULT_BASE_URL) or "")
        if not is_loopback_url(base_url):
            log.warning("Ignoring non-loopback watchdog/base_url")
            base_url = DEFAULT_BASE_URL
        return cls(
            enabled=_as_bool(store.value(KEY_ENABLED), True),
            base_url=base_url.rstrip("/"),
            start_bat=str(store.value(KEY_START_BAT, DEFAULT_START_BAT)
                          or DEFAULT_START_BAT),
            toast_waiting=_as_bool(store.value(KEY_TOAST_WAITING), True),
            toast_stuck=_as_bool(store.value(KEY_TOAST_STUCK), True),
            last_toast=str(store.value(KEY_LAST_TOAST, "") or ""),
            autostart=_as_bool(store.value(KEY_AUTOSTART), True),
        )


def save_setting(key: str, value: object) -> None:
    store = settings_store.settings()
    store.setValue(key, value)
    store.sync()


class AgentWatcher(QObject):
    """Polls the Watchdog; owns the state, the timers and the tray icon."""

    changed = Signal()          # badge / availability changed
    toast_clicked = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        endpoint_path: Path | None = None,
        clock: Callable[[], float] | None = None,
        use_tray: bool = True,
        launcher: Callable[[list[str], str], str] | None = None,
        autostart_clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(parent)
        self._endpoint_path = endpoint_path or default_endpoint_path()
        self._clock = clock
        self._launcher = launcher or agent_autostart.spawn_detached
        self._autostart_clock = autostart_clock or time.monotonic
        self._policy = agent_autostart.AttemptPolicy()
        self._fast_until = 0.0      # fast probing after a launch, until this time
        self._http_seen = False     # an HTTP answer (not the contract) this round
        self.autostart_message = ""  # why the Watchdog is (not) auto-started
        self.uses_bundled = False   # the last auto-start used the bundled copy
        self._bundled_failure = ""  # plain reason the bundled copy is not up
        self._last_launch: list[str] = []  # what the last auto-start executed
        self._last_cwd = ""
        self._use_tray = use_tray
        self.config = WatchdogConfig.load()
        self.state = self._new_state()
        self._backoff = Backoff()
        self._bases: list[str] = []
        self._base_index = 0
        self._replies: set[QNetworkReply] = set()
        self._generation = 0       # bumped whenever in-flight replies are aborted
        self._failures = 0         # consecutive failed polls while available
        self._legacy_probe_at = 0.0  # time.monotonic() of the next /api/events probe
        self._closed = False
        self._running = False
        self._tray: QSystemTrayIcon | None = None
        self.notify: Callable[[Toast], None] = self._tray_notify

        self._nam = QNetworkAccessManager(self)
        self._nam.setTransferTimeout(TRANSFER_TIMEOUT_MS)
        self._events_timer = QTimer(self)
        self._events_timer.setSingleShot(True)
        self._events_timer.timeout.connect(self._poll_events)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_INTERVAL_MS)
        self._status_timer.timeout.connect(self._poll_status)
        self._toast_timer = QTimer(self)
        self._toast_timer.setInterval(1000)
        self._toast_timer.timeout.connect(self._flush_toasts)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def _new_state(self) -> WatchdogState:
        kwargs: dict[str, Any] = {}
        if self._clock is not None:
            kwargs["clock"] = self._clock
        return WatchdogState(
            last_toast=load_last_toast(self.config.last_toast),
            toast_waiting=self.config.toast_waiting,
            toast_stuck=self.config.toast_stuck,
            **kwargs,
        )

    # --- lifecycle --------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._bases[self._base_index] if self._bases else self.config.base_url

    @property
    def _home(self) -> Path:
        """install.json lives next to endpoint.json (AIWATCH_HOME / ~/.aiwatch)."""
        return self._endpoint_path.parent

    @property
    def available(self) -> bool:
        return not self.state.unavailable

    @property
    def panel_path(self) -> str | None:
        """Panel page declared by /api/events; None means the plain "/"."""
        if self.state.unavailable or self.state.legacy:
            return None
        return self.state.panel_path

    @property
    def running(self) -> bool:
        return self._running

    def in_flight(self) -> int:
        return len(self._replies)

    def start(self) -> None:
        """(Re)start polling when enabled; safe to call repeatedly."""
        if self._closed:
            return
        self._stop_activity()
        self.config = WatchdogConfig.load()
        self.state.toast_waiting = self.config.toast_waiting
        self.state.toast_stuck = self.config.toast_stuck
        if not self.config.enabled:
            self._hide_tray()
            self.state.set_unavailable(True)
            self.changed.emit()
            return
        self._ensure_tray()
        self._running = True
        self._bases = self._discover_bases()
        self._base_index = 0
        self.state.reset_stream()
        self.state.clear_panel()  # the bases were rediscovered: ask again
        self.state.set_legacy(False)
        self.state.clear_pending()  # a woken window must not replay stale toasts
        self._failures = 0
        self._legacy_probe_at = 0.0
        self._backoff.reset()
        self._events_timer.start(0)
        self._status_timer.start()
        self._toast_timer.start()

    def pause(self) -> None:
        """Window closing: no timers, no in-flight replies (tray stays)."""
        self._stop_activity()

    def resume(self) -> None:
        if self._closed or self._running or not self.config.enabled:
            return
        self.start()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_activity()
        self._flush_last_toast()
        self._hide_tray()

    def _stop_activity(self) -> None:
        self._running = False
        self._events_timer.stop()
        self._status_timer.stop()
        self._toast_timer.stop()
        self._abort_replies()

    # --- discovery --------------------------------------------------------

    def _discover_bases(self) -> list[str]:
        """endpoint.json url first (it may be stale), then the configured URL."""
        bases: list[str] = []
        try:
            data = json.loads(self._endpoint_path.read_text(encoding="utf-8"))
            url = data.get("url") if isinstance(data, dict) else None
            if isinstance(url, str) and is_loopback_url(url):
                bases.append(url.rstrip("/"))
        except (OSError, ValueError):
            pass
        if self.config.base_url not in bases:
            bases.append(self.config.base_url)
        return bases

    def endpoint_boot_id(self) -> str | None:
        """Diagnostics only: endpoint.json may be stale, so polling never uses
        this value (the API's own boot_id is authoritative)."""
        try:
            data = json.loads(self._endpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        boot = data.get("boot_id") if isinstance(data, dict) else None
        return boot if isinstance(boot, str) else None

    # --- HTTP -------------------------------------------------------------

    def _get(self, path: str, handler: Callable[[QNetworkReply], None]) -> None:
        if self._closed or not self._running:
            return
        request = QNetworkRequest(QUrl(self.base_url + path))
        request.setTransferTimeout(TRANSFER_TIMEOUT_MS)
        # Never follow a redirect: a stale endpoint.json may point at another
        # program whose 302 would otherwise lead outside the loopback allowlist.
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        reply = self._nam.get(request)
        self._replies.add(reply)
        generation = self._generation

        def done() -> None:
            if reply not in self._replies:
                return
            self._replies.discard(reply)
            try:
                if (not self._closed and self._running
                        and generation == self._generation):
                    handler(reply)
            except Exception:
                # A malformed answer must never end the polling chain.
                log.exception("Watchdog response handler failed")
                self._rearm_if_idle()
            finally:
                reply.deleteLater()

        reply.finished.connect(done)

    def _rearm_if_idle(self) -> None:
        # Only an in-flight events request re-arms the chain by itself; a
        # reconciliation /api/status reply does not, so it must not block this.
        if (self._closed or not self._running or self._events_in_flight()
                or self._events_timer.isActive()):
            return
        self._events_timer.start(EVENTS_INTERVAL_MS)

    def _events_in_flight(self) -> bool:
        return any(r.url().path() == "/api/events" for r in self._replies)

    @staticmethod
    def _http_status(reply: QNetworkReply) -> int:
        value = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        return int(value) if value is not None else 0

    @staticmethod
    def _json(reply: QNetworkReply) -> dict[str, Any] | None:
        try:
            data = json.loads(bytes(reply.readAll().data()).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            return None  # RecursionError: absurdly deep nesting
        return data if isinstance(data, dict) else None

    def _poll_events(self) -> None:
        if self._events_in_flight():
            return  # at most one events request; its handler re-arms the chain
        if self.state.legacy:
            if time.monotonic() < self._legacy_probe_at:
                self._poll_status(reschedule=True)
                return
            # Legacy: look again for /api/events every LEGACY_REPROBE_S.
            self._legacy_probe_at = time.monotonic() + LEGACY_REPROBE_S
        path = "/api/events"
        if self.state.seq is not None and not self.state.legacy:
            path += f"?since={self.state.seq}"
        self._get(path, self._on_events)

    def _on_events(self, reply: QNetworkReply) -> None:
        status = self._http_status(reply)
        if status == 404:
            # Only a status snapshot of the right shape proves an old Watchdog
            # (checked in _on_status); a program that merely answers 404 is
            # treated as an occupied port there.
            self.state.set_legacy(True)
            if self._legacy_probe_at <= time.monotonic():
                self._legacy_probe_at = time.monotonic() + LEGACY_REPROBE_S
            self._poll_status(reschedule=True)
            return
        if status == 400:
            self.state.reset_stream()
            self._connected()
            self._events_timer.start(EVENTS_INTERVAL_MS)
            return
        payload = self._json(reply) if status == 200 else None
        if payload is None:  # includes any 3xx (redirects are never followed)
            self._failed(status)
            return
        was_legacy = self.state.legacy
        probe_state = (self.state.boot_id, self.state.seq)
        if was_legacy:
            self.state.reset_stream()
        result = self.state.apply_events(payload)
        if result == INVALID:
            if was_legacy:
                self.state.boot_id, self.state.seq = probe_state
            log.warning("Watchdog /api/events returned a malformed payload")
            self._failed(status)
            return
        self._connected()
        if was_legacy:
            self.state.set_legacy(False)
        if result in (BASELINE, RESYNC):
            self._poll_status()
        elif result == OK:
            self.changed.emit()
        self._events_timer.start(EVENTS_INTERVAL_MS)

    def _poll_status(self, reschedule: bool = False) -> None:
        if any(r.url().path() == "/api/status" for r in self._replies):
            # One status request at a time, but a polling chain must never die
            # here: the in-flight one may be a non-rescheduling request.
            if reschedule and not self._events_timer.isActive():
                self._events_timer.start(LEGACY_INTERVAL_MS)
            return
        self._get("/api/status", lambda r: self._on_status(r, reschedule))

    def _on_status(self, reply: QNetworkReply, reschedule: bool) -> None:
        status = self._http_status(reply)
        payload = self._json(reply) if status == 200 else None
        if reschedule and payload is not None and not _is_status_snapshot(payload):
            payload = None  # legacy mode: a foreign 200 is not a Watchdog
        if payload is None:
            if reschedule:
                self._failed(status)  # legacy mode: status is the only poll
            else:
                # Reconciliation only: events decide availability.
                log.warning("Watchdog /api/status reconciliation failed (%s)", status)
            return
        was_unavailable = self.state.unavailable
        was_legacy = self.state.legacy
        self._connected()  # leaving an outage clears legacy ...
        if reschedule and was_legacy:
            self.state.set_legacy(True)  # ... but a verified legacy status keeps it
        self.state.apply_status(payload)
        if was_unavailable and not self._events_timer.isActive():
            self._events_timer.start(0)
        if reschedule:
            self._events_timer.start(LEGACY_INTERVAL_MS)
        self.changed.emit()

    def _connected(self) -> None:
        self._backoff.reset()
        self._failures = 0
        self._http_seen = False
        self._fast_until = 0.0
        self._bundled_failure = ""
        self._set_autostart_message("")
        if self.state.unavailable:
            self.state.set_unavailable(False)
            # Back from an outage: the service may have been upgraded or
            # replaced, so look for /api/events again.
            self.state.set_legacy(False)
            self.changed.emit()

    def _abort_replies(self) -> None:
        self._generation += 1
        for reply in list(self._replies):
            self._replies.discard(reply)
            try:
                reply.finished.disconnect()
            except (RuntimeError, TypeError):
                pass
            reply.abort()
            reply.deleteLater()

    def _switch_base(self, index: int) -> None:
        self._abort_replies()
        self._base_index = index
        self._failures = 0
        self.state.reset_stream()
        self.state.set_legacy(False)
        self.state.clear_panel()  # the other base may be a different service
        self._events_timer.start(0)

    def _failed(self, http_status: int = 0) -> None:
        """One failed events (or legacy status) request.

        http_status is the HTTP status when the peer answered at all (0 for a
        refused/timed-out connection): an answer that is not the contract means
        another program owns the port.
        """
        if http_status:
            self._http_seen = True
        if not self.state.unavailable:
            self._failures += 1
            if self._failures < FAILURES_BEFORE_UNAVAILABLE:
                self._events_timer.start(RETRY_AFTER_FAILURE_MS)
                return
        # Confirmed failure.  The endpoint.json URL may be stale: try the
        # configured one next.
        if self._base_index + 1 < len(self._bases):
            self._switch_base(self._base_index + 1)
            return
        was_available = not self.state.unavailable
        self.state.set_unavailable(True)
        self._bases = self._discover_bases()
        self._base_index = 0
        self._events_timer.start(self._next_probe_delay_ms())
        if was_available:
            self.changed.emit()

    # --- auto-start -------------------------------------------------------

    def _set_autostart_message(self, text: str) -> None:
        if text == self.autostart_message:
            return
        self.autostart_message = text
        if text:
            log.warning("Watchdog auto-start: %s", text)
        self.changed.emit()

    def _next_probe_delay_ms(self) -> int:
        """Delay before the next probe once every base has failed.

        Decides on auto-start first: it resets the back-off and switches to the
        fast probe.  Anything else keeps the normal back-off.
        """
        now = self._autostart_clock()
        http_seen, self._http_seen = self._http_seen, False
        if now < self._fast_until:
            return agent_autostart.FAST_PROBE_INTERVAL_MS
        if self.uses_bundled and self._fast_until:
            # The fast-probe window after a bundled launch ran out unanswered.
            self._fast_until = 0.0
            seconds = int(agent_autostart.FAST_PROBE_WINDOW_S)
            self._bundled_failed(
                f"啟動後 {seconds} 秒內沒有回應（逾時）",
                f"內建的 Agent 監看已啟動，但 {seconds} 秒內沒有回應。"
                "稍後會自動再試，也可以按「重新偵測」立即重試。")
        if http_seen:
            self._set_autostart_message(
                "port 被佔用：有程式在回應，但不符合 Watchdog 的 /api/events 合約，"
                "因此不自動啟動。請關掉佔用該 port 的程式後按「重新偵測」。")
        elif self._try_autostart(now):
            self._backoff.reset()
            self._fast_until = now + agent_autostart.FAST_PROBE_WINDOW_S
            return agent_autostart.FAST_PROBE_INTERVAL_MS
        return int(self._backoff.next_delay() * 1000)

    def _try_autostart(self, now: float) -> bool:
        """True when a launch was made (fast probing should follow)."""
        if not self.config.autostart:
            self._set_autostart_message("已在設定關閉「自動啟動 Watchdog」。")
            return False
        verdict = self._policy.verdict(now)
        if verdict == "exhausted":
            if self.uses_bundled:
                self._bundled_failed(
                    f"已達重試上限（{agent_autostart.MAX_ATTEMPTS} 次），每次啟動後都沒有回應",
                    f"已嘗試啟動內建的 Agent 監看 {agent_autostart.MAX_ATTEMPTS} 次"
                    "仍連不上，已停止嘗試。請按「重新偵測」重試。")
            else:
                self._set_autostart_message(
                    f"已自動啟動 {agent_autostart.MAX_ATTEMPTS} 次仍連不上 Watchdog，"
                    "已停止嘗試。請檢查 Watchdog（aiwatch.log）後按「重新偵測」重試。")
            return False
        if verdict == "cooldown":
            return False
        plan = agent_autostart.resolve_launch(self._home)
        if not plan.launch:
            self.uses_bundled = False
            self._set_autostart_message("不自動啟動：" + plan.reason)
            return False
        self.uses_bundled = plan.bundled
        self._bundled_failure = ""
        self._last_launch, self._last_cwd = list(plan.launch), plan.cwd
        self._policy.record(now)
        error = self._launcher(plan.launch, plan.cwd)
        if error:
            if plan.bundled:
                self._bundled_failed(
                    f"無法執行啟動命令（{error}）",
                    "內建的 Agent 監看啟動失敗：無法執行啟動命令。"
                    "請按「重新偵測」重試。")
            else:
                self._set_autostart_message(error)
            return False
        if plan.bundled:
            self._set_autostart_message("正在啟動內建的 Agent 監看，等待回應中…")
        else:
            self._set_autostart_message("已嘗試自動啟動 Watchdog，等待回應中…")
        return True

    def _bundled_failed(self, reason: str, message: str) -> None:
        self._bundled_failure = reason
        self._set_autostart_message(message)

    @property
    def autostart_diagnostics(self) -> str:
        """Copyable details of a failed bundled start; "" when there is none."""
        if not (self.uses_bundled and self._bundled_failure):
            return ""
        home = self._home
        bundle = agent_autostart.agent_bundle
        lines = [
            f"原因：{self._bundled_failure}",
            f"命令列：{subprocess.list2cmdline(self._last_launch)}",
            f"工作目錄：{self._last_cwd}",
            f"記錄檔：{home / 'aiwatch.log'}",
            f"啟動錯誤記錄：{home / bundle.ERROR_LOG_NAME}",
        ]
        reported = bundle.read_last_error(home)
        if reported is not None:
            lines.extend(bundle.last_error_lines(reported, home))
        return "\n".join(lines)

    @property
    def bundled_available(self) -> bool:
        """The application ships its own Watchdog copy."""
        return agent_autostart.agent_bundle.bundled_script() is not None

    def redetect(self) -> None:
        """The panel's 'detect again' button: also the manual auto-start retry."""
        self._policy.reset()
        self._fast_until = 0.0
        self.uses_bundled = False
        self._bundled_failure = ""
        self._set_autostart_message("")
        self.start()

    # --- toasts -----------------------------------------------------------

    def _flush_toasts(self) -> None:
        toasts = self.state.due_toasts()  # already one merged toast per burst
        for toast in toasts:
            self.notify(toast)
        if toasts:
            self._flush_last_toast()

    def _flush_last_toast(self) -> None:
        if self.state.last_toast_dirty:
            self.state.last_toast_dirty = False
            save_setting(KEY_LAST_TOAST, dump_last_toast(self.state.last_toast))

    def _ensure_tray(self) -> None:
        if not self._use_tray or self._tray is not None:
            if self._tray is not None:
                self._tray.show()
            return
        icon = QApplication.windowIcon()
        if icon.isNull():
            app = QApplication.instance()
            style = app.style() if isinstance(app, QApplication) else None
            icon = (style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
                    if style is not None else QIcon())
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("Markdown Viewer：AI Agent 監看")
        tray.messageClicked.connect(self.toast_clicked)
        tray.show()
        self._tray = tray

    def _hide_tray(self) -> None:
        if self._tray is not None:
            self._tray.hide()

    def _tray_notify(self, toast: Toast) -> None:
        if self._tray is not None:
            self._tray.showMessage(
                toast.title, toast.message,
                QSystemTrayIcon.MessageIcon.Information, 10000,
            )
