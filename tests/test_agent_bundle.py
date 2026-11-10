"""The optional bundled Agent Watchdog: lookup, --agent-watchdog, fallback.

Only a fake aiwatch.py (written into tmp_path) is ever used; nothing here
starts a real Watchdog and the launcher is injected.
"""
# ruff: noqa: F811  (pytest fixtures imported from the sibling test modules)

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import agent_autostart as au
from app import agent_bundle as ab
from app import settings_store
from tests.test_agent_autostart import (  # noqa: F401
    BAD,
    dead_url,
    layout,
    make,
    two_failures,
    write_install,
)
from tests.test_agent_watchdog_qt import _panel_dock, mock_dog, wait_until  # noqa: F401
from tools import package_policy as pp

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"

FAKE_SCRIPT = """\
import json, os, sys
import sibling
print("fake watchdog says hi")
out = os.environ.get("FAKE_OUT")
if out:
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"argv": sys.argv, "path0": sys.path[0], "name": __name__,
                   "qt": any(m.startswith("PySide6") for m in sys.modules),
                   "sibling": sibling.VALUE, "file": __file__,
                   "env": {k: v for k, v in os.environ.items()
                           if k.startswith("AIWATCH_")}}, fh)
mode = os.environ.get("FAKE_MODE", "")
if mode == "exit7":
    sys.exit(7)
if mode == "exit_none":
    sys.exit()
if mode == "exit_text":
    sys.exit("fatal text")
if mode == "crash":
    raise RuntimeError("boom in fake watchdog")
"""


# The embedding interface: main(argv) is called, the file is never run as a
# script (that would raise).
FAKE_MODULE = """\
import json, os, sys
import sibling


def main(argv=None):
    out = os.environ.get("FAKE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"received": argv, "argv": sys.argv, "name": __name__,
                       "path0": sys.path[0], "sibling": sibling.VALUE,
                       "env": {k: v for k, v in os.environ.items()
                               if k.startswith("AIWATCH_")}}, fh)
    mode = os.environ.get("FAKE_MODE", "")
    if mode == "return5":
        return 5
    if mode == "exit7":
        sys.exit(7)
    if mode == "crash":
        raise RuntimeError("boom in fake main")


if __name__ == "__main__":
    raise AssertionError("must be called through main(argv)")
"""


def make_bundle(folder: Path, source: str = FAKE_SCRIPT) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "aiwatch.py").write_text(source, encoding="utf-8")
    (folder / "sibling.py").write_text("VALUE = 'beside the script'\n", encoding="utf-8")
    return folder


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A fake bundled copy, found through the development override."""
    folder = make_bundle(tmp_path / "bundle" / ab.BUNDLE_DIR_NAME)
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    return folder


@pytest.fixture
def no_bundle(tmp_path, monkeypatch):
    monkeypatch.delenv(ab.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(ab, "_REPO_ROOT", tmp_path / "empty-root")


# --- 1. lookup ----------------------------------------------------------------


def test_no_bundle_is_the_normal_case(no_bundle):
    assert ab.bundled_script() is None
    assert ab.bundled_launch() is None


def test_directory_without_the_script_counts_as_no_bundle(tmp_path, monkeypatch):
    (tmp_path / ab.BUNDLE_DIR_NAME).mkdir()
    monkeypatch.delenv(ab.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(ab, "_REPO_ROOT", tmp_path)
    assert ab.bundled_script() is None


def test_development_lookup_is_the_repository_root(tmp_path, monkeypatch):
    monkeypatch.delenv(ab.ENV_OVERRIDE, raising=False)
    assert ab.bundle_dir() == ROOT / "agent_watchdog"
    folder = make_bundle(tmp_path / ab.BUNDLE_DIR_NAME)
    monkeypatch.setattr(ab, "_REPO_ROOT", tmp_path)
    assert ab.bundled_script() == (folder / "aiwatch.py").resolve()


def test_development_override_points_elsewhere(bundle):
    assert ab.bundled_script() == (bundle / "aiwatch.py").resolve()


def test_frozen_lookup_uses_the_data_directory_and_ignores_the_override(
        tmp_path, monkeypatch, bundle):
    data = tmp_path / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(data), raising=False)
    assert ab.bundle_dir() == data / "agent_watchdog"
    assert ab.bundled_script() is None  # the override is development-only
    folder = make_bundle(data / "agent_watchdog")
    assert ab.bundled_script() == (folder / "aiwatch.py").resolve()


def test_frozen_lookup_without_meipass_falls_back_to_the_exe_folder(tmp_path, monkeypatch):
    exe = tmp_path / "MarkdownViewer.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert ab.bundle_dir() == tmp_path.resolve() / "agent_watchdog"


def test_launch_command_in_development_runs_main_py(bundle):
    launch, cwd = ab.bundled_launch()
    assert launch == [sys.executable, "-X", "utf8", str(ROOT / "main.py"),
                      "--agent-watchdog"]
    assert cwd == str(bundle.resolve())


def test_launch_command_when_frozen_is_the_application_itself(tmp_path, monkeypatch):
    data = tmp_path / "_internal"
    folder = make_bundle(data / "agent_watchdog")
    exe = tmp_path / "MarkdownViewer.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(data), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    launch, cwd = ab.bundled_launch()
    assert launch == [str(exe), "--agent-watchdog"]  # the flag and nothing else
    assert cwd == str(folder.resolve())
    assert ab.self_launch() == [str(exe.resolve()), "--agent-watchdog"]


# --- 2. the --agent-watchdog entry point ----------------------------------------


def _run_main(args, env_extra, timeout=120):
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(MAIN), *args],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=timeout,
    )


def test_main_runs_the_bundled_script_without_qt(bundle, tmp_path):
    out = tmp_path / "seen.json"
    result = _run_main(
        ["--agent-watchdog", "--serve", "--port", "1234"],
        {ab.ENV_OVERRIDE: str(bundle), "FAKE_OUT": str(out), "FAKE_MODE": "exit7"})
    assert result.returncode == 7, result.stderr
    assert "fake watchdog says hi" in result.stdout
    seen = json.loads(out.read_text(encoding="utf-8"))
    script = str((bundle / "aiwatch.py").resolve())
    assert seen["argv"] == [script, "--serve", "--port", "1234"]
    assert seen["path0"] == str(bundle.resolve())
    assert seen["name"] == "__main__" and seen["file"] == script
    assert seen["sibling"] == "beside the script"
    assert seen["qt"] is False  # no Qt, hence no QApplication and no IPC
    env = seen["env"]
    assert env["AIWATCH_EMBEDDED"] == "1"
    assert env["AIWATCH_RESOURCES"] == str(bundle.resolve())
    assert json.loads(env["AIWATCH_SELF_LAUNCH"]) == [
        str(Path(sys.executable).resolve()), str(MAIN), "--agent-watchdog"]
    assert env["AIWATCH_HOME"] == os.environ["AIWATCH_HOME"]  # the caller's, untouched


def test_main_with_only_the_flag_serves_in_the_background(bundle, tmp_path):
    out = tmp_path / "seen.json"
    result = _run_main(["--agent-watchdog"],
                       {ab.ENV_OVERRIDE: str(bundle), "FAKE_OUT": str(out)})
    assert result.returncode == 0, result.stderr
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert seen["argv"][1:] == ["--serve", "--background"]


def test_main_prefers_the_argv_entry_point(tmp_path):
    folder = make_bundle(tmp_path / "new" / ab.BUNDLE_DIR_NAME, FAKE_MODULE)
    out = tmp_path / "seen.json"
    result = _run_main(["--agent-watchdog"], {
        ab.ENV_OVERRIDE: str(folder), "FAKE_OUT": str(out), "FAKE_MODE": "return5"})
    assert result.returncode == 5, result.stderr
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert seen["received"] == ["--serve", "--background"]
    assert seen["name"] == "aiwatch"  # imported, not run as a script
    assert seen["path0"] == str(folder.resolve())
    assert seen["sibling"] == "beside the script"
    assert seen["env"]["AIWATCH_EMBEDDED"] == "1"
    assert seen["env"]["AIWATCH_RESOURCES"] == str(folder.resolve())


def test_main_without_a_bundle_exits_quietly_and_nonzero(tmp_path):
    result = _run_main(["--agent-watchdog", "--serve"],
                       {ab.ENV_OVERRIDE: str(tmp_path / "missing")})
    assert result.returncode == ab.EXIT_NO_BUNDLE != 0
    assert result.stdout == "" and result.stderr == ""


@pytest.fixture
def in_process(monkeypatch):
    """run_bundled() rewrites sys.argv / sys.path: restore them afterwards."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in (ab.ENV_EMBEDDED, ab.ENV_RESOURCES, ab.ENV_SELF_LAUNCH):
        monkeypatch.delenv(name, raising=False)  # restored (removed) afterwards
    yield
    sys.modules.pop("sibling", None)
    sys.modules.pop("aiwatch", None)


