"""Changing the Watchdog's port: --set-port, settings.json, discovery, notice.

No test talks to a real Watchdog or touches the real ~/.aiwatch: AIWATCH_HOME
is a temp directory (conftest), settings live in a temp ini, the processes
run are tiny scripts or fake bundles, and only processes started here are
ever ended.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess, QSettings
from PySide6.QtWidgets import QMainWindow

from app import agent_autostart as au
from app import agent_bundle as ab
from app import agent_port as ap
from app import agent_watchdog, settings_store
from app.agent_watchdog import AgentWatcher, address_notice_text
from app.agents_panel import AgentsDock
from app.theme import LIGHT
from tests.test_agent_bundle import ALLOWED_ARGS, REFUSED_ARGS, make_bundle
from tests.test_agent_watchdog_qt import wait_until

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"


@pytest.fixture
def ini(tmp_path, monkeypatch):
    path = tmp_path / "settings.ini"
    monkeypatch.setattr(settings_store, "settings",
                        lambda: QSettings(str(path), QSettings.Format.IniFormat))
    return path


def _home():
    home = au.aiwatch_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def _write_settings(home, payload, raw=None):
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    (home / "settings.json").write_bytes(data)


def _legacy(value):
    store = settings_store.settings()
    store.setValue(agent_watchdog.KEY_BASE_URL, value)
    store.sync()


def _legacy_value():
    return settings_store.settings().value(agent_watchdog.KEY_BASE_URL)


# --- 1. the --agent-watchdog allowlist ---------------------------------------------


@pytest.mark.parametrize("args", [
    ["--set-port", "6868"], ["--set-port=6868"], ["--set-port", "1"],
    ["--set-port=65535"], ["--set-port", "08787"],
])
def test_set_port_alone_is_allowed(args):
    assert ab.arguments_allowed(args)


@pytest.mark.parametrize("args", [
    ["--set-port"], ["--set-port="], ["--set-port", "0"], ["--set-port=0"],
    ["--set-port", "65536"], ["--set-port", "-1"], ["--set-port", "abc"],
    ["--set-port", "６８６８"], ["--set-port", " 6868"], ["--set-port", None],
    ["--set-port", "6868", "--serve"], ["--serve", "--set-port", "6868"],
    ["--set-port", "6868", "--port", "1"], ["--port", "1", "--set-port=6868"],
    ["--set-port=6868", "--background"], ["--set-port", "6868", "6869"],
    ["--set-port", "--port"], ["--set-port=6868", "--set-port=6869"],
    ["--set-port", "6868", "--set-port", "6869"], ["--set-por", "6868"],
    ["--set-ports", "6868"], ["--SET-PORT", "6868"], ["--set-port=6868=1"],
])
def test_set_port_combined_or_malformed_is_refused(args):
    assert not ab.arguments_allowed(args)


def test_everything_allowed_before_is_still_allowed_and_refused_still_refused():
    assert all(ab.arguments_allowed(args) for args in ALLOWED_ARGS)
    assert not any(ab.arguments_allowed(args) for args in REFUSED_ARGS)


def test_run_bundled_refuses_a_combined_set_port_without_running(tmp_path, monkeypatch):
    folder = make_bundle(tmp_path / "b" / ab.BUNDLE_DIR_NAME)
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    assert ab.run_bundled(["--serve", "--set-port", "6868"]) == ab.EXIT_BAD_ARGUMENTS


# --- 2. settings.json ----------------------------------------------------------------


@pytest.mark.parametrize("payload, expected", [
    ({"schema": 1, "port": 6868, "updated_at": "x"}, 6868),
    ({"schema": 2, "port": 6868, "new": {"a": 1}}, 6868),   # schema and extras ignored
    ({"port": 1}, 1), ({"port": 65535}, 65535),
    ({"schema": 1}, None), ({"port": 0}, None), ({"port": 65536}, None),
    ({"port": -5}, None), ({"port": "6868"}, None), ({"port": 6868.0}, None),
    ({"port": True}, None), ({"port": None}, None), ([6868], None), ("6868", None),
])
def test_port_setting_values(payload, expected):
    home = _home()
    _write_settings(home, payload)
    assert au.load_port_setting(home) == expected


@pytest.mark.parametrize("raw", [
    b"", b"{not json", b"\xff\xfe\x00garbage", b"[" * 60000, b"null", b"6868",
], ids=["empty", "broken", "bad-utf8", "deep", "null", "bare-int"])
def test_garbage_port_setting_is_not_set_and_never_raises(raw):
    home = _home()
    _write_settings(home, None, raw)
    assert au.load_port_setting(home) is None


def test_port_setting_with_bom_missing_oversized_or_a_folder(tmp_path):
    home = _home()
    _write_settings(home, None, "﻿".encode() + b'{"schema": 1, "port": 6868}')
    assert au.load_port_setting(home) == 6868
    _write_settings(home, None, b'{"port": 6868, "pad": "' + b"x" * 70000 + b'"}')
    assert au.load_port_setting(home) is None  # oversized
    assert au.load_port_setting(tmp_path / "missing") is None
    folder = tmp_path / "dir-home"
    (folder / "settings.json").mkdir(parents=True)
    assert au.load_port_setting(folder) is None


def test_port_setting_is_only_read(tmp_path):
    home = _home()
    _write_settings(home, {"schema": 1, "port": 6868})
    path = home / "settings.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    au.load_port_setting(home)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


# --- 3. discovery ----------------------------------------------------------------------


@pytest.fixture
def watcher(qapp, ini, tmp_path):
    created = []

    def build(legacy=None, fallback=8787):
        if legacy is not None:
            _legacy(legacy)
        home = _home()
        w = AgentWatcher(endpoint_path=home / "endpoint.json", use_tray=False)
        if fallback is not None:
            w.fallback_port = fallback  # only for discovery: no test here polls it
        created.append(w)
        return w

    yield build
    for w in created:
        w.shutdown()
        w.deleteLater()


def _endpoint(url):
    (_home() / "endpoint.json").write_text(json.dumps({"schema": 1, "url": url}),
                                           encoding="utf-8")


def test_discovery_order_endpoint_settings_legacy_then_8787(watcher):
    _endpoint("http://127.0.0.1:7001")
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w = watcher(legacy="http://127.0.0.1:9000")
    assert w._discover_bases() == [
        "http://127.0.0.1:7001", "http://127.0.0.1:6868",
        "http://127.0.0.1:9000", "http://127.0.0.1:8787"]
    assert w.remembered_port == 6868


def test_discovery_without_any_setting_is_endpoint_then_8787(watcher):
    w = watcher()
    assert w._discover_bases() == ["http://127.0.0.1:8787"]
    _endpoint("http://127.0.0.1:7001/")
    assert w._discover_bases() == ["http://127.0.0.1:7001", "http://127.0.0.1:8787"]
    assert w.remembered_port is None and w.configured_base is None


def test_discovery_deduplicates(watcher):
    _endpoint("http://127.0.0.1:6868/")
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w = watcher(legacy="http://127.0.0.1:6868/")
    assert w._discover_bases() == ["http://127.0.0.1:6868", "http://127.0.0.1:8787"]
    _write_settings(_home(), {"schema": 1, "port": 8787})
    _endpoint("http://127.0.0.1:8787")
    assert w._discover_bases() == ["http://127.0.0.1:8787", "http://127.0.0.1:6868"]


def test_default_legacy_value_is_not_an_extra_candidate(watcher):
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w = watcher(legacy=agent_watchdog.DEFAULT_BASE_URL)
    assert w._discover_bases() == ["http://127.0.0.1:6868", "http://127.0.0.1:8787"]


@pytest.mark.parametrize("raw", [b"{broken", b'{"port": "6868"}', b"\x00\x01"],
                         ids=["broken", "string-port", "binary"])
def test_garbage_settings_json_is_skipped_in_discovery(watcher, raw):
    _write_settings(_home(), None, raw)
    w = watcher(legacy="http://127.0.0.1:9000")
    assert w._discover_bases() == ["http://127.0.0.1:9000", "http://127.0.0.1:8787"]
    assert w.remembered_port is None
    assert w.configured_base == "http://127.0.0.1:9000"  # the legacy address then


def test_effective_port_follows_settings_json(watcher):
    w = watcher(legacy="http://127.0.0.1:9000")
    w._discover_bases()
    assert w._effective_port() == 8787  # a 1.39.3 address never is
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w._discover_bases()
    assert w._effective_port() == 6868


def test_watcher_never_writes_the_legacy_key(watcher, ini):
    w = watcher(fallback=None)  # start() runs: keep the closed test port
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w._discover_bases()
    w.start()
    w._stop_activity()
    assert _legacy_value() is None


# --- 4. the command and the bundled version ----------------------------------------------


def test_set_port_command_from_source(tmp_path, monkeypatch):
    folder = make_bundle(tmp_path / "b" / ab.BUNDLE_DIR_NAME)
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    argv, cwd = ap.set_port_command(6868)
    assert argv == [sys.executable, "-X", "utf8", str(MAIN), "--agent-watchdog",
                    "--set-port", "6868"]
    assert cwd == str(folder.resolve())
    assert ab.arguments_allowed(argv[argv.index("--agent-watchdog") + 1:])


def test_set_port_command_when_frozen(tmp_path, monkeypatch):
    data = tmp_path / "_internal"
    folder = make_bundle(data / ab.BUNDLE_DIR_NAME)
    exe = tmp_path / "MarkdownViewer.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(data), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    argv, cwd = ap.set_port_command(6868)
    assert argv == [str(exe), "--agent-watchdog", "--set-port", "6868"]
    assert cwd == str(folder.resolve())


def test_set_port_command_without_a_bundle_is_none():
    assert ap.set_port_command(6868) is None  # conftest: no bundled copy


def test_bundled_version_is_read_from_the_source(tmp_path, monkeypatch):
    folder = make_bundle(tmp_path / "b" / ab.BUNDLE_DIR_NAME,
                         '"""doc"""\n__version__ = "1.4.0"\nraise SystemExit(9)\n')
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    assert ab.bundled_version() == "1.4.0"  # read, never imported or run
    make_bundle(folder, "print('no version here')\n")
    assert ab.bundled_version() is None
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(tmp_path / "none"))
    assert ab.bundled_version() is None


def test_the_tracked_bundle_version_is_read(monkeypatch):
    import ast
    folder = ROOT / ab.BUNDLE_DIR_NAME
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    tree = ast.parse((folder / ab.SCRIPT_NAME).read_text(encoding="utf-8-sig"))
    declared = next(node.value.value for node in tree.body
                    if isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "__version__")
    assert ab.bundled_version() == declared
    assert ap.version_tuple(declared) is not None


@pytest.mark.parametrize("text, expected", [
    ("1.3.0", (1, 3, 0)), ("1.4", (1, 4, 0)), ("v1.4.0rc1", (1, 4, 0)),
    ("2", (2, 0, 0)), ("", None), (None, None), ("dev", None),
])
def test_version_tuple(text, expected):
    assert ap.version_tuple(text) == expected


def test_bundle_too_old_only_when_known():
    assert ap.bundle_too_old("1.3.9") and ap.bundle_too_old("0.9")
    assert not ap.bundle_too_old("1.4.0") and not ap.bundle_too_old("2.0")
    assert not ap.bundle_too_old(None) and not ap.bundle_too_old("weird")


# --- 5. exit code -> message ------------------------------------------------------------


LINE = "watchdog said something"


def _outcome(code, line=LINE, version="1.4.0", installed=None, status=None):
    return ap.outcome_for(ap.SetPortResult(code, line), 6868, version, installed, status)


EXIT_TABLE = [
    (0, "已改成連接埠 6868。Watchdog 現在在 6868 上執行，其他程式也會跟著換過去。",
     True, False),
    (1, "連接埠 6868 已記住，但目前的 Watchdog 仍在其他連接埠執行，沒能搬過去。", True, True),
    (2, ap.USAGE_FAILURE, False, True),
    (3, "連接埠 6868 已被其他程式使用，沒有做任何變更。", False, True),
    (4, "無法變更連接埠。無法確定是否已變更（結束碼 4）。", False, True),  # never returned
    (5, "無法寫入 Watchdog 的設定檔，沒有做任何變更。", False, True),
    (6, "連接埠 6868 已記住。目前的 Watchdog 是由其他程式啟動的，"
        "會在它下次重新啟動時換過去。", True, False),
    (7, "發生未預期的錯誤，無法確定是否已變更。", False, True),
    (8, "電腦上安裝的 Watchdog 版本太舊（1.4.0 以前的版本），無法記住連接埠。"
        "請先把它更新到 1.4.0 以上，再設定一次。沒有做任何變更。", False, True),
    (9, "Watchdog 的設定檔是較新版本寫的，沒有做任何變更。", False, True),
    (10, "連接埠 6868 已記住，但 Watchdog 停下後無法在 6868 重新啟動，"
         "目前沒有 Watchdog 在執行。", True, True),
    (42, "無法變更連接埠。無法確定是否已變更（結束碼 42）。", False, True),
    (-1, "無法變更連接埠。無法確定是否已變更（結束碼 -1）。", False, True),
]


@pytest.mark.parametrize("line", [LINE, ""], ids=["with-line", "empty-line"])
@pytest.mark.parametrize("code, text, saved, warning", EXIT_TABLE,
                         ids=[str(row[0]) for row in EXIT_TABLE])
def test_every_exit_code_has_its_message_with_or_without_the_line(
        code, text, saved, warning, line):
    """The code alone decides: a windowed host may receive no line at all."""
    outcome = _outcome(code, line)
    assert (outcome.text, outcome.saved, outcome.warning) == (text, saved, warning)
    assert outcome.detail == line


def test_saved_codes_constant_matches_the_table():
    assert ap.SAVED_CODES == {0, 1, 6, 10}
    assert {code for code, _, saved, _ in EXIT_TABLE if saved} == ap.SAVED_CODES


def test_newer_schema_is_its_own_code():
    assert ap.EXIT_NEWER_SCHEMA == 9
    outcome = _outcome(ap.EXIT_NEWER_SCHEMA, "")
    assert outcome.text == "Watchdog 的設定檔是較新版本寫的，沒有做任何變更。"
    assert not outcome.saved
    # exit 1 means one thing only, whatever the line says
    newer_words = _outcome(1, "settings.json was written by a newer watchdog (schema 2)")
    assert newer_words.saved and "沒能搬過去" in newer_words.text


def test_exit_8_version_from_the_installed_copy_then_the_line():
    line = ("port 6868 not saved: the installed watchdog is 1.3.0, which cannot "
            "remember a port; update it to 1.4.0 or later first")
    assert "（1.2.5）" in _outcome(8, line, installed="1.2.5").text  # the file wins
    assert "（1.2.5）" in _outcome(8, "", installed="1.2.5").text
    assert "（1.3.0）" in _outcome(8, line).text                      # then the line
    older = "port 6868 not saved: the installed watchdog is older than 1.4.0, which ..."
    assert "（1.4.0 以前的版本）" in _outcome(8, older).text
    assert "（1.4.0 以前的版本）" in _outcome(8, "").text


def _install(home, script_source, launch=None):
    wd = home.parent / (home.name + "-wd")
    wd.mkdir(parents=True, exist_ok=True)
    script = wd / "aiwatch.py"
    script.write_text(script_source, encoding="utf-8")
    python = wd / "pythonw.exe"
    python.write_text("", encoding="utf-8")
    if launch is None:
        launch = [str(python), "-X", "utf8", str(script), "--serve", "--background"]
    (home / "install.json").write_text(json.dumps(
        {"schema": 1, "launch": launch, "cwd": str(wd), "autostart": True}),
        encoding="utf-8")
    return script


def test_installed_version_is_read_from_install_json_launch():
    home = _home()
    script = _install(home, '"""x"""\n__version__ = "1.3.0"\nraise SystemExit(9)\n')
    assert au.installed_version(home) == "1.3.0"  # read, never imported or run
    script.write_text("print('no version')\n", encoding="utf-8")
    assert au.installed_version(home) is None
    _install(home, '__version__ = "1.3.0"\n', launch=["aiwatch.py"])  # not absolute
    assert au.installed_version(home) is None
    (home / "install.json").write_text("{broken", encoding="utf-8")
    assert au.installed_version(home) is None
    (home / "install.json").unlink()
    assert au.installed_version(home) is None


def test_exit_8_end_to_end_names_the_installed_version(ini):
    _install(_home(), '__version__ = "1.3.0"\n')
    _legacy("http://127.0.0.1:9000")
    outcome = ap.finish_set_port(ap.SetPortResult(8, ""), 6868, "1.4.0", None)
    assert outcome.text.startswith("電腦上安裝的 Watchdog 版本太舊（1.3.0），")
    assert _legacy_value() == "http://127.0.0.1:9000"


def test_exit_2_from_a_bundle_too_old_for_set_port():
    assert _outcome(2, version="1.3.0").text == ap.TOO_OLD_BUNDLE
    assert _outcome(2, "", version="1.3.0").text == ap.TOO_OLD_BUNDLE
    argparse_line = "aiwatch.py: error: unrecognized arguments: --set-port 6868"
    assert _outcome(2, argparse_line, version=None).text == ap.TOO_OLD_BUNDLE
    assert _outcome(2, "usage error", version="1.4.0").text == ap.USAGE_FAILURE
    assert _outcome(2, "", version=None).text == ap.USAGE_FAILURE


@pytest.mark.parametrize("failure, words", [
    (ap.NO_BUNDLE, "沒有內建 Watchdog"), (ap.TOO_OLD, "版本太舊"),
    (ap.LAUNCH, "無法執行內建的 Watchdog"), (ap.TIMEOUT, "60 秒"),
    (ap.CRASHED, "意外結束"),
])
def test_runs_that_did_not_end_normally(failure, words):
    outcome = ap.outcome_for(ap.SetPortResult(None, "why", failure), 6868)
    assert words in outcome.text and not outcome.saved and outcome.warning
    assert outcome.detail == "why"


# --- the status line: settings.json and endpoint.json after the run ----------------------


class FakeProcesses:
    def __init__(self, alive=(), started=None):
        self._alive = set(alive)
        self._started = started

    def alive(self, pid):
        return pid in self._alive

    def started_at(self, pid):
        return self._started


def test_status_text():
    assert ap.status_text(ap.PortStatus(6868, "127.0.0.1:6868")) == (
        "記住的連接埠：6868；目前 Watchdog：127.0.0.1:6868")
    assert ap.status_text(ap.PortStatus(None, None)) == (
        "記住的連接埠：尚未設定（預設 8787）；目前沒有 Watchdog 在執行")


def test_status_line_is_added_after_every_run():
    status = ap.PortStatus(6868, "127.0.0.1:8787")
    for code, _, _, _ in EXIT_TABLE:
        assert _outcome(code, "", status=status).text.endswith(
            "\n\n記住的連接埠：6868；目前 Watchdog：127.0.0.1:8787")
    unexpected = _outcome(7, "", status=status).text
    assert unexpected.startswith("發生未預期的錯誤，無法確定是否已變更。")
    timeout = ap.outcome_for(ap.SetPortResult(None, "", ap.TIMEOUT), 6868, status=status)
    assert timeout.text.endswith("目前 Watchdog：127.0.0.1:8787")
    nothing_ran = ap.outcome_for(ap.SetPortResult(None, "", ap.NO_BUNDLE), 6868,
                                 status=status)
    assert "記住的連接埠" not in nothing_ran.text


def test_exit_0_with_no_watchdog_running_says_the_next_start_uses_it():
    outcome = _outcome(0, "", status=ap.PortStatus(6868, None))
    assert outcome.text.startswith(
        "已改成連接埠 6868。目前沒有 Watchdog 在執行，下次啟動時會使用 6868")
    assert outcome.saved


def _endpoint_record(home, **record):
    (home / "endpoint.json").write_text(json.dumps(record), encoding="utf-8")


def test_read_status_from_settings_and_a_live_endpoint():
    home = _home()
    _write_settings(home, {"schema": 1, "port": 6868})
    _endpoint_record(home, url="http://127.0.0.1:6868", pid=4242, started_at=1000.0)
    assert ap.read_status(home, FakeProcesses(alive={4242}, started=1001.0)) == (
        ap.PortStatus(6868, "127.0.0.1:6868"))


@pytest.mark.parametrize("record, processes", [
    ({"url": "http://127.0.0.1:6868", "pid": 4242}, FakeProcesses()),          # dead
    ({"url": "http://127.0.0.1:6868", "pid": 4242, "started_at": 1000.0},
     FakeProcesses(alive={4242}, started=2000.0)),                            # pid reused
    ({"url": "http://example.com:6868", "pid": 4242}, FakeProcesses(alive={4242})),
    ({"url": "http://127.0.0.1:6868"}, FakeProcesses(alive={4242})),           # no pid
    ({"url": "http://127.0.0.1:6868", "pid": True}, FakeProcesses(alive={1})),
], ids=["dead", "reused", "not-loopback", "no-pid", "bool-pid"])
def test_read_status_ignores_a_stale_or_odd_endpoint(record, processes):
    home = _home()
    _endpoint_record(home, **record)
    assert ap.read_status(home, processes) == ap.PortStatus(None, None)


def test_read_status_without_files_or_with_garbage_never_raises():
    home = _home()
    assert ap.read_status(home, FakeProcesses()) == ap.PortStatus(None, None)
    (home / "endpoint.json").write_bytes(b"\xff{garbage")
    _write_settings(home, None, b"[[[")
    assert ap.read_status(home, FakeProcesses()) == ap.PortStatus(None, None)

    class Boom(FakeProcesses):
        def alive(self, pid):
            raise OSError("no access")

    _endpoint_record(home, url="http://127.0.0.1:6868", pid=4242)
    assert ap.read_status(home, Boom()) == ap.PortStatus(None, None)


def test_pick_line():
    assert ap.pick_line(0, "first\nport 6868 saved\n\n", "noise") == "port 6868 saved"
    usage = "usage: aiwatch.py [-h]\n   [--serve]\naiwatch.py: error: unrecognized arguments\n"
    assert ap.pick_line(2, "", usage) == "aiwatch.py: error: unrecognized arguments"
    assert ap.pick_line(3, "only stdout\n", "") == "only stdout"
    assert ap.pick_line(1, "", "a\x1b[31m\u202eb\n") == "a[31mb"
    assert ap.pick_line(0, "", "") == ""


def test_decode_output_falls_back_to_the_ansi_code_page(monkeypatch):
    assert ap.decode_output("連接埠".encode()) == "連接埠"
    # a windowed build's own pipe stream: ANSI code page, even in UTF-8 mode
    monkeypatch.setattr(ap.locale, "getencoding", lambda: "cp950")
    assert ap.decode_output("已記住".encode("cp950")) == "已記住"
    assert isinstance(ap.decode_output(b"\xff\xfe\xfa"), str)


def test_dialog_label_and_default():
    assert ap.dialog_label("目前連線：127.0.0.1:8787", 6868).startswith(
        "目前連線：127.0.0.1:8787\n記住的連接埠：6868\n")
    assert "記住的連接埠：尚未設定（預設 8787）" in ap.dialog_label("目前沒有連上 Watchdog", None)
    assert ap.dialog_default(6868, 9000) == 6868
    assert ap.dialog_default(None, 9000) == 9000
    assert ap.dialog_default(None, None) == 8787


# --- 6. after the run: the 1.39.3 address and the watcher ---------------------------------


class FakeWatcher:
    def __init__(self, available=True, base_url="http://127.0.0.1:8787"):
        self.available = available
        self.base_url = base_url
        self.log = []

    def pause(self):
        self.log.append("pause")

    def start(self):
        self.log.append("start")


@pytest.mark.parametrize("code, deleted", [
    (0, True), (1, True), (6, True), (10, True),
    (2, False), (3, False), (4, False), (5, False), (7, False), (8, False),
    (9, False), (11, False),
])
def test_legacy_key_deleted_only_after_the_saved_codes(ini, code, deleted):
    _legacy("http://127.0.0.1:9000")
    w = FakeWatcher()
    ap.finish_set_port(ap.SetPortResult(code, ""), 6868, "1.4.0", w)
    assert (_legacy_value() is None) == deleted
    assert w.log == ["start"]  # rediscovered either way


@pytest.mark.parametrize("failure", [ap.LAUNCH, ap.TIMEOUT, ap.CRASHED])
def test_legacy_key_kept_when_the_run_failed(ini, failure):
    _legacy("http://127.0.0.1:9000")
    ap.finish_set_port(ap.SetPortResult(None, "", failure), 6868, "1.4.0", None)
    assert _legacy_value() == "http://127.0.0.1:9000"


def _change(watcher, ask=6868, result=None, raises=None):
    seen = {"asked": None, "run": None, "shown": None}

    def fake_ask(parent, label, default):
        seen["asked"] = (label, default)
        return ask

    def fake_run(parent, argv, cwd, port):
        seen["run"] = (argv, cwd, port, list(watcher.log) if watcher else None)
        if raises:
            raise raises
        return result

    def fake_show(parent, outcome):
        seen["shown"] = outcome

    outcome = ap.change_watchdog_port(None, watcher, ask=fake_ask, run=fake_run,
                                      show=fake_show)
    return outcome, seen


@pytest.fixture
def new_bundle(tmp_path, monkeypatch):
    folder = make_bundle(tmp_path / "nb" / ab.BUNDLE_DIR_NAME,
                         '__version__ = "1.4.0"\n\ndef main(argv):\n    return 0\n')
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    return folder


def test_change_runs_the_bundle_pauses_and_restarts_the_watcher(ini, new_bundle):
    _legacy("http://127.0.0.1:9000")
    _write_settings(_home(), {"schema": 1, "port": 7000})
    w = FakeWatcher(base_url="http://127.0.0.1:8787")
    outcome, seen = _change(w, result=ap.SetPortResult(0, "port 6868 saved"))
    label, default = seen["asked"]
    assert "目前連線：127.0.0.1:8787" in label and "記住的連接埠：7000" in label
    assert default == 7000
    argv, cwd, port, log_at_run = seen["run"]
    assert argv[-3:] == ["--agent-watchdog", "--set-port", "6868"] and port == 6868
    assert cwd == str(new_bundle.resolve())
    assert log_at_run == ["pause"]           # nothing polls while it moves
    assert w.log == ["pause", "start"]
    assert outcome is seen["shown"] and outcome.saved
    assert outcome.detail == "port 6868 saved"
    assert _legacy_value() is None


def test_change_defaults_to_the_connected_port(ini, new_bundle):
    _, seen = _change(FakeWatcher(base_url="http://127.0.0.1:9100"), ask=None)
    assert seen["asked"][1] == 9100
    _, seen = _change(FakeWatcher(available=False), ask=None)
    assert seen["asked"][1] == 8787
    assert "目前沒有連上 Watchdog" in seen["asked"][0]


def test_cancel_does_nothing(ini, new_bundle):
    _legacy("http://127.0.0.1:9000")
    w = FakeWatcher()
    outcome, seen = _change(w, ask=None)
    assert outcome is None and seen["run"] is None and seen["shown"] is None
    assert w.log == [] and _legacy_value() == "http://127.0.0.1:9000"


def test_no_bundle_runs_nothing(ini):
    w = FakeWatcher()
    outcome, seen = _change(w)
    assert seen["run"] is None and w.log == []
    assert "沒有內建 Watchdog" in outcome.text


def test_bundle_too_old_runs_nothing(ini, tmp_path, monkeypatch):
    folder = make_bundle(tmp_path / "old" / ab.BUNDLE_DIR_NAME, '__version__ = "1.3.0"\n')
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    _legacy("http://127.0.0.1:9000")
    w = FakeWatcher()
    outcome, seen = _change(w)
    assert seen["run"] is None and w.log == []
    assert outcome.text == ap.TOO_OLD_BUNDLE and "1.3.0" in outcome.detail
    assert _legacy_value() == "http://127.0.0.1:9000"


def test_a_failing_run_still_restarts_the_watcher(ini, new_bundle):
    w = FakeWatcher()
    outcome, _ = _change(w, raises=OSError("cannot"))
    assert w.log == ["pause", "start"]
    assert "無法執行內建的 Watchdog" in outcome.text and outcome.detail == "cannot"


# --- 7. the process -----------------------------------------------------------------------


def _run(qapp, argv, cwd, timeout_ms=30000):
    run = ap.SetPortRun(argv, str(cwd), timeout_ms=timeout_ms)
    run.start()
    assert wait_until(qapp, lambda: run.result is not None, timeout=60)
    return run


@pytest.mark.parametrize("code, expected", [
    (0, "port 6868 saved"), (3, "port 6868 is used by another program"),
])
def test_run_captures_the_exit_code_and_the_line(qapp, tmp_path, code, expected):
    script = (
        "import sys\n"
        "print('port 6868 saved')\n"
        "print('port 6868 is used by another program', file=sys.stderr)\n"
        f"sys.exit({code})\n")
    run = _run(qapp, [sys.executable, "-c", script], tmp_path)
    assert run.result == ap.SetPortResult(code, expected)
    run.deleteLater()


def test_run_times_out_and_kills_only_its_own_process(qapp, tmp_path):
    run = ap.SetPortRun([sys.executable, "-c", "import time; time.sleep(60)"],
                        str(tmp_path), timeout_ms=500)
    run.start()
    assert run.process.waitForStarted(10000)
    pid = run.process.processId()
    assert wait_until(qapp, lambda: run.result is not None, timeout=20)
    assert run.result.failure == ap.TIMEOUT
    assert run.process.state() == QProcess.ProcessState.NotRunning
    assert pid and pid != os.getpid()
    run.deleteLater()


def test_run_that_cannot_start_is_a_launch_failure(qapp, tmp_path):
    run = _run(qapp, [str(tmp_path / "missing.exe"), "--agent-watchdog"], tmp_path)
    assert run.result.failure == ap.LAUNCH and run.result.code is None
    run.deleteLater()


def test_run_modal_busy_dialog_cannot_be_cancelled(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QCloseEvent

    seen = {}
    original = ap._BusyDialog.exec

    def exec_and_try_to_cancel(self):
        def poke():
            seen["modal"] = self.isModal()
            self.reject()                       # Esc
            seen["after_reject"] = self.isVisible()
            event = QCloseEvent()
            self.closeEvent(event)              # the title bar's close box
            seen["close_accepted"] = event.isAccepted()

        QTimer.singleShot(0, poke)
        return original(self)

    monkeypatch.setattr(ap._BusyDialog, "exec", exec_and_try_to_cancel)
    script = "import sys, time; time.sleep(0.8); print('port 6868 saved'); sys.exit(0)"
    result = ap._run_modal(None, [sys.executable, "-c", script], str(tmp_path), 6868)
    assert result == ap.SetPortResult(0, "port 6868 saved")
    assert seen == {"modal": True, "after_reject": True, "close_accepted": False}


FAKE_SET_PORT = """\
import json, os, sys

