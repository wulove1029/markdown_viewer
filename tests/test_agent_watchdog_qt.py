"""Qt-layer tests: poller, back-off, shutdown, loopback guard, panel profile."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QEvent, QSettings, Qt, QThreadPool, QTimer
from PySide6.QtWebEngineCore import QWebEngineProfile

from app import agent_watchdog, settings_store
from app.agent_watchdog import AgentWatcher
from app.agent_watchdog_state import Backoff

BOOT = "591cbaa4eeb947ec838b3d9659bb7913"
MACHINE = {"id": "ab" * 8, "name": "PC"}
# Spelled out rather than imported, so a change to what the panel asks for
# shows up here.
PANEL_QUERY = "readonly=1&compact=1"


class Script:
    """Mutable behaviour of the mock Watchdog, shared with handler threads."""

    def __init__(self):
        self.boot = BOOT
        self.latest = 5
        self.truncated = False
        self.events = []
        self.attention = []
        self.events_status = 200
        self.delay = 0.0
        self.status_status = 200
        self.status_override = None   # dict -> sent instead of the status body
        self.events_override = None   # dict -> sent instead of the normal body
        self.location = None          # Location header for 3xx answers
        self.panel = None             # value of the optional "panel" key
        self.log = []          # (path, query, thread name)
        self.lock = threading.Lock()


def _handler_for(script):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            if script.location:
                self.send_header("Location", script.location)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            with script.lock:
                script.log.append((path, query, threading.current_thread().name))
            if script.delay:
                time.sleep(script.delay)
            try:
                if path == "/api/events":
                    if script.events_status != 200:
                        return self._send(script.events_status, b"<html>x</html>")
                    if script.events_override is not None:
                        return self._send(200, script.events_override)
                    body = {
                        "schema": 1, "boot_id": script.boot,
                        "latest_seq": script.latest, "oldest_seq": 1,
                        "truncated": script.truncated,
                        "events": script.events if query else [],
                        "machine": MACHINE,
                    }
                    if script.panel is not None:
                        body["panel"] = script.panel
                    self._send(200, body)
                elif path == "/api/status":
                    if script.status_status != 200:
                        return self._send(script.status_status, b"boom")
                    if script.status_override is not None:
                        return self._send(200, script.status_override)
                    self._send(200, {
                        "generated_at": 1.0, "version": 3, "sessions": [],
                        "attention": script.attention, "hidden": 0,
                        "remote_errors": [], "malformed": 0, "cache_age_s": 0,
                        "cache_stale": False, "scanner_error": None,
                        "machine": MACHINE,
                    })
                else:
                    self._send(404, b"nope")
            except (BrokenPipeError, ConnectionError):
                pass

    return Handler


@pytest.fixture
def mock_dog():
    script = Script()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(script))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield script
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.fixture
def other_dog():
    """A second mock service (e.g. the target of a redirect)."""
    script = Script()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(script))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield script
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.fixture
def watchers(qapp, tmp_path, monkeypatch):
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        settings_store, "settings",
        lambda: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    monkeypatch.setattr(agent_watchdog, "EVENTS_INTERVAL_MS", 40)
    monkeypatch.setattr(agent_watchdog, "LEGACY_INTERVAL_MS", 40)
    monkeypatch.setattr(agent_watchdog, "RETRY_AFTER_FAILURE_MS", 30)
    created = []

    def make(base_url, **kwargs):
        store = settings_store.settings()
        store.setValue("watchdog/base_url", base_url)
        store.sync()
        w = AgentWatcher(
            endpoint_path=tmp_path / "no-endpoint.json", use_tray=False, **kwargs
        )
        w._backoff = Backoff((0.03, 0.06, 0.09, 0.12))
        created.append(w)
        return w

    yield make
    for w in created:
        w.shutdown()
    assert QThreadPool.globalInstance().waitForDone(15000)
    qapp.processEvents()
    # Python-owned QObjects must be destroyed here on the GUI thread, not by a
    # cyclic-GC pass that happens to run inside a mock server thread.
    for w in created:
        if shiboken6.isValid(w):
            w.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def wait_until(qapp, condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def att(lid, state="WAITING"):
    return {"logical_id": lid, "state": state, "project": lid, "agent": "Claude",
            "host": "local"}


def event(seq, lid, to, frm="WORKING"):
    return {"seq": seq, "ts": 1.0, "logical_id": lid, "agent": "Claude",
            "host": "local", "project": lid, "from": frm, "to": to, "title": None}


def test_baseline_then_status_then_events(qapp, mock_dog, watchers):
    mock_dog.attention = [att("a")]
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.state.badge_text() == "🤖 1 等待中")
    mock_dog.events = [event(6, "b", "STUCK")]
    mock_dog.latest = 6
    assert wait_until(qapp, lambda: w.state.badge_text() == "🤖 1 等待中 · 1 可能卡住")
    paths = [(p, q) for p, q, _ in mock_dog.log]
    assert ("/api/events", "") in paths
    assert any(p == "/api/status" for p, _ in paths)
    assert any(p == "/api/events" and q == "since=5" for p, q in paths)
    assert w.state.boot_id == BOOT


def test_truncated_and_boot_change_rebuild_from_status(qapp, mock_dog, watchers):
    mock_dog.attention = [att("a")]
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: "a" in w.state.attention)
    mock_dog.attention = [att("c", "STUCK")]
    mock_dog.truncated = True
    mock_dog.latest = 50
    assert wait_until(qapp, lambda: list(w.state.attention) == ["c"])
    mock_dog.truncated = False
    mock_dog.attention = [att("d")]
    mock_dog.boot = "e" * 32
    assert wait_until(qapp, lambda: list(w.state.attention) == ["d"])
    assert w.state.boot_id == "e" * 32


def test_to_null_event_removes_session(qapp, mock_dog, watchers):
    mock_dog.attention = [att("a")]
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: "a" in w.state.attention)
    mock_dog.events = [event(6, "a", None, "WAITING")]
    mock_dog.latest = 6
    assert wait_until(qapp, lambda: w.state.attention == {})


def test_legacy_404_uses_status_only(qapp, mock_dog, watchers):
    mock_dog.events_status = 404
    mock_dog.attention = [att("a")]
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.state.badge_text() == "🤖 1 等待中")
    assert w.state.legacy
    assert w.state.due_toasts() == []


def test_legacy_chain_survives_inflight_status(qapp, mock_dog, watchers, monkeypatch):
    """A rescheduling poll that meets an in-flight status must still re-arm."""
    monkeypatch.setattr(agent_watchdog, "STATUS_INTERVAL_MS", 600000)
    mock_dog.events_status = 404
    mock_dog.delay = 0.15
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.state.legacy)
    assert wait_until(qapp, lambda: w.in_flight() == 0)
    w._events_timer.stop()
    w._poll_status()          # non-rescheduling request now in flight
    w._poll_events()          # legacy poll collides with it
    assert w.in_flight() == 1
    count = lambda: sum(1 for p, _, _ in mock_dog.log if p == "/api/status")  # noqa: E731
    start = count()
    assert wait_until(qapp, lambda: count() >= start + 3, timeout=10)


def test_backoff_on_failure_and_recovery(qapp, mock_dog, watchers):
    mock_dog.events_status = 500
    delays = []
    w = watchers(mock_dog.url)
    original = w._backoff.next_delay

    def spy():
        delays.append(original())
        return delays[-1]

    w._backoff.next_delay = spy
    w.start()
    assert wait_until(qapp, lambda: len(delays) >= 5)
    assert delays[:5] == [0.03, 0.06, 0.09, 0.12, 0.12]
    assert w.state.badge_text() == "🤖 未啟動"
    mock_dog.events_status = 200
    assert wait_until(qapp, lambda: w.available)
    assert w.state.badge_text() == "🤖 0"
    assert w._backoff.next_delay() == 0.03  # reset after the recovery


def test_connection_refused_shows_not_started(qapp, watchers):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    w = watchers(f"http://127.0.0.1:{port}")
    w.start()
    qapp.processEvents()
    assert wait_until(qapp, lambda: w._backoff._index >= 2)
    assert not w.available
    assert w.state.badge_text() == "🤖 未啟動"


def test_http_runs_off_the_gui_thread(qapp, mock_dog, watchers):
    mock_dog.delay = 0.5
    w = watchers(mock_dog.url)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(20)
    started = time.monotonic()
    w.start()
    assert time.monotonic() - started < 0.25
    assert wait_until(qapp, lambda: w.available, timeout=5)
    timer.stop()
    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert len(ticks) > 5 and max(gaps) < 0.3
    main = threading.current_thread().name
    assert all(name != main for _, _, name in mock_dog.log)


def test_pause_and_quit_leave_no_inflight_reply(qapp, mock_dog, watchers):
    mock_dog.delay = 0.6
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.in_flight() > 0)
    w.pause()  # what MainWindow.closeEvent calls
    assert w.in_flight() == 0
    assert not (w._events_timer.isActive() or w._status_timer.isActive()
                or w._toast_timer.isActive())
    w.resume()
    assert wait_until(qapp, lambda: w.in_flight() > 0)
    qapp.aboutToQuit.emit()  # safety net
    assert w.in_flight() == 0 and not w.running
    assert not (w._events_timer.isActive() or w._status_timer.isActive())
    w.start()  # a closed watcher never restarts
    assert w.in_flight() == 0


def test_non_loopback_base_url_is_rejected(qapp, mock_dog, watchers, tmp_path):
    w = watchers("http://example.com:8787")
    assert w.config.base_url == "http://127.0.0.1:8787"
    endpoint = tmp_path / "ep.json"
    endpoint.write_text(json.dumps({"url": "http://10.0.0.5:8787"}))
    w._endpoint_path = endpoint
    assert w._discover_bases() == ["http://127.0.0.1:8787"]
    w2 = watchers("http://localhost:8787")
    assert w2.config.base_url == "http://127.0.0.1:8787"


def test_endpoint_json_discovery_and_stale_fallback(qapp, mock_dog, watchers, tmp_path):
    endpoint = tmp_path / "ep.json"
    endpoint.write_text(json.dumps({
        "schema": 1, "port": 1, "pid": 1, "boot_id": "stale", "started_at": 1,
        "machine": MACHINE, "url": "http://127.0.0.1:9"}))  # nothing listens
    mock_dog.attention = [att("a")]
    w = watchers(mock_dog.url)
    w._endpoint_path = endpoint
    assert w._discover_bases() == ["http://127.0.0.1:9", mock_dog.url]
    assert w.endpoint_boot_id() == "stale"
    w.start()
    assert wait_until(qapp, lambda: w.available and "a" in w.state.attention)
    assert w.base_url == mock_dog.url
    assert w.state.boot_id != w.endpoint_boot_id()  # stale file: the API wins


def test_toast_goes_through_notify_and_persists_cooldown(qapp, mock_dog, watchers):
    clock = SimpleNamespace(now=1000.0)
    mock_dog.attention = []
    w = watchers(mock_dog.url, clock=lambda: clock.now)
    shown = []
    w.notify = shown.append
    w.start()
    assert wait_until(qapp, lambda: w.available)
    mock_dog.events = [event(6, "a", "WAITING")]
    mock_dog.latest = 6
    mock_dog.attention = [att("a")]
    assert wait_until(qapp, lambda: "a" in w.state._pending)
    clock.now += 30
    assert wait_until(qapp, lambda: len(shown) == 1)
    raw = settings_store.settings().value("watchdog/last_toast")
    assert "a" in json.loads(raw)


def test_agents_panel_uses_its_own_profile_and_lazy_load(qapp, tmp_path):
    from PySide6.QtWidgets import QMainWindow

    from app.agents_panel import AgentsDock

    host = QMainWindow()
    dock = AgentsDock(host)
    try:
        assert dock.profile is not QWebEngineProfile.defaultProfile()
        assert dock.view.page().profile() is dock.profile
        assert dock.profile.isOffTheRecord()
        assert dock.profile.storageName() == ""
        dock.refresh()  # not yet shown: nothing is loaded
        assert dock._loaded_url is None
        host.show()
        dock.show()
        qapp.processEvents()
        assert "未啟動" in dock._info_label.text()
        assert "start.bat" in dock._info_label.text()
        assert dock._redetect.text() == "重新偵測"
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_second_instance_path_never_builds_a_watcher(monkeypatch):
    """main() only starts the watchdog when it owns the IPC server."""
    import inspect

    import main

    src = inspect.getsource(main.main)
    gate = src.index("if _owns_ipc(_ipc_server):")
    assert "start_agent_watchdog" in src[gate:gate + 200]
    # the "lost the race" branch exits before reaching that gate
    assert src.index("Lost IPC startup race") < gate


def test_bring_to_front_cancels_deferred_update_close():
    from app.window import MainWindow

    calls = []
    fake = SimpleNamespace(
        _update_close_pending=True, _update_close_deferred=True,
        _deferred_update_close_approved=True, _session_closing=True,
        isVisible=lambda: False, isMinimized=lambda: False,
        show=lambda: calls.append("show"), showNormal=lambda: None,
        raise_=lambda: None, activateWindow=lambda: None,
        _agent_watch_resume=lambda: calls.append("resume"),
    )
    fake._cancel_deferred_update_close = (
        lambda: MainWindow._cancel_deferred_update_close(fake)
    )
    MainWindow.bring_to_front(fake)
    assert fake._update_close_pending is False
    assert fake._update_close_deferred is False
    assert fake._deferred_update_close_approved is False
    assert calls == ["resume", "show"]


def test_deferred_close_does_not_quit_after_wakeup(qapp):
    """The updater thread finishing after a wake-up must not close the app."""
    from app import update_flow

    class Win:
        _update_close_deferred = False
        _update_close_pending = False
        _deferred_update_close_approved = False

    from app.window import MainWindow

    win = Win()
    closed = []
    win.close = lambda: closed.append(1)
    win._update_close_deferred = True
    win._update_close_pending = True
    MainWindow._cancel_deferred_update_close(win)
    update_flow._resume_deferred_close(__import__("weakref").ref(win))
    qapp.processEvents()
    assert closed == []


def test_main_window_close_event_stops_the_watcher(
    qapp, mock_dog, watchers, tmp_path, monkeypatch
):
    import shiboken6

    from app import session_state
    from app import window as window_mod
    from app.recovery import RecoveryStore

    monkeypatch.setattr(window_mod, "RecoveryStore",
                        lambda: RecoveryStore(tmp_path / "recovery"))
    ini = tmp_path / "window.ini"
    iso = lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat)  # noqa: E731
    monkeypatch.setattr(window_mod, "QSettings", iso)
    monkeypatch.setattr(session_state, "QSettings", iso)
    store = settings_store.settings()
    store.setValue("watchdog/base_url", mock_dog.url)
    store.sync()
    mock_dog.delay = 0.6
    window = window_mod.MainWindow()
    try:
        window.start_agent_watchdog()
        watcher = window._agent_watcher
        watcher._endpoint_path = tmp_path / "none.json"
        watcher.start()
        assert wait_until(qapp, lambda: watcher.in_flight() > 0)
        window.close()
        assert watcher.in_flight() == 0
        assert not watcher._events_timer.isActive()
        assert not watcher._status_timer.isActive()
        window.bring_to_front()  # waking the window resumes the watcher
        assert watcher.running
        assert window._agent_badge is not None
    finally:
        window._agent_watcher.shutdown()
        window._preview_editing = False
        window.deleteLater()
        if shiboken6.isValid(window):
            window.hide()
        qapp.processEvents()


def test_agents_dock_shutdown_deletes_page_before_profile(qapp):
    import shiboken6
    from PySide6.QtWidgets import QMainWindow

    from app.agents_panel import AgentsDock

    host = QMainWindow()
    dock = AgentsDock(host)
    page = dock.view.page()
    dock.shutdown()
    dock.shutdown()  # idempotent
    assert not shiboken6.isValid(page)
    assert shiboken6.isValid(dock.profile)
    host.deleteLater()
    qapp.processEvents()


def test_window_calls_dock_shutdown_on_about_to_quit(qapp, tmp_path, monkeypatch):
    import shiboken6

    from app import agents_panel, session_state
    from app import window as window_mod
    from app.recovery import RecoveryStore

    monkeypatch.setattr(window_mod, "RecoveryStore",
                        lambda: RecoveryStore(tmp_path / "recovery"))
    ini = tmp_path / "window.ini"
    iso = lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat)  # noqa: E731
    monkeypatch.setattr(window_mod, "QSettings", iso)
    monkeypatch.setattr(session_state, "QSettings", iso)
    store = settings_store.settings()
    store.setValue("watchdog/enabled", False)
    store.sync()
    calls = []
    original = agents_panel.AgentsDock.shutdown

    def spy(self):
        calls.append(self)
        original(self)

    monkeypatch.setattr(agents_panel.AgentsDock, "shutdown", spy)
    window = window_mod.MainWindow()
    try:
        window._ensure_agents_dock()
        qapp.aboutToQuit.emit()
        assert calls == [window._agents_dock]
    finally:
        window._preview_editing = False
        window.deleteLater()
        if shiboken6.isValid(window):
            window.hide()
        qapp.processEvents()


def test_badge_kind_and_theme_colors(qapp):
    from app.agents_panel import AgentBadge, badge_colors
    from app.theme import DARK, LIGHT

    badge = AgentBadge(theme=DARK)
    assert badge.cursor().shape() == Qt.CursorShape.PointingHandCursor
    watcher = SimpleNamespace(state=SimpleNamespace(
        unavailable=False, _counts=lambda: (2, 1),
        badge_text=lambda: "x", tooltip=lambda: "tip"))
    badge.refresh(watcher)
    assert badge.kind == "stuck" and badge.toolTip() == "tip"
    watcher.state._counts = lambda: (1, 0)
    badge.refresh(watcher)
    assert badge.kind == "waiting"
    watcher.state._counts = lambda: (0, 0)
    badge.refresh(watcher)
    assert badge.kind == "idle"
    badge.refresh(None)
    assert badge.kind == "off" and badge.font().strikeOut()
    for kind in ("stuck", "waiting", "idle"):
        assert badge_colors(LIGHT, kind) != badge_colors(DARK, kind)


# ---------------- W1c robustness ----------------
def _count(script, path):
    return sum(1 for p, _, _ in script.log if p == path)


def _arm_pending(w, clock, lid="a", state="WAITING"):
    """Put one debounced toast into a running, available watcher."""
    w.state.attention[lid] = {"state": state, "project": lid, "agent": "Claude",
                              "host": "local", "logical_id": lid}
    w.state._pending[lid] = (clock.now + 25, state)


def test_legacy_endpoint_recovery_is_reprobed_and_toasts(
    qapp, mock_dog, watchers, monkeypatch
):
    monkeypatch.setattr(agent_watchdog, "LEGACY_REPROBE_S", 0.2)
    clock = SimpleNamespace(now=1000.0)
    mock_dog.events_status = 404
    w = watchers(mock_dog.url, clock=lambda: clock.now)
    shown = []
    w.notify = shown.append
    w.start()
    assert wait_until(qapp, lambda: w.state.legacy)
    mock_dog.events_status = 200            # the Watchdog was upgraded
    assert wait_until(qapp, lambda: not w.state.legacy and w.state.boot_id == BOOT)
    mock_dog.events = [event(6, "a", "WAITING")]
    mock_dog.latest = 6
    mock_dog.attention = [att("a")]
    assert wait_until(qapp, lambda: "a" in w.state._pending)
    clock.now += 30
    assert wait_until(qapp, lambda: len(shown) == 1)


def test_legacy_stays_legacy_while_probe_still_404(qapp, mock_dog, watchers, monkeypatch):
    monkeypatch.setattr(agent_watchdog, "LEGACY_REPROBE_S", 0.15)
    mock_dog.events_status = 404
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") >= 3)
    assert w.state.legacy and w.available
    assert _count(mock_dog, "/api/status") >= 3   # status polling keeps going


def test_recovery_from_unavailable_clears_legacy(qapp, watchers, mock_dog):
    w = watchers(mock_dog.url)
    w.state.set_legacy(True)
    w.state.set_unavailable(True)
    w._connected()
    assert w.available and not w.state.legacy


def test_single_failure_keeps_pending_and_base(qapp, mock_dog, watchers):
    clock = SimpleNamespace(now=1000.0)
    w = watchers(mock_dog.url, clock=lambda: clock.now)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)
    w._events_timer.stop()
    w._bases = [mock_dog.url, "http://127.0.0.1:9"]
    w._base_index = 0
    _arm_pending(w, clock)
    w._failed()
    assert w.available and w._base_index == 0
    assert "a" in w.state._pending and w.state.boot_id == BOOT
    assert w._events_timer.isActive()          # quick retry, chain alive


def test_two_consecutive_failures_mark_unavailable_and_keep_toast(
    qapp, mock_dog, watchers
):
    clock = SimpleNamespace(now=1000.0)
    w = watchers(mock_dog.url, clock=lambda: clock.now)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)
    w._events_timer.stop()
    w._bases = [mock_dog.url]
    w._base_index = 0
    _arm_pending(w, clock)
    w._failed()
    w._failed()
    assert not w.available
    assert "a" in w.state._pending            # kept across the outage
    w._connected()                             # service answers again
    clock.now += 30
    (toast,) = w.state.due_toasts()
    assert toast.ids == ("a",)


def test_success_between_failures_resets_the_count(qapp, mock_dog, watchers):
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.available)
    w._events_timer.stop()
    w._failed()
    w._connected()
    w._failed()
    assert w.available


def test_second_consecutive_failure_switches_base_and_aborts_inflight(
    qapp, mock_dog, watchers
):
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)
    w._events_timer.stop()
    w._status_timer.stop()
    assert wait_until(qapp, lambda: w.in_flight() == 0)
    w._bases = ["http://127.0.0.1:9", mock_dog.url]
    w._base_index = 0
    mock_dog.delay = 0.4
    w._get("/api/status", lambda r: None)
    assert w.in_flight() == 1
    generation = w._generation
    w._failed()
    assert w._base_index == 0
    w._failed()
    assert w._base_index == 1 and w._generation > generation
    assert w.in_flight() == 0                 # old base's request aborted
    assert w.state.boot_id is None            # stream re-baselines


def test_failed_reconciliation_status_does_not_affect_availability(
    qapp, mock_dog, watchers, monkeypatch
):
    monkeypatch.setattr(agent_watchdog, "STATUS_INTERVAL_MS", 50)
    mock_dog.status_status = 500
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/status") >= 3)
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") >= 3)
    assert w.available and w._failures == 0 and w._base_index == 0


@pytest.mark.parametrize("override", [
    {"schema": 2, "boot_id": BOOT, "latest_seq": 5, "truncated": False, "events": []},
    {"schema": 1, "boot_id": BOOT, "latest_seq": True, "truncated": False, "events": []},
    {"schema": 1, "boot_id": BOOT, "latest_seq": 5, "truncated": "false", "events": []},
    {"schema": 1, "boot_id": BOOT, "latest_seq": 5, "truncated": False,
     "events": [{"seq": 6, "logical_id": "a"}, {"seq": "7", "logical_id": "b"}]},
    [1, 2, 3],
])
def test_malformed_events_counts_as_failure_and_chain_survives(
    qapp, mock_dog, watchers, override
):
    mock_dog.events_override = override
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") >= 4)
    assert w.state.boot_id is None and not w.available
    mock_dog.events_override = None            # fixed: the chain recovers
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)


def test_handler_exception_never_breaks_the_polling_chain(qapp, mock_dog, watchers):
    w = watchers(mock_dog.url)
    original = w.state.apply_events
    calls = []

    def flaky(payload):
        calls.append(1)
        if len(calls) == 1:
            raise TypeError("boom")
        return original(payload)

    w.state.apply_events = flaky
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)
    assert len(calls) >= 2


def test_handler_exception_while_reconciliation_in_flight_keeps_polling(
        qapp, mock_dog, watchers):
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id == BOOT)
    assert wait_until(qapp, lambda: w.in_flight() == 0)
    w._events_timer.stop()

    def boom(payload):
        raise TypeError("boom")

    w.state.apply_events = boom
    mock_dog.delay = 0.6
    n = _count(mock_dog, "/api/status")
    w._poll_status()                     # reconciliation request, still in flight
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/status") > n)
    mock_dog.delay = 0
    w._poll_events()                     # its handler raises
    assert wait_until(qapp, lambda: w.in_flight() == 0, timeout=4)
    before = _count(mock_dog, "/api/events")
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") > before,
                      timeout=2)


def test_deeply_nested_json_is_treated_as_malformed():
    depth = 200_000
    body = b"[" * depth + b"]" * depth
    reply = SimpleNamespace(
        readAll=lambda: SimpleNamespace(data=lambda: body))
    assert AgentWatcher._json(reply) is None


def test_only_one_events_request_in_flight(qapp, mock_dog, watchers):
    mock_dog.delay = 0.4
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.in_flight() == 1)
    for _ in range(3):
        w._poll_events()
    assert w.in_flight() == 1
    w.start()                                  # restart aborts the old one
    assert wait_until(qapp, lambda: w.in_flight() == 1)
    w._poll_events()
    assert w.in_flight() == 1


def test_late_reply_from_an_old_generation_is_dropped(qapp, mock_dog, watchers):
    mock_dog.delay = 0.2
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.in_flight() == 1)
    w._events_timer.stop()
    w._status_timer.stop()
    seen = []
    w._get("/api/status", seen.append)
    w._generation += 1                         # e.g. the base was switched
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/status") >= 1)
    time.sleep(0.5)
    qapp.processEvents()
    assert seen == []


def test_start_clears_pending_toasts(qapp, mock_dog, watchers):
    clock = SimpleNamespace(now=1000.0)
    w = watchers(mock_dog.url, clock=lambda: clock.now)
    w.start()
    assert wait_until(qapp, lambda: w.available)
    _arm_pending(w, clock)
    w.pause()
    w.resume()                                 # window woken: start() again
    assert w.state._pending == {}


def test_redirect_is_a_failure_and_never_followed(
    qapp, mock_dog, other_dog, watchers
):
    mock_dog.events_status = 302
    mock_dog.location = other_dog.url + "/api/events"
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") >= 3)
    qapp.processEvents()
    assert not w.available
    assert other_dog.log == []                 # the redirect target was never hit


def test_flush_merges_waiting_and_stuck_into_one_notification(
    qapp, mock_dog, watchers
):
    clock = SimpleNamespace(now=1000.0)
    w = watchers(mock_dog.url, clock=lambda: clock.now)
    shown = []
    w.notify = shown.append
    base = {"schema": 1, "boot_id": BOOT, "latest_seq": 5, "truncated": False,
            "events": []}
    w.state.apply_events(base)
    w.state.apply_events({**base, "latest_seq": 7, "events": [
        event(6, "a", "WAITING"), event(7, "b", "STUCK")]})
    clock.now += 26
    w._flush_toasts()
    assert len(shown) == 1
    assert "1 個在等你、1 個可能卡住" in shown[0].message


def test_endpoint_boot_id_is_documented_as_diagnostic_only():
    assert "Diagnostics only" in (AgentWatcher.endpoint_boot_id.__doc__ or "")


def test_listen_failure_other_than_address_in_use_starts_no_poller(
    qapp, monkeypatch
):
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QWidget

    import main as main_mod

    monkeypatch.setattr(main_mod, "_IPC_SERVER_NAME", f"mv-test-{id(qapp)}-nolisten")
    monkeypatch.setattr(main_mod, "_ipc_server_is_alive", lambda: False)
    monkeypatch.setattr(QLocalServer, "listen", lambda self, name: False)
    owner = QWidget()
    try:
        server = main_mod._setup_ipc_server(owner)
        assert server is not None and not server.isListening()
        assert main_mod._owns_ipc(server) is False
        assert main_mod._owns_ipc(None) is False
    finally:
        owner.deleteLater()


def test_owns_ipc_true_for_a_really_listening_server(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    import main as main_mod

    monkeypatch.setattr(main_mod, "_IPC_SERVER_NAME", f"mv-test-{id(qapp)}-listen")
    owner = QWidget()
    server = main_mod._setup_ipc_server(owner)
    try:
        assert server is not None and main_mod._owns_ipc(server) is True
    finally:
        if server is not None:
            server.close()
        owner.deleteLater()


# ---------------- panel ----------------
def _panel_dock(watcher):
    from PySide6.QtWidgets import QMainWindow

    from app.agents_panel import AgentsDock

    host = QMainWindow()
    dock = AgentsDock(host)
    dock._watcher = watcher
    host.show()
    return host, dock


def test_panel_never_gets_narrower_than_the_star_map_needs(qapp):
    """Docked or floating, help page or dashboard: the page keeps its width."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    from app.agents_panel import PANEL_MIN_PAGE_WIDTH

    host, dock = _panel_dock(None)
    try:
        host.setCentralWidget(QWidget())
        host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        host.resize(1200, 600)
        dock.show()
        qapp.processEvents()
        assert dock.minimumSizeHint().width() >= PANEL_MIN_PAGE_WIDTH
        for page in (dock._info, dock._view):
            dock._stack.setCurrentWidget(page)
            host.resizeDocks([dock], [200], Qt.Orientation.Horizontal)
            qapp.processEvents()
            assert dock._stack.width() >= PANEL_MIN_PAGE_WIDTH
        dock._stack.setCurrentWidget(dock._view)
        dock.setFloating(True)
        dock.resize(200, 400)
        qapp.processEvents()
        assert dock._view.width() >= PANEL_MIN_PAGE_WIDTH
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_dashboard_page_navigation_allows_only_loopback_http(qapp, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWebEngineCore import QWebEnginePage

    from app import agents_panel

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()))
    host, dock = _panel_dock(None)
    page = dock._page
    assert isinstance(page, agents_panel._DashboardPage)
    link = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    typed = QWebEnginePage.NavigationType.NavigationTypeTyped
    try:
        assert page.acceptNavigationRequest(QUrl("http://127.0.0.1:8787/"), typed, True)
        assert page.acceptNavigationRequest(QUrl("http://[::1]:8787/x"), link, True)
        assert page.acceptNavigationRequest(QUrl("about:blank"), typed, True)
        for refused in ("https://127.0.0.1:8787/", "http://localhost:8787/",
                        "http://example.com/", "file:///C:/Windows/win.ini",
                        "javascript:alert(1)", "ftp://127.0.0.1/"):
            assert not page.acceptNavigationRequest(QUrl(refused), typed, True)
        # clicked external links go to the system browser, never in the panel
        assert not page.acceptNavigationRequest(QUrl("https://example.com/a"), link, True)
        assert not page.acceptNavigationRequest(QUrl("file:///C:/x.exe"), link, True)
        qapp.processEvents()
        assert opened == ["https://example.com/a"]
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_info_page_stops_the_dashboard_and_page_is_frozen_on_hide(qapp, mock_dog):
    from PySide6.QtWebEngineCore import QWebEnginePage

    watcher = SimpleNamespace(
        available=True, base_url=mock_dog.url,
        config=SimpleNamespace(enabled=True, start_bat="start.bat"),
        redetect=lambda: None)
    host, dock = _panel_dock(watcher)
    try:
        dock.show()
        assert wait_until(qapp, lambda: dock.view.url().host() == "127.0.0.1")
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
        watcher.available = False
        dock.refresh()
        assert dock._loaded_url is None
        assert dock._stack.currentWidget() is dock._info
        assert wait_until(qapp, lambda: dock.view.url().toString() == "about:blank")
        # hiding still freezes the (now blank) page: it is not skipped early
        dock.hide()
        assert wait_until(
            qapp,
            lambda: dock._page.lifecycleState()
            == QWebEnginePage.LifecycleState.Frozen,
        )
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_start_bat_default_is_unset_and_saved_value_is_kept(tmp_path, monkeypatch):
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        settings_store, "settings",
        lambda: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    assert agent_watchdog.DEFAULT_START_BAT == ""
    assert agent_watchdog.WatchdogConfig.load().start_bat == ""
    agent_watchdog.save_setting(agent_watchdog.KEY_START_BAT, r"C:\x\start.bat")
    assert agent_watchdog.WatchdogConfig.load().start_bat == r"C:\x\start.bat"


