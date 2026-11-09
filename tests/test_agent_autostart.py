"""Watchdog auto-start: install.json validation, probing, spawn, pacing.

Nothing here starts a real process: the launcher, Popen and the clock are
injected, and the HTTP side is a loopback mock server.
"""

import json
import socket
import subprocess
import sys

import pytest
import shiboken6
from PySide6.QtCore import QEvent, QSettings, QThreadPool

from app import agent_autostart as au
from app import agent_watchdog, settings_store
from app.agent_watchdog import AgentWatcher
from app.agent_watchdog_state import Backoff
from tests.test_agent_watchdog_qt import mock_dog, wait_until  # noqa: F401

# --- helpers ---------------------------------------------------------------


@pytest.fixture
def layout(tmp_path):
    """A valid Watchdog install on disk; returns (home, install dict)."""
    home = tmp_path / "home"
    home.mkdir()
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "aiwatch.py").write_text("", encoding="utf-8")
    exe = tmp_path / "pythonw.exe"
    exe.write_text("", encoding="utf-8")
    install = {
        "schema": 1,
        "launch": [str(exe), "-X", "utf8", str(wd / "aiwatch.py"),
                   "--serve", "--background"],
        "cwd": str(wd), "autostart": True, "updated_at": 1.0,
    }
    (home / "install.json").write_text(json.dumps(install), encoding="utf-8")
    return home, install


def write_install(home, install):
    (home / "install.json").write_text(json.dumps(install), encoding="utf-8")


def dead_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make(qapp, tmp_path, monkeypatch):
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        settings_store, "settings",
        lambda: QSettings(str(ini), QSettings.Format.IniFormat))
    monkeypatch.setattr(agent_watchdog, "EVENTS_INTERVAL_MS", 40)
    monkeypatch.setattr(agent_watchdog, "RETRY_AFTER_FAILURE_MS", 20)
    monkeypatch.setattr(au, "FAST_PROBE_INTERVAL_MS", 40)
    created = []

    def build(base_url, home, autostart=True):
        store = settings_store.settings()
        store.setValue("watchdog/base_url", base_url)
        store.setValue("watchdog/autostart", autostart)
        store.sync()
        calls = []
        clock = Clock()

        def launcher(launch, cwd):
            calls.append((launch, cwd))
            return ""

        w = AgentWatcher(endpoint_path=home / "endpoint.json", use_tray=False,
                         launcher=launcher, autostart_clock=clock)
        w._backoff = Backoff((0.03, 0.06))
        w.calls = calls
        w.clock = clock
        created.append(w)
        return w

    yield build
    for w in created:
        w.shutdown()
    assert QThreadPool.globalInstance().waitForDone(15000)
    qapp.processEvents()
    for w in created:
        if shiboken6.isValid(w):
            w.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def two_failures(w, http_status=0):
    """Force a confirmed failure on the single configured base."""
    w._bases = [w.config.base_url]
    w._base_index = 0
    w._failed(http_status)
    w._failed(http_status)


# --- 1. install.json validation ---------------------------------------------


def test_valid_install_passes(layout):
    home, install = layout
    check = au.load_install(home)
    assert check.ok and check.launch == install["launch"] and check.cwd == install["cwd"]


def test_missing_install_json_points_to_register(tmp_path):
    check = au.load_install(tmp_path / "nope")
    assert not check.ok and "--register" in check.reason


def test_corrupt_install_json(layout):
    home, _ = layout
    (home / "install.json").write_text("{not json", encoding="utf-8")
    assert not au.load_install(home).ok