__version__ = "1.4.0"


def main(argv):
    with open(os.environ["FAKE_OUT"], "w", encoding="utf-8") as fh:
        json.dump({"argv": argv, "self_launch": os.environ.get("AIWATCH_SELF_LAUNCH"),
                   "embedded": os.environ.get("AIWATCH_EMBEDDED")}, fh)
    print("port %s saved; the running watchdog is embedded and cannot be moved" % argv[1])
    return 6
"""


def test_end_to_end_through_main_py_with_a_fake_bundle(qapp, tmp_path, monkeypatch, ini):
    folder = make_bundle(tmp_path / "fb" / ab.BUNDLE_DIR_NAME, FAKE_SET_PORT)
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    out = tmp_path / "seen.json"
    monkeypatch.setenv("FAKE_OUT", str(out))
    argv, cwd = ap.set_port_command(6868)
    run = _run(qapp, argv, cwd)
    assert run.result == ap.SetPortResult(
        6, "port 6868 saved; the running watchdog is embedded and cannot be moved")
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert seen["argv"] == ["--set-port", "6868"] and seen["embedded"] == "1"
    assert json.loads(seen["self_launch"])[-1] == "--agent-watchdog"
    _legacy("http://127.0.0.1:9000")
    outcome = ap.finish_set_port(run.result, 6868, ab.bundled_version(), None)
    assert outcome.saved and "下次重新啟動時換過去" in outcome.text
    assert _legacy_value() is None
    run.deleteLater()


def test_end_to_end_the_tracked_1_3_0_bundle_refuses_set_port(qapp, tmp_path, monkeypatch):
    """What the pre-check spares the user: 1.3.0 answers with a usage error
    (and leaves a report in its data directory, a temp one here)."""
    home = Path(os.environ["AIWATCH_HOME"])
    assert home.name.endswith("-aiwatch")  # conftest's temp directory
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(ROOT / ab.BUNDLE_DIR_NAME))
    if not ap.bundle_too_old(ab.bundled_version()):
        pytest.skip("the tracked bundle knows --set-port: never run it from a test")
    argv, cwd = ap.set_port_command(6868)
    run = _run(qapp, argv, cwd)
    assert run.result.code == 2
    assert "unrecognized arguments" in run.result.line
    outcome = ap.outcome_for(run.result, 6868, None)
    assert outcome.text == ap.TOO_OLD_BUNDLE and not outcome.saved
    run.deleteLater()


# --- 8. the notice bar ---------------------------------------------------------------------


def _dock(w):
    host = QMainWindow()
    dock = AgentsDock(host, LIGHT)
    dock.set_watcher(w)
    return host, dock


def _connect(w, index):
    w._bases = w._discover_bases()
    w._base_index = index
    w.state.set_unavailable(False)
    w.changed.emit()


def test_notice_when_the_remembered_port_is_not_the_one_in_use(watcher):
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w = watcher()
    host, dock = _dock(w)
    _connect(w, 1)                       # [6868, 8787]: on 8787
    assert not dock._notice.isHidden()
    assert dock._notice_label.text() == (
        "記住的連接埠 6868 沒有回應，目前顯示的是 127.0.0.1:8787 的 Watchdog。")
    _connect(w, 0)                       # on the remembered port
    assert dock._notice.isHidden()
    w.state.set_unavailable(True)
    w.changed.emit()
    assert dock._notice.isHidden()


def test_notice_settings_json_wins_over_the_legacy_address(watcher):
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w = watcher(legacy="http://127.0.0.1:9000")
    _connect(w, 1)                       # [6868, 9000, 8787]: on 9000
    assert w.fallback_from == "http://127.0.0.1:6868"
    assert address_notice_text(w).startswith("記住的連接埠 6868 沒有回應，")


def test_notice_for_a_legacy_address_without_settings_json(watcher):
    w = watcher(legacy="http://127.0.0.1:9000")
    _connect(w, 1)                       # [9000, 8787]: on 8787
    assert address_notice_text(w) == (
        "你設定的 127.0.0.1:9000 沒有回應，目前顯示的是 127.0.0.1:8787 的 Watchdog。")


def test_no_notice_without_any_configured_port(watcher):
    _endpoint("http://127.0.0.1:7001")
    w = watcher()
    _connect(w, 1)                       # [7001, 8787]: on 8787, nothing chosen
    assert address_notice_text(w) == "" and w.fallback_from is None


def test_no_notice_when_the_endpoint_is_the_remembered_port(watcher):
    _endpoint("http://[::1]:6868")
    _write_settings(_home(), {"schema": 1, "port": 6868})
    w = watcher()
    _connect(w, 0)                       # same port, other loopback spelling
    assert address_notice_text(w) == ""


def test_notice_text_for_fake_watchers():
    assert address_notice_text(None) == ""
    assert address_notice_text(object()) == ""


# --- 9. standard streams of a windowed build ---------------------------------------------
#
# The real proof (a throwaway windowed PyInstaller exe run through SetPortRun)
# is outside the test suite; here the Windows handle branch is substituted.


def test_existing_streams_are_left_alone(monkeypatch):
    import io
    out, err, inp = io.StringIO(), io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdin", inp)
    monkeypatch.setattr(ab, "_std_handle", lambda name: pytest.fail("not asked"))
    ab._ensure_std_streams()
    assert (sys.stdout, sys.stderr, sys.stdin) == (out, err, inp)


def _missing_streams(monkeypatch):
    for name in ("stdout", "stderr", "stdin"):
        monkeypatch.setattr(sys, name, None)


def _close_new_streams():
    for name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, name)
        if stream is not None and not getattr(stream, "closed", True):
            stream.close()


@pytest.mark.parametrize("handle", [lambda name: None,
                                    lambda name: (_ for _ in ()).throw(OSError("x"))],
                         ids=["no-handle", "handle-lookup-raises"])
def test_without_a_usable_handle_devnull_is_used(monkeypatch, handle):
    _missing_streams(monkeypatch)
    monkeypatch.setattr(ab, "_std_handle", handle)
    try:
        ab._ensure_std_streams()  # never raises
        assert sys.stdout.name == os.devnull and sys.stderr.name == os.devnull
        assert sys.stdin.name == os.devnull
        print("goes nowhere")
    finally:
        _close_new_streams()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handles")
def test_an_inherited_handle_is_attached_utf8_and_line_buffered(monkeypatch, tmp_path):
    import _winapi
    import msvcrt

    target = tmp_path / "pipe-stand-in.txt"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_BINARY)
    me = _winapi.GetCurrentProcess()
    # A handle of its own (like an inherited one): the descriptor the helper
    # opens on it owns it, and closing that never touches *fd*.
    handle = _winapi.DuplicateHandle(me, msvcrt.get_osfhandle(fd), me, 0, False,
                                     _winapi.DUPLICATE_SAME_ACCESS)
    _missing_streams(monkeypatch)
    monkeypatch.setattr(ab, "_std_handle", lambda name: handle if name == "stdout" else None)
    attached_fd = None
    try:
        ab._ensure_std_streams()
        attached = sys.stdout
        attached_fd = attached.fileno()
        assert attached_fd != fd
        assert attached.encoding == "utf-8" and attached.line_buffering
        print("port 6868 saved（已記住）")  # no flush: the newline sends it
        assert target.read_bytes().decode("utf-8").rstrip() == "port 6868 saved（已記住）"
        assert sys.stderr.name == os.devnull  # no handle for that one
    finally:
        _close_new_streams()  # closefd=False: the descriptor stays open ...
        if attached_fd is not None:
            os.close(attached_fd)  # ... and owns the duplicate handle
        else:
            _winapi.CloseHandle(handle)
        os.close(fd)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handles")
def test_std_handle_lookup_itself_never_raises():
    for name in ("stdin", "stdout", "stderr"):
        value = ab._std_handle(name)
        assert value is None or (isinstance(value, int) and value > 0)