def test_run_bundled_without_a_bundle_changes_nothing(no_bundle, in_process):
    argv, path = list(sys.argv), list(sys.path)
    assert ab.run_bundled(["--serve"]) == ab.EXIT_NO_BUNDLE
    assert sys.argv == argv and sys.path == path
    assert ab.ENV_EMBEDDED not in os.environ


@pytest.mark.parametrize("source, expected", [
    ("def main(argv=None):\n    pass\n", True),
    ("def main(argv, /):\n    pass\n", True),
    ("def main(*args):\n    pass\n", True),
    ("def main():\n    pass\n", False),             # today's Watchdog
    ("def main(*, argv=None):\n    pass\n", False),  # cannot be called positionally
    ("def other(argv):\n    pass\n", False),
    ("class K:\n    def main(self, argv):\n        pass\n", False),
    ("def (:\n", False),
])
def test_accepts_argv_is_decided_from_the_source(tmp_path, source, expected):
    script = tmp_path / "aiwatch.py"
    script.write_text(source, encoding="utf-8")
    assert ab.accepts_argv(script) is expected
    assert ab.accepts_argv(tmp_path / "missing.py") is False


@pytest.mark.parametrize("mode, code", [("", 0), ("return5", 5), ("exit7", 7),
                                        ("crash", ab.EXIT_CRASHED)])
def test_run_bundled_through_the_argv_entry_point(
        tmp_path, monkeypatch, in_process, mode, code):
    folder = make_bundle(tmp_path / "new" / ab.BUNDLE_DIR_NAME, FAKE_MODULE)
    out = tmp_path / "seen.json"
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(folder))
    monkeypatch.setenv("FAKE_OUT", str(out))
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("AIWATCH_HOME", str(tmp_path / "home"))
    assert ab.run_bundled(["--serve", "--port", "9"]) == code
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert seen["received"] == ["--serve", "--port", "9"]
    assert seen["env"]["AIWATCH_HOME"] == str(tmp_path / "home")
    if mode == "crash":
        log = tmp_path / "home" / ab.ERROR_LOG_NAME
        assert "boom in fake main" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode, code", [("", 0), ("exit7", 7), ("exit_none", 0),
                                        ("exit_text", 1)])
def test_run_bundled_passes_the_exit_code_on(bundle, in_process, monkeypatch, mode, code):
    monkeypatch.setenv("FAKE_MODE", mode)
    assert ab.run_bundled(["--serve", "--port", "7"]) == code
    assert sys.argv == [str((bundle / "aiwatch.py").resolve()), "--serve", "--port", "7"]
    assert sys.path[0] == str(bundle.resolve())


def test_run_bundled_survives_missing_standard_streams(bundle, in_process, monkeypatch):
    # A windowed packaged build: no console, so all three are None.
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, None)
    opened = []
    try:
        assert ab.run_bundled([]) == 0  # the fake script print()s
        opened = [sys.stdin, sys.stdout, sys.stderr]
        assert all(stream is not None for stream in opened)
    finally:
        for stream in opened:
            if stream is not None:
                stream.close()


def test_run_bundled_crash_is_reported_not_raised(bundle, in_process, monkeypatch, tmp_path):
    home = tmp_path / "aiwatch-home"
    monkeypatch.setenv("AIWATCH_HOME", str(home))
    monkeypatch.setenv("FAKE_MODE", "crash")
    assert ab.run_bundled([]) == ab.EXIT_CRASHED
    assert "boom in fake watchdog" in (home / ab.ERROR_LOG_NAME).read_text(encoding="utf-8")


ALLOWED_ARGS = [
    [], ["--serve"], ["--background"], ["--serve", "--background"],
    ["--serve", "--port", "18080"], ["--port=18080", "--background"], ["--port", "0"],
    ["--serve", "--idle-exit", "30"], ["--idle-exit=0.5"], ["--idle-exit", "0"],
    ["--background", "--port", "65535", "--idle-exit", "600", "--serve"],
]
REFUSED_ARGS = [
    ["--stop"], ["--start"], ["--restart"], ["--register"], ["--autostart", "off"],
    ["--autostart=on"], ["--events-log"], ["--events-log", "x.jsonl"], ["--once"],
    ["--serve", "--stop"], ["--serve", "--background", "--register"],
    ["--sto"], ["--reg"], ["--por", "1"], ["--serv"],      # parser abbreviations
    ["--port"], ["--port", "abc"], ["--port", "-1"], ["--port", "65536"], ["--port="],
    ["--port", "8080", "extra"], ["--port", "１２３"], ["--idle-exit"],
    ["--idle-exit", "-1"], ["--idle-exit", "nan"], ["--idle-exit", "inf"],
    ["--idle-exit", "--stop"], ["serve"], ["-h"], ["--help"], [""], ["--serve="],
    ["--SERVE"], ["--background=1"], [None], ["--port", None],
    ["--idle-exit", "１０"], ["--idle-exit=１０"], ["--idle-exit", "1_0"],
    ["--idle-exit=1_0"], ["--idle-exit", "١٠"],
]