BAD = {
    "schema_2": (lambda i, t: i.update(schema=2), "schema"),
    "schema_bool": (lambda i, t: i.update(schema=True), "schema"),
    "schema_missing": (lambda i, t: i.pop("schema"), "schema"),
    "autostart_false": (lambda i, t: i.update(autostart=False), "autostart"),
    "autostart_string": (lambda i, t: i.update(autostart="true"), "autostart"),
    "launch_not_list": (lambda i, t: i.update(launch="x"), "launch"),
    "launch_short": (lambda i, t: i.update(launch=i["launch"][:5]), "launch"),
    "launch_long": (lambda i, t: i.update(launch=i["launch"] + ["x"]), "launch"),
    "launch_non_string": (lambda i, t: i["launch"].__setitem__(1, 5), "launch"),
    "wrong_flag_X": (lambda i, t: i["launch"].__setitem__(1, "-O"), "launch"),
    "wrong_flag_utf8": (lambda i, t: i["launch"].__setitem__(2, "utf-8"), "launch"),
    "wrong_serve": (lambda i, t: i["launch"].__setitem__(4, "--once"), "launch"),
    "wrong_background": (lambda i, t: i["launch"].__setitem__(5, "--x"), "launch"),
    "exe_not_python": (
        lambda i, t: i["launch"].__setitem__(0, str(t / "notepad.exe")), "python"),
    "exe_relative": (lambda i, t: i["launch"].__setitem__(0, "pythonw.exe"), "python"),
    "script_not_aiwatch": (
        lambda i, t: i["launch"].__setitem__(3, str(t / "wd" / "other.py")), "aiwatch"),
    "script_relative": (lambda i, t: i["launch"].__setitem__(3, "aiwatch.py"), "aiwatch"),
    "cwd_relative": (lambda i, t: i.update(cwd="wd"), "cwd"),
    "cwd_not_string": (lambda i, t: i.update(cwd=None), "cwd"),
    "exe_missing": (
        lambda i, t: i["launch"].__setitem__(0, str(t / "gone" / "python.exe")),
        "--register"),
    "script_missing": (
        lambda i, t: i["launch"].__setitem__(3, str(t / "wd" / "x" / "aiwatch.py")),
        "--register"),
    "cwd_missing": (lambda i, t: i.update(cwd=str(t / "gone")), "--register"),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_invalid_install_is_rejected_with_a_reason(layout, tmp_path, name):
    home, install = layout
    mutate, keyword = BAD[name]
    mutate(install, tmp_path)
    write_install(home, install)
    check = au.load_install(home)
    assert not check.ok
    assert check.reason and keyword in check.reason


def test_install_json_is_never_written(layout):
    home, _ = layout
    path = home / "install.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    au.load_install(home)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert sorted(p.name for p in home.iterdir()) == ["install.json"]


@pytest.mark.parametrize("name", ["schema_2", "autostart_false", "launch_short",
                                  "exe_not_python", "exe_missing", "cwd_missing"])
def test_invalid_install_never_launches_and_shows_reason(
        qapp, layout, tmp_path, make, name):
    home, install = layout
    mutate, keyword = BAD[name]
    mutate(install, tmp_path)
    write_install(home, install)
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == []
    assert keyword in w.autostart_message
    assert w._events_timer.interval() != au.FAST_PROBE_INTERVAL_MS


# --- 2. probing ---------------------------------------------------------------


def test_stale_endpoint_json_and_dead_service_starts(qapp, layout, make):
    home, _ = layout
    (home / "endpoint.json").write_text(json.dumps({
        "schema": 1, "port": 9, "pid": 1, "boot_id": "stale", "started_at": 1.0,
        "url": dead_url()}), encoding="utf-8")
    w = make(dead_url(), home)
    w.start()
    assert wait_until(qapp, lambda: len(w.calls) == 1)
    assert w.calls[0][1] == str(home.parent / "wd")


def test_live_service_via_endpoint_json_does_not_start(qapp, layout, make, mock_dog):  # noqa: F811
    home, _ = layout
    (home / "endpoint.json").write_text(json.dumps({"schema": 1, "url": mock_dog.url}),
                                        encoding="utf-8")
    w = make(dead_url(), home)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id is not None)
    qapp.processEvents()
    assert w.calls == []


def test_live_service_on_default_url_does_not_start(qapp, layout, make, mock_dog):  # noqa: F811
    home, _ = layout
    (home / "endpoint.json").write_text(json.dumps({"schema": 1, "url": dead_url()}),
                                        encoding="utf-8")
    w = make(mock_dog.url, home)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id is not None)
    assert w.calls == []


def test_non_contract_http_answer_means_port_busy_and_no_start(
        qapp, layout, make, mock_dog):  # noqa: F811
    home, _ = layout
    mock_dog.events_override = {"hello": "world"}  # 200 but not the contract
    w = make(mock_dog.url, home)
    w.start()
    assert wait_until(qapp, lambda: "port 被佔用" in w.autostart_message)
    assert w.calls == []


def test_http_error_answer_means_port_busy_and_no_start(qapp, layout, make, mock_dog):  # noqa: F811
    home, _ = layout
    mock_dog.events_status = 500
    w = make(mock_dog.url, home)
    w.start()
    assert wait_until(qapp, lambda: "port 被佔用" in w.autostart_message)
    assert w.calls == []