@pytest.mark.parametrize("bat", ["", r"C:\x\start.bat"])
def test_info_page_with_and_without_start_bat(qapp, bat):
    watcher = SimpleNamespace(
        available=False, base_url="http://127.0.0.1:1",
        config=SimpleNamespace(enabled=True, start_bat=bat),
        redetect=lambda: None)
    host, dock = _panel_dock(watcher)
    try:
        dock._show_info()
        text = dock._info_label.text()
        assert "未啟動" in text
        if bat:
            assert bat in text
        else:
            assert "尚未指定" in text and "watchdog/start_bat" in text
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


# ---------------- declared panel page ----------------
def _live_panel(watcher, theme=None, show=True):
    """Dock on a real watcher; show=False keeps it in the never-shown state."""
    from PySide6.QtWidgets import QMainWindow

    from app.agents_panel import AgentsDock

    host = QMainWindow()
    dock = AgentsDock(host) if theme is None else AgentsDock(host, theme)
    dock.set_watcher(watcher)
    if show:
        host.show()
    return host, dock


def _settle(qapp, seconds=0.3):
    wait_until(qapp, lambda: False, timeout=seconds)


def _close_panel(qapp, host, dock):
    dock.shutdown()
    host.close()
    host.deleteLater()
    qapp.processEvents()