@pytest.mark.parametrize("args", ALLOWED_ARGS, ids=lambda a: " ".join(a) or "none")
def test_serving_arguments_are_allowed(args):
    assert ab.arguments_allowed(args)


@pytest.mark.parametrize("args", REFUSED_ARGS, ids=lambda a: " ".join(map(str, a)) or "empty")
def test_every_other_argument_is_refused(args):
    assert not ab.arguments_allowed(args)


@pytest.mark.parametrize("text", [
    "１０", "١٠", "1０", "1_0", "1_000", "0.5_0", "1e1_0",
], ids=["full-width", "arabic-indic", "mixed", "underscore", "grouped", "fraction",
        "exponent"])
def test_seconds_are_plain_ascii_without_underscores(text):
    """float() takes these too; the port check never did."""
    assert float(text) >= 0  # what made them pass before
    assert not ab._is_seconds(text)


@pytest.mark.parametrize("text", ["0", "10", "0.5", "600", "1e3"])
def test_plain_seconds_are_still_accepted(text):
    assert ab._is_seconds(text)


def test_refused_arguments_run_nothing(bundle, in_process, monkeypatch, tmp_path):
    out = tmp_path / "seen.json"
    monkeypatch.setenv("FAKE_OUT", str(out))
    argv, path = list(sys.argv), list(sys.path)
    for args in (["--stop"], ["--serve", "--register"], ["--autostart", "off"],
                 ["--events-log", "x"]):
        assert ab.run_bundled(args) == ab.EXIT_BAD_ARGUMENTS != 0
    assert not out.exists()  # the bundled file was never run
    assert sys.argv == argv and sys.path == path
    assert ab.ENV_EMBEDDED not in os.environ


@pytest.mark.parametrize("args", [["--stop"], ["--serve", "--register"],
                                  ["--autostart", "off"], ["--events-log"]])
def test_main_refuses_other_arguments_quietly(bundle, tmp_path, args):
    out = tmp_path / "seen.json"
    result = _run_main(["--agent-watchdog", *args],
                       {ab.ENV_OVERRIDE: str(bundle), "FAKE_OUT": str(out)})
    assert result.returncode == ab.EXIT_BAD_ARGUMENTS
    assert result.stdout == "" and result.stderr == ""
    assert not out.exists()


def test_main_handles_the_flag_before_everything_else():
    source = MAIN.read_text(encoding="utf-8")
    assert source.index('"--agent-watchdog"') < source.index('"--packaged-benchmark"')
    assert source.index('"--agent-watchdog"') < source.index("from PySide6")


# --- 3. which copy is started ---------------------------------------------------


def expected_bundled(bundle):
    return ([sys.executable, "-X", "utf8", str(ROOT / "main.py"), "--agent-watchdog"],
            str(bundle.resolve()))


def test_valid_registration_wins_over_the_bundle(layout, bundle):
    home, install = layout
    plan = au.resolve_launch(home)
    assert (plan.launch, plan.cwd, plan.bundled) == (install["launch"], install["cwd"], False)


def test_valid_registration_is_what_the_watcher_starts(qapp, layout, make, bundle):
    home, install = layout
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == [(install["launch"], install["cwd"])]
    assert w.uses_bundled is False
    assert w.autostart_message == "已嘗試自動啟動 Watchdog，等待回應中…"


def test_install_json_autostart_off_is_respected_despite_the_bundle(
        qapp, layout, make, bundle):
    home, install = layout
    install["autostart"] = False
    write_install(home, install)
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == [] and w.uses_bundled is False
    assert w.autostart_message == (
        "不自動啟動：install.json 的 autostart 為關閉（可用 aiwatch.py --autostart on 開啟）。")


@pytest.mark.parametrize("broken", ["schema_2", "launch_short", "exe_missing"])
def test_explicit_off_wins_even_in_an_otherwise_unusable_file(layout, tmp_path, bundle, broken):
    home, install = layout
    BAD[broken][0](install, tmp_path)
    install["autostart"] = False
    write_install(home, install)
    check = au.load_install(home)
    assert not check.ok and check.opted_out
    plan = au.resolve_launch(home)
    assert plan.launch == [] and plan.reason == check.reason


FALLBACK = sorted(set(BAD) - {"autostart_false"})


@pytest.mark.parametrize("name", FALLBACK)
def test_unusable_registration_falls_back_to_the_bundle(layout, tmp_path, bundle, name):
    home, install = layout
    BAD[name][0](install, tmp_path)
    write_install(home, install)
    plan = au.resolve_launch(home)
    assert plan.bundled and (plan.launch, plan.cwd) == expected_bundled(bundle)


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", '"text"', ""])
def test_missing_or_corrupt_install_json_falls_back_to_the_bundle(
        qapp, tmp_path, make, bundle, content):
    home = tmp_path / "home"
    home.mkdir()
    if content is not None:
        (home / "install.json").write_text(content, encoding="utf-8")
    before = sorted(p.name for p in home.iterdir())
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == [expected_bundled(bundle)]
    assert w.uses_bundled is True
    assert w.autostart_message == "正在啟動內建的 Agent 監看，等待回應中…"
    assert w._events_timer.interval() == au.FAST_PROBE_INTERVAL_MS
    assert sorted(p.name for p in home.iterdir()) == before  # never written


def test_registration_written_by_the_bundled_copy_does_not_block(
        qapp, layout, tmp_path, make, bundle):
    """Run by the application's exe, the Watchdog registers that exe as the
    interpreter.  That is outside the contract, so it is never executed; the
    bundled copy is started again instead, and the file is left untouched."""
    home, install = layout
    viewer = tmp_path / "MarkdownViewer.exe"
    viewer.write_text("", encoding="utf-8")
    install["launch"][0] = str(viewer)
    write_install(home, install)
    path = home / "install.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert "launch[0]" in au.load_install(home).reason
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == [expected_bundled(bundle)]
    assert str(viewer) not in w.calls[0][0]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert sorted(p.name for p in home.iterdir()) == ["install.json"]


@pytest.mark.parametrize("name", ["schema_2", "launch_short", "exe_not_python", "exe_missing"])
def test_without_a_bundle_nothing_changes(qapp, layout, tmp_path, make, no_bundle, name):
    home, install = layout
    BAD[name][0](install, tmp_path)
    write_install(home, install)
    reason = au.load_install(home).reason
    plan = au.resolve_launch(home)
    assert (plan.launch, plan.bundled, plan.reason) == ([], False, reason)
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == [] and w.uses_bundled is False
    assert w.autostart_message == "不自動啟動：" + reason
    assert w.bundled_available is False


def test_missing_install_json_without_a_bundle_keeps_the_register_hint(
        qapp, tmp_path, make, no_bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w)
    assert w.calls == []
    assert w.autostart_message.startswith("不自動啟動：找不到 ")
    assert "aiwatch.py --register" in w.autostart_message


def test_viewer_autostart_setting_off_never_starts_the_bundle(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home, autostart=False)
    two_failures(w)
    assert w.calls == [] and w.uses_bundled is False
    assert w.autostart_message == "已在設定關閉「自動啟動 Watchdog」。"