def test_busy_port_check_uses_http_status_of_the_failure(qapp, layout, make):
    home, _ = layout
    w = make(dead_url(), home)
    two_failures(w, http_status=200)
    assert w.calls == [] and "port 被佔用" in w.autostart_message


# --- 3. viewer setting ---------------------------------------------------------


def test_viewer_setting_default_is_on(qapp, tmp_path, monkeypatch):
    ini = tmp_path / "s.ini"
    monkeypatch.setattr(settings_store, "settings",
                        lambda: QSettings(str(ini), QSettings.Format.IniFormat))
    assert agent_watchdog.WatchdogConfig.load().autostart is True
    agent_watchdog.save_setting(agent_watchdog.KEY_AUTOSTART, False)
    assert agent_watchdog.WatchdogConfig.load().autostart is False


def test_viewer_setting_off_never_launches(qapp, layout, make):
    home, _ = layout
    w = make(dead_url(), home, autostart=False)
    two_failures(w)
    assert w.calls == [] and "關閉" in w.autostart_message


def test_setting_on_and_install_autostart_true_launches_and_fast_probes(
        qapp, layout, make):
    home, install = layout
    w = make(dead_url(), home, autostart=True)
    w._backoff.next_delay()
    two_failures(w)
    assert w.calls == [(install["launch"], install["cwd"])]
    assert w._events_timer.interval() == au.FAST_PROBE_INTERVAL_MS
    assert w._backoff._index == 0  # back-off was reset by the decision


# --- 4. spawn ------------------------------------------------------------------


class FakePopen:
    calls: list = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        FakePopen.calls.append(self)

    def kill(self):
        raise AssertionError("must never kill the Watchdog")

    terminate = send_signal = wait = kill


def test_spawn_arguments_and_flags(monkeypatch):
    FakePopen.calls = []
    env = {"PATH": "C:\\x", "KEEP": "1", "_MEIPASS2": "a", "_PYI_APPLICATION_HOME_DIR": "b",
           "PYTHONHOME": "c", "PYTHONPATH": "d", "pythonpath": "e"}
    err = au.spawn_detached(["py", "a"], "C:\\wd", popen=FakePopen, environ=env)
    assert err == ""
    (call,) = FakePopen.calls
    kw = call.kwargs
    assert call.args == (["py", "a"],)
    assert kw["shell"] is False and kw["cwd"] == "C:\\wd"
    assert kw["creationflags"] == (
        au.DETACHED_PROCESS | au.CREATE_NEW_PROCESS_GROUP | au.CREATE_NO_WINDOW
        | au.CREATE_BREAKAWAY_FROM_JOB)
    assert (kw["stdin"], kw["stdout"], kw["stderr"]) == (subprocess.DEVNULL,) * 3
    assert kw["env"] == {"PATH": "C:\\x", "KEEP": "1"}


def test_breakaway_refused_retries_without_the_flag():
    seen = []

    def popen(launch, **kw):
        seen.append(kw["creationflags"])
        if kw["creationflags"] & au.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("job does not allow breakaway")
        return FakePopen(launch, **kw)

    FakePopen.calls = []
    assert au.spawn_detached(["py"], "C:\\", popen=popen, environ={}) == ""
    assert len(seen) == 2
    assert seen[1] == au.DETACHED_PROCESS | au.CREATE_NEW_PROCESS_GROUP | au.CREATE_NO_WINDOW


def test_spawn_failure_returns_text_and_does_not_raise():
    def popen(*a, **k):
        raise OSError("nope")

    assert "nope" in au.spawn_detached(["py"], "C:\\", popen=popen, environ={})


def test_pyinstaller_environment_is_cleaned(monkeypatch):
    root = "C:\\Temp\\_MEI123"
    env = {"PATH": f"{root};C:\\Windows;{root}\\sub;C:\\Python", "_MEI9": "1"}
    out = au.clean_environment(env, frozen=True, meipass=root)
    assert out["PATH"] == "C:\\Windows;C:\\Python".replace(";", __import__("os").pathsep)
    not_frozen = au.clean_environment(env, frozen=False, meipass=root)
    assert root in not_frozen["PATH"] and "_MEI9" not in not_frozen
    monkeypatch.setattr(sys, "frozen", False, raising=False)


# --- 5. pacing -------------------------------------------------------------------