def test_declared_panel_path_is_loaded_with_the_light_scheme(qapp, mock_dog, watchers):
    mock_dog.panel = {"path": "/office", "future": 1}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w, show=False)
    try:
        w.start()
        assert wait_until(qapp, lambda: w.panel_path == "/office")
        _settle(qapp)                              # several polls and refreshes
        assert dock._loaded_url is None            # still lazy: never shown
        assert not dock._page_loaded
        host.show()
        dock.show()
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
        assert wait_until(qapp, lambda: dock.view.url().path() == "/office")
        assert dock.view.url().query() == "scheme=light&" + PANEL_QUERY
        assert dock._stack.currentWidget() is dock.view
    finally:
        _close_panel(qapp, host, dock)


def test_no_declaration_still_loads_the_plain_root(qapp, mock_dog, watchers):
    from app.theme import DARK

    w = watchers(mock_dog.url)
    host, dock = _live_panel(w, DARK)
    try:
        w.start()
        assert wait_until(qapp, lambda: w.available)
        assert w.panel_path is None
        dock.show()
        assert wait_until(qapp, lambda: dock._loaded_url is not None)
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
        assert wait_until(qapp, lambda: dock.view.url().host() == "127.0.0.1")
        assert dock.view.url().toString() == mock_dog.url + "/?" + PANEL_QUERY
    finally:
        _close_panel(qapp, host, dock)