def test_agent_watching_disabled_never_starts_the_bundle(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    store = settings_store.settings()
    store.setValue("watchdog/enabled", False)
    store.sync()
    w.start()
    qapp.processEvents()
    assert not w.running and w.calls == []


def test_answering_watchdog_means_the_bundle_is_not_started(
        qapp, tmp_path, make, bundle, mock_dog):  # noqa: F811
    home = tmp_path / "home"
    home.mkdir()
    w = make(mock_dog.url, home)
    w.start()
    assert wait_until(qapp, lambda: w.available and w.state.boot_id is not None)
    qapp.processEvents()
    assert w.calls == [] and w.uses_bundled is False and w.autostart_message == ""


def test_busy_port_means_the_bundle_is_not_started(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w, http_status=200)
    assert w.calls == [] and "port 被佔用" in w.autostart_message


def test_bundle_shares_the_attempt_budget_and_manual_retry(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w)
    assert len(w.calls) == 1
    w.clock.now += 5  # fast-probe window: no second launch
    w._failed()
    assert len(w.calls) == 1
    for _ in range(5):
        w.clock.now += 61 + au.FAST_PROBE_WINDOW_S
        two_failures(w)
    assert len(w.calls) == au.MAX_ATTEMPTS
    assert "內建的 Agent 監看" in w.autostart_message
    assert "重新偵測" in w.autostart_message
    assert "--register" not in w.autostart_message
    assert "已達重試上限" in w.autostart_diagnostics
    w.redetect()
    assert w.uses_bundled is False and w.autostart_message == ""
    assert w.autostart_diagnostics == ""
    two_failures(w)
    assert len(w.calls) == au.MAX_ATTEMPTS + 1 and w._policy.attempts == 1


def test_bundle_launch_failure_explains_itself(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    w._launcher = lambda launch, cwd: "無法啟動 Watchdog：boom"
    two_failures(w)
    assert w._policy.attempts == 1 and w.uses_bundled is True
    assert "內建的 Agent 監看啟動失敗" in w.autostart_message
    assert "重新偵測" in w.autostart_message
    assert w._events_timer.interval() != au.FAST_PROBE_INTERVAL_MS
    launch, cwd = expected_bundled(bundle)
    assert w.autostart_diagnostics.splitlines() == [
        "原因：無法執行啟動命令（無法啟動 Watchdog：boom）",
        "命令列：" + subprocess.list2cmdline(launch),
        "工作目錄：" + cwd,
        "記錄檔：" + str(home / "aiwatch.log"),
        "啟動錯誤記錄：" + str(home / ab.ERROR_LOG_NAME),
    ]


def test_bundle_that_never_answers_reports_a_timeout(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w)
    assert w.autostart_message == "正在啟動內建的 Agent 監看，等待回應中…"
    assert w.autostart_diagnostics == ""  # still starting: nothing failed yet
    w.clock.now += au.FAST_PROBE_WINDOW_S + 1  # window over, cooldown running
    w._failed()
    assert len(w.calls) == 1
    assert "沒有回應" in w.autostart_message and "重新偵測" in w.autostart_message
    diagnostics = w.autostart_diagnostics
    assert "逾時" in diagnostics.splitlines()[0]
    assert diagnostics.splitlines()[1].endswith("main.py --agent-watchdog")
    assert str(home / "aiwatch.log") in diagnostics
    w._failed()  # later probes inside the cooldown keep the explanation
    assert "沒有回應" in w.autostart_message
    w.clock.now += 61
    w._failed()  # next attempt: back to "starting"
    assert len(w.calls) == 2 and w.autostart_diagnostics == ""
    w._connected()
    assert w.autostart_message == "" and w.autostart_diagnostics == ""


GOOD_ERROR = {"schema": 1, "at": "2026-01-01T00:00:00", "code": "port_in_use",
              "message": "cannot listen on\n127.0.0.1:8787", "watchdog_version": "2.1"}


def test_last_error_is_read_defensively(tmp_path):
    path = tmp_path / ab.LAST_ERROR_NAME
    assert ab.read_last_error(tmp_path) is None  # missing
    path.write_text(json.dumps(GOOD_ERROR), encoding="utf-8")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert ab.read_last_error(tmp_path) == ab.LastError(
        "port_in_use", "cannot listen on 127.0.0.1:8787", "2.1")
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before  # read-only
    path.write_text(json.dumps({"code": 17, "message": "x" * 5000}), encoding="utf-8")
    report = ab.read_last_error(tmp_path)
    assert report.code == "17" and report.version == ""
    assert len(report.message) == ab.LAST_ERROR_MAX_TEXT


@pytest.mark.parametrize("content", [
    "{not json", "[1]", '"text"', "", json.dumps({"message": "no code"}),
    json.dumps({"code": "x"}), json.dumps({"code": True, "message": "m"}),
    json.dumps({"code": ["x"], "message": "m"}), json.dumps({"code": "x", "message": 5}),
    json.dumps({"code": "x", "message": "m", "pad": "p" * (ab.LAST_ERROR_MAX_BYTES + 1)}),
    "[" * 50000,
], ids=["corrupt", "list", "string", "empty", "no-code", "no-message", "bool-code",
        "list-code", "int-message", "oversized", "deeply-nested"])
def test_unusable_last_error_is_ignored(tmp_path, content):
    (tmp_path / ab.LAST_ERROR_NAME).write_text(content, encoding="utf-8")
    assert ab.read_last_error(tmp_path) is None


def test_last_error_as_a_directory_is_ignored(tmp_path):
    (tmp_path / ab.LAST_ERROR_NAME).mkdir()
    assert ab.read_last_error(tmp_path) is None


#: Dropped from reported text: C0 and C1 controls that are not white space,
#: DEL, every mark that changes the text direction, and lone surrogates.
UNPRINTABLE = ([chr(c) for c in range(0x00, 0x09)] + [chr(c) for c in range(0x0E, 0x1C)]
               + [chr(c) for c in range(0x7F, 0xA0) if c != 0x85]
               + ["\u061c", "\u200e", "\u200f"]
               + [chr(c) for c in range(0x202A, 0x202F)]
               + [chr(c) for c in range(0x2066, 0x206A)]
               + ["\ud800", "\udbff", "\udc00", "\udfff"])


@pytest.mark.parametrize("char", UNPRINTABLE, ids=lambda c: f"U+{ord(c):04X}")
def test_reported_text_drops_control_and_direction_characters(tmp_path, char):
    report = _reported(tmp_path, code=f"c{char}d", message=f"port{char}8787",
                       watchdog_version=f"2{char}.1")
    assert (report.code, report.message, report.version) == ("cd", "port8787", "2.1")


def test_reported_text_keeps_what_can_be_shown(tmp_path):
    """White space of any kind (NEL and the line separators included) becomes
    one space; everything printable stays, astral characters too."""
    message = "a\tb\nc\r\nd\x0be\x0cf\x1cg\x1fh\x85i j k　l 中文 \U0001f600"
    report = _reported(tmp_path, message=message)
    assert report.message == "a b c d e f g h i j k l 中文 \U0001f600"


def test_reversed_text_cannot_disguise_a_report(tmp_path):
    report = _reported(tmp_path, message="\u202eexe.lmth\u202c ok \u2067x\u2069")
    assert report.message == "exe.lmth ok x"


def test_diagnostics_include_what_the_watchdog_reported(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w)
    w.clock.now += au.FAST_PROBE_WINDOW_S + 1
    w._failed()  # timed out
    assert len(w.autostart_diagnostics.splitlines()) == 5
    (home / ab.LAST_ERROR_NAME).write_text(json.dumps(GOOD_ERROR), encoding="utf-8")
    assert w.autostart_diagnostics.splitlines()[5] == (
        "Agent 監看回報（版本 2.1）：[port_in_use] cannot listen on 127.0.0.1:8787")
    (home / ab.LAST_ERROR_NAME).write_text("{broken", encoding="utf-8")
    assert len(w.autostart_diagnostics.splitlines()) == 5  # silently skipped


def _reported(tmp_path, **fields):
    data = {"schema": 1, "at": 1767225600.5, "code": 3, "message": "m",
            "watchdog_version": "2.1", **fields}
    (tmp_path / ab.LAST_ERROR_NAME).write_text(json.dumps(data), encoding="utf-8")
    return ab.read_last_error(tmp_path)


@pytest.mark.parametrize("code, expected", [
    (3, "預設的連接埠被其他程式佔用（不是 Agent 監看程式）。"),
    (4, "內建的 Agent 監看檔案不完整，請重新安裝 Markdown Viewer。"),
    (5, "無法寫入 Agent 監看的資料資料夾（{home}）。"),
    (6, "內建啟動參數不正確，請回報此問題。"),
    (7, "啟動時發生未預期的錯誤，請附上診斷資訊回報。"),
])
def test_known_exit_codes_are_explained_next_to_the_raw_report(tmp_path, code, expected):
    assert sorted(ab.LAST_ERROR_HINTS) == [3, 4, 5, 6, 7]
    report = _reported(tmp_path, code=code, message="raw text")
    assert report.code == str(code) and report.number == code
    hint = expected.replace("{home}", str(tmp_path))
    assert ab.last_error_hint(report, tmp_path) == hint
    lines = ab.last_error_lines(report, tmp_path)
    assert lines[0] == f"Agent 監看回報（版本 2.1）：[{code}] raw text"  # raw, kept
    assert lines[1] == f"結束碼 {code} 的說明：{hint}"
    assert "{" not in lines[1]


@pytest.mark.parametrize("code", [0, 1, 2, 8, 99, -3, "3", "5", "port_in_use", ""],
                         ids=repr)
def test_other_codes_are_shown_as_reported_without_explanation(tmp_path, code):
    report = _reported(tmp_path, code=code, at=None)
    assert ab.last_error_hint(report, tmp_path) == ""
    assert ab.last_error_lines(report, tmp_path) == [
        f"Agent 監看回報（版本 2.1）：[{code}] m"]


def test_report_time_is_shown_in_local_time(tmp_path):
    from datetime import datetime

    report = _reported(tmp_path, code="x", at=1767225600.5)
    expected = datetime.fromtimestamp(1767225600.5).strftime("%Y-%m-%d %H:%M:%S")
    assert ab.last_error_time(report) == expected
    assert ab.last_error_lines(report, tmp_path)[-1] == f"回報時間：{expected}"
    assert ab.last_error_time(_reported(tmp_path, at=1767225600)) == expected  # int


@pytest.mark.parametrize("at", [
    None, "2026-01-01T00:00:00", "1767225600", True, [1], {"t": 1}, 1e300, -1e300,
    float("nan"), float("inf"),
], ids=repr)
def test_unusable_report_time_is_left_out(tmp_path, at):
    report = _reported(tmp_path, at=at)
    assert report is not None and ab.last_error_time(report) == ""
    lines = ab.last_error_lines(report, tmp_path)
    assert len(lines) == 2 and lines[1].startswith("結束碼 3 的說明：")


def test_missing_report_time_is_left_out(tmp_path):
    (tmp_path / ab.LAST_ERROR_NAME).write_text(
        json.dumps({"code": 4, "message": "m"}), encoding="utf-8")
    report = ab.read_last_error(tmp_path)
    assert report.at is None and len(ab.last_error_lines(report, tmp_path)) == 2


def test_diagnostics_explain_a_known_exit_code(qapp, tmp_path, make, bundle):
    from datetime import datetime

    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w)
    w.clock.now += au.FAST_PROBE_WINDOW_S + 1
    w._failed()  # timed out
    (home / ab.LAST_ERROR_NAME).write_text(json.dumps(
        {"schema": 1, "at": 1767225600.0, "code": 5, "message": "cannot write",
         "watchdog_version": "2.1"}), encoding="utf-8")
    when = datetime.fromtimestamp(1767225600.0).strftime("%Y-%m-%d %H:%M:%S")
    assert w.autostart_diagnostics.splitlines()[5:] == [
        "Agent 監看回報（版本 2.1）：[5] cannot write",
        f"結束碼 5 的說明：無法寫入 Agent 監看的資料資料夾（{home}）。",
        f"回報時間：{when}",
    ]


def test_registered_copy_never_shows_bundle_diagnostics(qapp, layout, make, bundle):
    home, _ = layout
    w = make(dead_url(), home)
    w._launcher = lambda launch, cwd: "無法啟動 Watchdog：boom"
    two_failures(w)
    assert w.autostart_message == "無法啟動 Watchdog：boom"
    w.clock.now += au.FAST_PROBE_WINDOW_S + 61
    two_failures(w)
    assert w.autostart_diagnostics == ""


def test_bundle_is_spawned_detached_and_never_killed(qapp, tmp_path, make, bundle):
    seen = []

    class Popen:
        def __init__(self, *args, **kwargs):
            seen.append((args, kwargs))

        def kill(self):
            raise AssertionError("must never kill the Watchdog")

        terminate = send_signal = wait = kill

    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    w._launcher = lambda launch, cwd: au.spawn_detached(
        launch, cwd, popen=Popen, environ={"KEEP": "1", "PYTHONPATH": "x", "_MEIPASS2": "y"})
    two_failures(w)
    ((args, kwargs),) = seen
    assert args == (expected_bundled(bundle)[0],)
    assert kwargs["cwd"] == str(bundle.resolve()) and kwargs["shell"] is False
    assert kwargs["env"] == {"KEEP": "1"}
    assert kwargs["creationflags"] & au.DETACHED_PROCESS
    w.pause()
    w.shutdown()
    assert not any(isinstance(v, Popen) for v in vars(w).values())


def test_bundled_available_follows_the_lookup(qapp, tmp_path, make, bundle, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    assert w.bundled_available is True
    monkeypatch.setenv(ab.ENV_OVERRIDE, str(tmp_path / "gone"))
    assert w.bundled_available is False


# --- 4. the panel's help text -----------------------------------------------------

OLD_NO_BAT = ("AI_Agent_Watchdog 未啟動\n\n"
              "尚未指定 AI_Agent_Watchdog 的 start.bat。\n"
              "請自行啟動 Watchdog 後按「重新偵測」，"
              "或在設定值 watchdog/start_bat 記錄 start.bat 路徑。\n\n")


def _info_text(qapp, **fields):
    watcher = SimpleNamespace(
        available=False, base_url="http://127.0.0.1:1",
        config=SimpleNamespace(enabled=True, start_bat="", autostart=True),
        redetect=lambda: None, **fields)
    host, dock = _panel_dock(watcher)
    try:
        dock._show_info()
        return dock._info_label.text(), dock._redetect.isVisible()
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_info_text_without_a_bundle_is_unchanged(qapp):
    text, button = _info_text(qapp)
    assert text == OLD_NO_BAT + "自動啟動：已開啟（依 Watchdog 登記的 install.json）。\n"
    assert button
    reason = ("不自動啟動：找不到 X，Watchdog 尚未登記。"
              "請在 Watchdog 資料夾執行 aiwatch.py --register。")
    text, _ = _info_text(qapp, autostart_message=reason, uses_bundled=False,
                         bundled_available=False)
    assert text == OLD_NO_BAT + f"自動啟動：{reason}\n"


def test_info_text_while_the_bundle_is_starting(qapp):
    note = "正在啟動內建的 Agent 監看，等待回應中…"
    text, button = _info_text(qapp, autostart_message=note, uses_bundled=True,
                              bundled_available=True)
    assert text == f"Agent 監看尚未就緒\n\n{note}\n"
    assert button
    for jargon in ("--register", "install.json", "start.bat", "aiwatch.py"):
        assert jargon not in text


def _failed_watcher(**fields):
    return SimpleNamespace(
        available=False, base_url="http://127.0.0.1:1",
        config=SimpleNamespace(enabled=True, start_bat="", autostart=True),
        redetect=lambda: None, **fields)


def test_info_text_when_the_bundle_failed_shows_copyable_diagnostics(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    note = "內建的 Agent 監看啟動失敗：無法執行啟動命令。請按「重新偵測」重試。"
    diagnostics = "原因：boom\n命令列：X.exe --agent-watchdog\n記錄檔：C:/h/aiwatch.log"
    watcher = _failed_watcher(autostart_message=note, uses_bundled=True,
                              bundled_available=True, autostart_diagnostics=diagnostics)
    host, dock = _panel_dock(watcher)
    try:
        dock._show_info()
        text = dock._info_label.text()
        assert text == (f"Agent 監看尚未就緒\n\n{note}\n"
                        f"\n診斷資訊（回報問題時請一併附上）：\n{diagnostics}\n")
        assert "--register" not in text and "start.bat" not in text
        assert (dock._info_label.textInteractionFlags()
                & Qt.TextInteractionFlag.TextSelectableByMouse)
        assert dock._redetect.isVisible() and dock._copy_diagnostics.isVisible()
        QApplication.clipboard().clear()
        dock._copy_diagnostics.click()
        assert QApplication.clipboard().text() == diagnostics
        # once it is merely starting again the button goes away
        watcher.autostart_diagnostics = ""
        dock._show_info()
        assert not dock._copy_diagnostics.isVisible()
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_copy_button_is_hidden_without_a_bundle(qapp):
    host, dock = _panel_dock(_failed_watcher(autostart_message="不自動啟動：x"))
    try:
        dock._show_info()
        assert not dock._copy_diagnostics.isVisible()
        assert "診斷資訊" not in dock._info_label.text()
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_info_text_with_a_bundle_before_any_attempt(qapp):
    text, _ = _info_text(qapp, autostart_message="", uses_bundled=False,
                         bundled_available=True)
    assert "內建的 Agent 監看" in text
    assert "尚未指定" not in text and "start.bat" not in text


def test_info_text_from_a_real_watcher_using_the_bundle(qapp, tmp_path, make, bundle):
    home = tmp_path / "home"
    home.mkdir()
    w = make(dead_url(), home)
    two_failures(w)
    host, dock = _panel_dock(w)
    try:
        dock._show_info()
        text = dock._info_label.text()
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()
    assert text == "Agent 監看尚未就緒\n\n正在啟動內建的 Agent 監看，等待回應中…\n"


#: What a report may carry: a path or a token without a single space.
LONG_RUN = "x" * 5000


def _reporting_watcher(diagnostics):
    return _failed_watcher(autostart_message="內建的 Agent 監看啟動失敗。", uses_bundled=True,
                           bundled_available=True, autostart_diagnostics=diagnostics)


def _docked_panel(qapp, watcher):
    """The panel docked in a main window the way the application docks it."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    host, dock = _panel_dock(watcher)
    host.setCentralWidget(QWidget())
    host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    dock.show()
    qapp.processEvents()
    return host, dock


def _min_widths(qapp, host, dock):
    qapp.processEvents()
    return dock.minimumSizeHint().width(), host.minimumSizeHint().width()


def test_long_diagnostics_never_widen_the_panel_or_the_window(qapp):
    """A label makes an unbreakable run its minimum width: the dock, and with
    it the main window, could no longer be narrower than the whole run."""
    watcher = _reporting_watcher("原因：short")
    host, dock = _docked_panel(qapp, watcher)
    try:
        dock._show_info()
        before = _min_widths(qapp, host, dock)
        watcher.autostart_diagnostics = "原因：" + LONG_RUN
        dock._show_info()
        assert LONG_RUN in dock._info_label.text()
        assert _min_widths(qapp, host, dock) == before
        assert before[1] < 1000
        host.resize(700, 500)
        qapp.processEvents()
        assert host.width() == 700 and dock.width() < 700
        # the run is wrapped inside the panel instead (and scrolls when tall)
        label = dock._info_label
        assert label.sizeHint().height() > 5 * label.fontMetrics().lineSpacing()
        assert label.minimumSizeHint().height() <= 3 * label.fontMetrics().lineSpacing()
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


def test_reported_text_is_shown_as_plain_text(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    diagnostics = ("原因：<b>bold</b> <a href=\"https://example.com/\">link</a>"
                   " <img src=\"file:///c:/x.png\"> &amp;")
    host, dock = _docked_panel(qapp, _reporting_watcher(diagnostics))
    try:
        dock._show_info()
        label = dock._info_label
        assert label.textFormat() == Qt.TextFormat.PlainText
        assert diagnostics in label.text()
        assert diagnostics in label.document().toPlainText()  # nothing was parsed
        html = label.document().toHtml()
        assert "&lt;b&gt;bold&lt;/b&gt;" in html and "&amp;amp;" in html
        assert "<a " not in html and "<img " not in html and "font-weight:700" not in html
        QApplication.clipboard().clear()
        dock._copy_diagnostics.click()
        assert QApplication.clipboard().text() == diagnostics
    finally:
        dock.shutdown()
        host.close()
        host.deleteLater()
        qapp.processEvents()


# --- 5. packaging -------------------------------------------------------------------


def test_packaging_without_the_directory_adds_nothing(tmp_path):
    assert pp.agent_bundle_source(tmp_path, {}) is None
    (tmp_path / "agent_watchdog").mkdir()  # a folder without the script
    assert pp.agent_bundle_source(tmp_path, {}) is None
    assert pp.agent_bundle_datas(None) == []
    assert pp.stdlib_imports(None) == []


def test_packaging_names_match_the_runtime_lookup():
    assert pp.AGENT_BUNDLE_DIR == ab.BUNDLE_DIR_NAME
    assert pp.AGENT_BUNDLE_SCRIPT == ab.SCRIPT_NAME
    assert pp.AGENT_BUNDLE_ENV == ab.ENV_OVERRIDE


def _packaging_tree(root: Path) -> Path:
    source = root / "agent_watchdog"
    (source / "static").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "__pycache__").mkdir()
    (source / "aiwatch.py").write_text(
        "import argparse, json\n"
        "import http.server\n"
        "import psutil\n"
        "import unittest\n"
        "from urllib.parse import parse_qs\n"
        "from ctypes import wintypes\n"
        "from helper import thing\n"
        "from . import relative\n"
        "def later():\n"
        "    import sqlite3\n"
        "    try:\n"
        "        import winreg\n"
        "    except ImportError:\n"
        "        pass\n",
        encoding="utf-8")
    (source / "helper.py").write_text("import queue\nfrom os import path\n", encoding="utf-8")
    (source / "broken.py").write_text("def (:\n", encoding="utf-8")
    (source / "dash.html").write_text("<html>", encoding="utf-8")
    (source / "static" / "app.js").write_text("//", encoding="utf-8")
    (source / "tests" / "test_x.py").write_text("import tkinter\n", encoding="utf-8")
    (source / "__pycache__" / "aiwatch.cpython-313.pyc").write_bytes(b"\0")
    for private in ("hidden.json", "remotes.json", "events.jsonl", "old.log"):
        (source / private).write_text("{}", encoding="utf-8")
    return source


def test_packaging_collects_the_directory_as_data(tmp_path):
    source = _packaging_tree(tmp_path)
    assert pp.agent_bundle_source(tmp_path, {}) == source
    datas = pp.agent_bundle_datas(source)
    shipped = sorted((Path(src).relative_to(source).as_posix(), dest) for src, dest in datas)
    assert shipped == [
        ("aiwatch.py", "agent_watchdog"),
        ("broken.py", "agent_watchdog"),
        ("dash.html", "agent_watchdog"),
        ("helper.py", "agent_watchdog"),
        ("static/app.js", "agent_watchdog/static"),
    ]


# What a real working copy accumulates.  Only the names listed in SHIPPED may
# ever reach the installer.
WORKING_COPY = [
    "aiwatch.py", "state_core.py", "scanner_core.py", "remote_scan.py",
    "dash.html", "office.html", "Upper.PY", "Page.HTML",
    "static/watchdog.js", "static/css/site.css", "static/img/logo.png",
    # per-machine state and private configuration
    "endpoint.json", "install.json", "last_error.json", "hidden.json",
    "remotes.json", "remotes.local.json", "remotes.example.json", "events.jsonl",
    "events.jsonl.1", "aiwatch.log", "aiwatch.log.1", "aiwatch.log.lock", ".env",
    # documentation, launchers, tooling
    "README.md", "start.bat", "update.bat", ".gitignore", "requirements.txt",
    "notes.txt", "aiwatch.pyc", "aiwatch.py.bak", "data.sqlite",
    # folders that must never be entered
    ".venv/Lib/site.py", ".venv/pyvenv.cfg", ".vscode/settings.json", ".git/config",
    "__pycache__/aiwatch.cpython-313.pyc", "__PYCACHE__/x.py",
    "tests/test_aiwatch.py", "Tests/conftest.py", "TESTS/page.html",
    "docs/guide.html", "tools/helper.py", "venv/Lib/os.py",
    # the same rules inside static/
    "static/.hidden.js", "static/.cache/x.js", "static/__pycache__/x.pyc",
    "static/tests/fixture.js", "static/Tests/page.html",
    # dot files that otherwise match the allowlist
    ".secret.py", ".draft.html",
]
SHIPPED = [
    ("Page.HTML", "agent_watchdog"), ("Upper.PY", "agent_watchdog"),
    ("aiwatch.py", "agent_watchdog"), ("dash.html", "agent_watchdog"),
    ("office.html", "agent_watchdog"), ("remote_scan.py", "agent_watchdog"),
    ("scanner_core.py", "agent_watchdog"), ("state_core.py", "agent_watchdog"),
    ("static/css/site.css", "agent_watchdog/static/css"),
    ("static/img/logo.png", "agent_watchdog/static/img"),
    ("static/watchdog.js", "agent_watchdog/static"),
]


def test_packaging_is_an_allowlist(tmp_path):
    source = tmp_path / "agent_watchdog"
    for name in WORKING_COPY:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import sqlite3\n" if name.lower().endswith(".py") else "x",
                        encoding="utf-8")
    datas = pp.agent_bundle_datas(pp.agent_bundle_source(tmp_path, {}))
    shipped = sorted((Path(src).relative_to(source).as_posix(), dest) for src, dest in datas)
    assert shipped == sorted(SHIPPED)
    assert pp.stdlib_imports(source) == ["sqlite3"]


def test_import_scan_only_reads_what_is_shipped(tmp_path):
    source = tmp_path / "agent_watchdog"
    (source / "static").mkdir(parents=True)
    (source / "aiwatch.py").write_text("import json\n", encoding="utf-8")
    for name, module in (("tests/t.py", "tkinter"), (".venv/x.py", "sqlite3"),
                         ("tools/h.py", "winreg"), (".hidden.py", "queue"),
                         ("static/gen.py", "uuid")):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"import {module}\n", encoding="utf-8")
    # static/gen.py is shipped (everything under static/ is), so it is scanned
    assert pp.stdlib_imports(source) == ["json", "uuid"]


def test_packaging_source_can_live_outside_the_repository(tmp_path):
    source = _packaging_tree(tmp_path / "elsewhere")
    env = {pp.AGENT_BUNDLE_ENV: str(source)}
    assert pp.agent_bundle_source(tmp_path / "repo", env) == source
    assert pp.agent_bundle_source(tmp_path / "repo", {pp.AGENT_BUNDLE_ENV: "  "}) is None


def test_stdlib_imports_are_found_for_hidden_imports(tmp_path):
    source = _packaging_tree(tmp_path)
    found = pp.stdlib_imports(source, excludes=["tkinter", "unittest"])
    assert found == sorted(found)
    for module in ("argparse", "json", "http.server", "urllib.parse", "ctypes",
                   "ctypes.wintypes", "sqlite3", "winreg", "queue", "os"):
        assert module in found
    # third party, local, relative, excluded and test-only imports stay out
    for module in ("psutil", "helper", "relative", "unittest", "tkinter", "os.path.join"):
        assert module not in found
    assert "unittest" in pp.stdlib_imports(source)


def test_spec_wires_the_optional_bundle_in():
    spec = (ROOT / "markdown_viewer.spec").read_text(encoding="utf-8")
    assert "agent_bundle_source(SPECPATH)" in spec
    assert "+ _agent_datas" in spec and "+ _agent_hiddenimports" in spec
    assert "stdlib_imports(_agent_source, excludes=EXCLUDES)" in spec


def test_installer_ends_only_this_installations_bundled_copy():
    main_iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert '#include "installer_agent_watchdog.iss"' in main_iss
    code = (ROOT / "installer_agent_watchdog.iss").read_text(encoding="utf-8")
    assert f"AgentWatchdogFlag = '{ab.CLI_FLAG}'" in code
    assert "CompareText(Path, ExePath) <> 0" in code  # this folder's exe only
    assert "function PrepareToInstall" in code and "function InitializeUninstall" in code
    # the clean stop comes first, and only for an installation with the bundle
    assert "AgentWatchdogStopFlag = '--agent-watchdog-stop'" in code
    assert "AgentWatchdogScript = '_internal\\agent_watchdog\\aiwatch.py'" in code
    for function in ("PrepareToInstall", "InitializeUninstall"):
        body = code[code.index("function " + function):]
        assert (body.index("AskBundledAgentWatchdogToStop")
                < body.index("StopBundledAgentWatchdog("))
    assert "taskkill" not in code.lower()  # never by image name alone
    # the flag must be the first argument, not text somewhere in the command line
    assert "CompareText(FirstArgument(CommandLine), AgentWatchdogFlag) = 0" in code
    assert "Pos(" not in code


def _tracked_manifest() -> dict[str, str]:
    """{relative path: sha256} as listed in the tracked bundle's MANIFEST.txt."""
    listed = {}
    for line in (ROOT / ab.BUNDLE_DIR_NAME / "MANIFEST.txt").read_text(
            encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and len(fields[2]) == 64:
            listed[fields[0]] = fields[2]
    return listed


def test_tracked_bundle_is_exactly_what_its_manifest_lists():
    import hashlib

    folder = ROOT / ab.BUNDLE_DIR_NAME
    listed = _tracked_manifest()
    assert "aiwatch.py" in listed
    present = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    assert present == sorted([*listed, "MANIFEST.txt"])
    for name, digest in listed.items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest, name


def test_tracked_bundle_is_what_packaging_ships():
    source = pp.agent_bundle_source(ROOT, {})
    assert source == ROOT / ab.BUNDLE_DIR_NAME
    shipped = sorted(Path(src).relative_to(source).as_posix()
                     for src, _dest in pp.agent_bundle_datas(source))
    assert shipped == sorted(_tracked_manifest())


def test_tracked_bundle_is_never_converted_on_checkout():
    # A converted line ending would no longer match the manifest's hashes.
    lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "agent_watchdog/** -text" in lines
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not any(line.strip("/") == ab.BUNDLE_DIR_NAME for line in ignored)


# --- 6. the hidden panel's page is frozen -------------------------------------------


def _loaded_dock(qapp, mock_dog):
    watcher = SimpleNamespace(
        available=True, base_url=mock_dog.url,
        config=SimpleNamespace(enabled=True, start_bat=""), redetect=lambda: None)
    host, dock = _panel_dock(watcher)
    dock.show()
    assert wait_until(qapp, lambda: dock.view.url().host() == "127.0.0.1")
    return host, dock


def _close(qapp, host, dock):
    dock.shutdown()
    host.close()
    host.deleteLater()
    qapp.processEvents()


def _state(dock):
    return dock._page.lifecycleState()


def test_hidden_panel_is_frozen_on_the_next_event_loop_turn(qapp, mock_dog):
    from PySide6.QtWebEngineCore import QWebEnginePage

    states = QWebEnginePage.LifecycleState
    host, dock = _loaded_dock(qapp, mock_dog)
    try:
        requested = []
        real = dock._set_lifecycle
        dock._set_lifecycle = lambda state: (requested.append(state), real(state))[1]
        dock.hide()
        # Not inside the visibilityChanged handler: there the view still counts
        # as visible and Qt would drop the request.
        assert requested == [] and dock._freeze_timer.isActive()
        assert wait_until(qapp, lambda: _state(dock) == states.Frozen)
        assert requested == [states.Frozen] and not dock._freeze_timer.isActive()
        dock.show()
        assert requested[-1] == states.Active and _state(dock) == states.Active
    finally:
        _close(qapp, host, dock)


def test_showing_again_cancels_a_pending_freeze(qapp, mock_dog):
    from PySide6.QtWebEngineCore import QWebEnginePage

    states = QWebEnginePage.LifecycleState
    host, dock = _loaded_dock(qapp, mock_dog)
    try:
        for _ in range(5):  # quick toggling, ending shown
            dock.hide()
            dock.show()
        assert not dock._freeze_timer.isActive()
        wait_until(qapp, lambda: False, timeout=0.3)
        assert _state(dock) == states.Active
        for _ in range(5):  # ending hidden
            dock.show()
            dock.hide()
        assert wait_until(qapp, lambda: _state(dock) == states.Frozen)
        dock.show()
        assert _state(dock) == states.Active
    finally:
        _close(qapp, host, dock)


def test_freeze_waits_until_the_page_is_really_hidden(qapp, mock_dog, monkeypatch):
    """What a real window does (offscreen never reports the page visible)."""
    from PySide6.QtWebEngineCore import QWebEnginePage

    from app import agents_panel

    states = QWebEnginePage.LifecycleState
    monkeypatch.setattr(agents_panel, "_FREEZE_RETRY_MS", 5)
    host, dock = _loaded_dock(qapp, mock_dog)
    try:
        visible = [True]
        dock._page.isVisible = lambda: visible[0]
        dock.hide()
        wait_until(qapp, lambda: dock._freeze_retries >= 3, timeout=2)
        assert dock._freeze_retries >= 3 and _state(dock) == states.Active
        visible[0] = False
        assert wait_until(qapp, lambda: _state(dock) == states.Frozen)
    finally:
        _close(qapp, host, dock)


def test_never_loaded_panel_is_left_alone_when_hidden(qapp):
    from PySide6.QtWebEngineCore import QWebEnginePage

    watcher = SimpleNamespace(
        available=False, base_url="http://127.0.0.1:1",
        config=SimpleNamespace(enabled=True, start_bat=""), redetect=lambda: None)
    host, dock = _panel_dock(watcher)
    try:
        requested = []
        dock._set_lifecycle = requested.append
        dock.show()
        dock.hide()
        wait_until(qapp, lambda: False, timeout=0.2)
        assert not dock._page_loaded
        assert QWebEnginePage.LifecycleState.Frozen not in requested
    finally:
        _close(qapp, host, dock)


def test_shutdown_cancels_a_pending_freeze(qapp, mock_dog):
    host, dock = _loaded_dock(qapp, mock_dog)
    dock.hide()
    assert dock._freeze_timer.isActive()
    _close(qapp, host, dock)
    assert not dock._freeze_timer.isActive()