def test_fast_probe_window_then_back_to_backoff_and_cooldown(qapp, layout, make):
    home, _ = layout
    w = make(dead_url(), home)
    two_failures(w)
    assert len(w.calls) == 1
    w.clock.now += 5  # inside the 20 s window: probe every 2 s, no new launch
    w._failed()
    assert w._events_timer.interval() == au.FAST_PROBE_INTERVAL_MS
    assert len(w.calls) == 1
    w.clock.now += 20  # window over (t=25), cooldown (60 s) still running
    w._failed()
    assert w._events_timer.interval() != au.FAST_PROBE_INTERVAL_MS
    assert len(w.calls) == 1
    w.clock.now += 40  # t=65 >= 60 after the first attempt
    w._failed()
    assert len(w.calls) == 2


def test_at_most_three_attempts_then_manual_retry_resets(qapp, layout, make):
    home, _ = layout
    w = make(dead_url(), home)
    for _ in range(5):
        two_failures(w)
        w.clock.now += 61  # also lets any fast window lapse
        w.clock.now += au.FAST_PROBE_WINDOW_S
    assert len(w.calls) == 3
    assert "重新偵測" in w.autostart_message
    w.redetect()  # manual retry: budget and cooldown reset
    two_failures(w)
    assert len(w.calls) == 4
    assert w._policy.attempts == 1


def test_launch_error_counts_as_an_attempt_and_shows_text(qapp, layout, make):
    home, _ = layout
    w = make(dead_url(), home)
    w._launcher = lambda launch, cwd: "無法啟動 Watchdog：boom"
    two_failures(w)
    assert "boom" in w.autostart_message and w._policy.attempts == 1
    assert w._events_timer.interval() != au.FAST_PROBE_INTERVAL_MS


def test_connected_clears_the_message(qapp, layout, make):
    home, _ = layout
    w = make(dead_url(), home)
    two_failures(w)
    assert w.autostart_message
    w._connected()
    assert w.autostart_message == ""


# --- 6. shutdown ------------------------------------------------------------------


def test_closing_the_viewer_kills_nothing_and_sends_no_shutdown(
        qapp, layout, make, monkeypatch):
    from PySide6.QtNetwork import QNetworkAccessManager

    def forbidden(*a, **k):
        raise AssertionError("no POST / custom request may be sent")

    monkeypatch.setattr(QNetworkAccessManager, "post", forbidden)
    monkeypatch.setattr(QNetworkAccessManager, "put", forbidden)
    monkeypatch.setattr(QNetworkAccessManager, "sendCustomRequest", forbidden)
    FakePopen.calls = []
    home, _ = layout
    w = make(dead_url(), home)
    w._launcher = lambda launch, cwd: au.spawn_detached(
        launch, cwd, popen=FakePopen, environ={})
    two_failures(w)
    assert len(FakePopen.calls) == 1
    w.pause()
    w.shutdown()  # FakePopen.kill/terminate/wait would raise
    qapp.processEvents()
    assert not any(isinstance(v, FakePopen) for v in vars(w).values())
    assert w.calls == []


# --- 7. AIWATCH_HOME ----------------------------------------------------------------


def test_aiwatch_home_decides_both_file_locations(tmp_path, monkeypatch):
    monkeypatch.setenv("AIWATCH_HOME", str(tmp_path / "custom"))
    assert au.aiwatch_home() == tmp_path / "custom"
    assert agent_watchdog.default_endpoint_path() == tmp_path / "custom" / "endpoint.json"
    w = AgentWatcher(use_tray=False)
    try:
        assert w._home == tmp_path / "custom"
    finally:
        w.shutdown()
        w.deleteLater()


def test_default_home_is_dot_aiwatch(monkeypatch):
    monkeypatch.delenv("AIWATCH_HOME", raising=False)
    assert au.aiwatch_home().name == ".aiwatch"
    monkeypatch.setenv("AIWATCH_HOME", "  ")
    assert au.aiwatch_home().name == ".aiwatch"


def test_watcher_reads_install_json_from_aiwatch_home(qapp, layout, make, monkeypatch):
    home, install = layout
    monkeypatch.setenv("AIWATCH_HOME", str(home))
    seen = []
    w = AgentWatcher(use_tray=False, launcher=lambda ln, c: seen.append((ln, c)) or "",
                     autostart_clock=Clock())
    try:
        w.config.base_url = dead_url()
        two_failures(w)
        assert seen == [(install["launch"], install["cwd"])]
    finally:
        w.shutdown()
        w.deleteLater()