@pytest.mark.parametrize("panel", [
    [], "/office", {"path": "//evil.example/x"}, {"path": "/a?b=1"}, {"path": 7}, {},
])
def test_unusable_declaration_loads_the_plain_root_and_keeps_polling(
        qapp, mock_dog, watchers, panel):
    mock_dog.panel = panel
    mock_dog.attention = [att("a")]
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        assert wait_until(qapp, lambda: w.state.badge_text() == "🤖 1 等待中")
        assert w.available and w.panel_path is None
        dock.show()
        assert wait_until(qapp, lambda: dock._loaded_url is not None)
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
    finally:
        _close_panel(qapp, host, dock)


def test_dark_theme_and_theme_switch_reload_the_declared_page(qapp, mock_dog, watchers):
    from app.theme import DARK, LIGHT

    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w, DARK, show=False)
    try:
        w.start()
        assert wait_until(qapp, lambda: w.panel_path == "/office")
        dock.apply_theme(LIGHT)                    # never shown: nothing loads
        dock.apply_theme(DARK)
        _settle(qapp)
        assert dock._loaded_url is None and not dock._page_loaded
        host.show()
        dock.show()
        dark_url = mock_dog.url + "/office?scheme=dark&" + PANEL_QUERY
        light_url = mock_dog.url + "/office?scheme=light&" + PANEL_QUERY
        assert wait_until(qapp, lambda: dock._loaded_url == dark_url)
        dock.apply_theme(LIGHT)
        assert dock._loaded_url == light_url
        assert wait_until(qapp, lambda: dock.view.url().query() == "scheme=light&" + PANEL_QUERY)
        dock.apply_theme(LIGHT)                    # same theme: same URL, no reload
        assert dock._loaded_url == light_url
        # hidden: the frozen page is left alone, the next show catches up
        dock.hide()
        qapp.processEvents()
        dock.apply_theme(DARK)
        polls = _count(mock_dog, "/api/events")
        assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") >= polls + 3)
        _settle(qapp, 0.1)                         # refreshes ran while hidden
        assert dock._loaded_url == light_url
        assert dock.view.url().query() == "scheme=light&" + PANEL_QUERY
        dock.show()
        assert wait_until(qapp, lambda: dock._loaded_url == dark_url)
        assert wait_until(qapp, lambda: dock.view.url().query() == "scheme=dark&" + PANEL_QUERY)
    finally:
        _close_panel(qapp, host, dock)


def test_theme_switch_on_the_plain_root_or_help_page_loads_nothing(qapp, mock_dog):
    from app.theme import DARK, LIGHT

    watcher = SimpleNamespace(
        available=True, base_url=mock_dog.url,
        config=SimpleNamespace(enabled=True, start_bat="start.bat"),
        redetect=lambda: None)
    host, dock = _panel_dock(watcher)
    started = []
    dock._page.loadStarted.connect(lambda: started.append(dock._loaded_url))

    try:
        dock.show()
        assert wait_until(qapp, lambda: dock.view.url().host() == "127.0.0.1")
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
        assert wait_until(qapp, lambda: _count(mock_dog, "/") == 1)
        dock.apply_theme(LIGHT)
        dock.apply_theme(DARK)
        _settle(qapp)
        assert started == [mock_dog.url + "/?" + PANEL_QUERY]
        assert _count(mock_dog, "/") == 1
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
        assert dock.view.url().toString() == mock_dog.url + "/?" + PANEL_QUERY
        watcher.available = False
        dock.refresh()                             # help page (about:blank)
        assert wait_until(qapp, lambda: dock.view.url().toString() == "about:blank")
        loads = len(started)
        dock.apply_theme(LIGHT)
        _settle(qapp)
        assert len(started) == loads
        assert _count(mock_dog, "/") == 1
        assert dock._loaded_url is None
        assert dock.view.url().toString() == "about:blank"
        assert dock._stack.currentWidget() is dock._info
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_declaration_appearing_changing_and_vanishing_follows_on_refresh(
        qapp, mock_dog, watchers):
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        dock.show()
        assert wait_until(qapp, lambda: dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY)
        mock_dog.panel = {"path": "/office"}
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
        mock_dog.panel = {"path": "/desk"}
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/desk?scheme=light&" + PANEL_QUERY)
        mock_dog.panel = None
        assert wait_until(qapp, lambda: dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY)
        assert not dock._loaded_declared
    finally:
        _close_panel(qapp, host, dock)


def test_falling_back_to_legacy_mode_returns_to_the_plain_root(
        qapp, mock_dog, watchers, monkeypatch):
    monkeypatch.setattr(agent_watchdog, "LEGACY_REPROBE_S", 0.0)
    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        dock.show()
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
        mock_dog.events_status = 404               # replaced by an old service
        assert wait_until(qapp, lambda: w.state.legacy)
        assert wait_until(qapp, lambda: dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY)
        assert w.available and w.panel_path is None
        mock_dog.events_status = 200               # upgraded again
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
    finally:
        _close_panel(qapp, host, dock)


def test_unavailable_or_malformed_answers_drop_the_declaration(qapp, mock_dog, watchers):
    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        dock.show()
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
        mock_dog.events_override = {"schema": 2, "panel": {"path": "/office"}}
        assert wait_until(qapp, lambda: not w.available)
        assert w.panel_path is None and w.state.panel_path is None
        assert wait_until(qapp, lambda: dock._stack.currentWidget() is dock._info)
        assert dock._loaded_url is None and not dock._loaded_declared
    finally:
        _close_panel(qapp, host, dock)


def test_window_hands_the_theme_to_the_agents_dock(qapp, tmp_path, monkeypatch):
    from app import session_state
    from app import window as window_mod
    from app.recovery import RecoveryStore

    monkeypatch.setattr(window_mod, "RecoveryStore",
                        lambda: RecoveryStore(tmp_path / "recovery"))
    ini = tmp_path / "window.ini"
    iso = lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat)  # noqa: E731
    monkeypatch.setattr(window_mod, "QSettings", iso)
    monkeypatch.setattr(session_state, "QSettings", iso)
    # Never the real settings: they belong to the viewer installed on this machine.
    monkeypatch.setattr(settings_store, "settings", iso)
    store = settings_store.settings()
    store.setValue("watchdog/enabled", False)
    store.sync()
    window = window_mod.MainWindow()
    try:
        assert window._agents_dock is None
        window._apply_theme()                      # no dock yet: must not fail
        dock = window._ensure_agents_dock()
        assert dock._theme is window._theme        # created with the current theme
        window._theme_name = "light" if window._theme.name == "dark" else "dark"
        window._apply_theme()
        assert dock._theme is window._theme
        assert dock._theme.name == window._theme_name
    finally:
        window._preview_editing = False
        if window._agents_dock is not None:
            window._agents_dock.shutdown()
        window.deleteLater()
        if shiboken6.isValid(window):
            window.hide()
        qapp.processEvents()


def test_hidden_panel_still_follows_a_changed_path(qapp, mock_dog, watchers):
    """Only a scheme-only difference is postponed while hidden."""
    from app.theme import DARK

    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        dock.show()
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
        dock.hide()
        qapp.processEvents()
        dock.apply_theme(DARK)
        mock_dog.panel = {"path": "/desk"}
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/desk?scheme=dark&" + PANEL_QUERY)
        mock_dog.panel = None
        assert wait_until(qapp, lambda: dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY)
    finally:
        _close_panel(qapp, host, dock)


def test_restart_on_another_base_forgets_the_declaration(
        qapp, mock_dog, other_dog, watchers):
    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        dock.show()
        assert wait_until(
            qapp, lambda: dock._loaded_url == mock_dog.url + "/office?scheme=light&" + PANEL_QUERY)
        agent_watchdog.save_setting(agent_watchdog.KEY_BASE_URL, other_dog.url)
        w.start()
        assert w.base_url == other_dog.url
        assert w.available and w.panel_path is None and w.state.panel_path is None
        dock.refresh()
        assert dock._loaded_url == other_dog.url + "/?" + PANEL_QUERY
        assert wait_until(qapp, lambda: _count(other_dog, "/api/status") >= 1)
        _settle(qapp, 0.2)
        assert w.panel_path is None
        assert dock._loaded_url == other_dog.url + "/?" + PANEL_QUERY
        assert wait_until(
            qapp, lambda: dock.view.url().toString() == other_dog.url + "/?" + PANEL_QUERY)
    finally:
        _close_panel(qapp, host, dock)


def test_pause_and_resume_ask_for_the_declaration_again(qapp, mock_dog, watchers):
    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    w.start()
    assert wait_until(qapp, lambda: w.panel_path == "/office")
    w.pause()
    mock_dog.panel = None
    polls = _count(mock_dog, "/api/events")
    w.resume()
    assert w.panel_path is None
    assert wait_until(qapp, lambda: _count(mock_dog, "/api/events") >= polls + 3)
    assert w.available and w.panel_path is None


def test_single_malformed_answer_keeps_the_declared_page(
        qapp, mock_dog, watchers, monkeypatch):
    from app.theme import DARK

    monkeypatch.setattr(agent_watchdog, "FAILURES_BEFORE_UNAVAILABLE", 10**6)
    mock_dog.panel = {"path": "/office"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w)
    try:
        w.start()
        dock.show()
        light_url = mock_dog.url + "/office?scheme=light&" + PANEL_QUERY
        dark_url = mock_dog.url + "/office?scheme=dark&" + PANEL_QUERY
        assert wait_until(qapp, lambda: dock._loaded_url == light_url)
        mock_dog.events_override = {"schema": 2}
        assert wait_until(qapp, lambda: w._failures >= 2)   # malformed, retrying
        assert w.available and w.panel_path == "/office"
        dock.refresh()
        assert dock._loaded_url == light_url
        dock.apply_theme(DARK)
        assert dock._loaded_url == dark_url
        dock.hide()
        qapp.processEvents()
        dock.show()
        qapp.processEvents()
        assert dock._loaded_url == dark_url
        assert dock._stack.currentWidget() is dock.view
    finally:
        _close_panel(qapp, host, dock)


def test_declared_root_loads_the_plain_root_without_a_scheme(qapp, mock_dog, watchers):
    from app.theme import DARK, LIGHT

    mock_dog.panel = {"path": "/"}
    w = watchers(mock_dog.url)
    host, dock = _live_panel(w, DARK)
    try:
        w.start()
        assert wait_until(qapp, lambda: w.available)
        assert w.panel_path is None
        dock.show()
        assert wait_until(qapp, lambda: dock._loaded_url is not None)
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
        assert not dock._loaded_declared
        assert wait_until(qapp, lambda: _count(mock_dog, "/") == 1)
        dock.apply_theme(LIGHT)
        _settle(qapp)
        assert dock._loaded_url == mock_dog.url + "/?" + PANEL_QUERY
        assert _count(mock_dog, "/") == 1
        assert dock.view.url().toString() == mock_dog.url + "/?" + PANEL_QUERY
    finally:
        _close_panel(qapp, host, dock)
