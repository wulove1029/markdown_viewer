"""Fresh checks for explicit diagnostics and packaged benchmark control flow."""

import json
import runpy
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest
from PySide6 import QtCore, QtNetwork, QtWidgets
from PySide6.QtTest import QTest
from shiboken6 import isValid

from app import diagnostics
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
)
from tools import benchmark_packaged


def test_help_diagnostics_action_invokes_explicit_export_for_current_window(
    make_window, monkeypatch  # noqa: F811
):
    window = make_window()
    exports = []
    monkeypatch.setattr(diagnostics, "export_dialog", exports.append)
    # Look the menu up by title instead of QAction.menu(): on PySide6 6.11.0 the
    # wrapper returned by QAction.menu() can already point at a deleted QMenu.
    help_menu = next(
        menu for menu in window.menuBar().findChildren(QtWidgets.QMenu)
        if menu.title() == "說明(&H)"
    )
    action = next(item for item in help_menu.actions() if item.text() == "匯出效能診斷…")
    assert action.isEnabled()
    assert exports == []
    action.trigger()
    assert exports == [window]


def test_packaged_benchmark_entry_bypasses_normal_logging_ipc_and_qt_startup(monkeypatch):
    dispatched = []
    stub = ModuleType("tools.benchmark_file_switching")
    stub.main = lambda: dispatched.append(list(sys.argv))
    monkeypatch.setitem(sys.modules, stub.__name__, stub)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Isolated benchmark entry reached normal application startup")

    entry = Path(__file__).resolve().parents[1] / "main.py"
    monkeypatch.setattr(sys, "argv", [str(entry), "--packaged-benchmark", "--output", "test.json"])
    # Restore Qt before pytest-qt's post-call event processing runs.
    with monkeypatch.context() as patched:
        patched.setattr(QtWidgets, "QApplication", unexpected)
        patched.setattr(QtNetwork, "QLocalSocket", unexpected)
        patched.setattr(QtNetwork, "QLocalServer", unexpected)
        patched.setattr(QtCore.QStandardPaths, "writableLocation", unexpected)
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(entry), run_name="__main__")
    assert result.value.code == 0
    assert dispatched == [[str(entry), "--output", "test.json"]]


@pytest.mark.parametrize("child_code", [0, 7])
def test_packaged_runner_keeps_scope_and_requires_successful_child(
    tmp_path, monkeypatch, child_code,
):
    executable = tmp_path / "viewer.exe"
    executable.write_bytes(b"test-only placeholder")
    output = tmp_path / "report.json"
    launches = []

    def execute(command, **kwargs):
        launches.append((command, kwargs))
        child_output = Path(command[command.index("--output") + 1])
        child_output.write_text(json.dumps({
            "passed": True, "window_ready_wall_time": time.time() + 0.01,
            "samples": [{"warm": True, "rss_tree_bytes": 100},
                        {"warm": True, "rss_tree_bytes": 105}],
            "summary": {"ready_ms": {"n": 2}},
        }), encoding="utf-8")
        return subprocess.CompletedProcess(command, child_code)

    monkeypatch.setattr(benchmark_packaged.subprocess, "run", execute)
    monkeypatch.setattr(sys, "argv", ["benchmark_packaged", "--exe", str(executable),
                                      "--output", str(output)])
    with pytest.raises(SystemExit) as result:
        benchmark_packaged.main()
    assert result.value.code == (0 if child_code == 0 else 1)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["exit_code"] == child_code
    assert report["rss_warm_growth_percent"] == 5.0
    assert "excludes first document paint" in report["launch_scope"]
    assert "not an installed first-run or cold-boot measurement" in report["launch_scope"]
    command, options = launches[0]
    assert command[1] == "--packaged-benchmark"
    assert options["env"]["QT_QPA_PLATFORM"] == "offscreen"


def test_packaged_runner_rejects_stale_child_report_without_overwriting_parent(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "viewer.exe"
    executable.write_bytes(b"test-only placeholder")
    output = tmp_path / "report.json"
    output.write_bytes(b"previous validated report")
    output.with_name("report-child.json").write_text(json.dumps({
        "window_ready_wall_time": 1, "passed": True, "samples": [],
    }), encoding="utf-8")
    monkeypatch.setattr(benchmark_packaged.subprocess, "run",
                        lambda command, **kwargs: subprocess.CompletedProcess(command, 0))
    monkeypatch.setattr(sys, "argv", ["benchmark_packaged", "--exe", str(executable),
                                      "--output", str(output)])
    with pytest.raises(SystemExit, match="fresh readiness report"):
        benchmark_packaged.main()
    assert output.read_bytes() == b"previous validated report"


def test_data_safety_fixture_releases_qt_window_before_settings_restore(qapp):
    from tests.test_editor_data_safety import make_data_safety_window
    from tests.test_window_integration import _dispose_window

    fixture = make_data_safety_window.__wrapped__(qapp)
    factory = next(fixture)
    window = factory()
    try:
        with pytest.raises(StopIteration):
            next(fixture)
        assert not isValid(window), "Fixture left its QWidget alive after teardown"
    finally:
        _dispose_window(window)


def test_large_source_workspace_mode_control_cannot_enable_split_preview(
    make_window, tmp_path, monkeypatch  # noqa: F811
):
    from app import large_source, view_mode

    monkeypatch.setattr(large_source, "MAX_PREVIEW_BYTES", 1024)
    source = tmp_path / "large.md"
    source.write_text("# Large\n" + "line\n" * 500, encoding="utf-8")
    window = make_window()
    window.open_path(str(source))
    assert window._open_source_editor()
    deadline = time.monotonic() + 5
    while not window._edit_mode and time.monotonic() < deadline:
        QTest.qWait(5)
    assert window._edit_mode
    assert window._active_editor_state()["limited_source"]
    mode_control = window._workspace_controls.mode
    selected = mode_control.findData(view_mode.SPLIT)
    mode_control.setCurrentIndex(selected)
    mode_control.activated.emit(selected)
    assert window._view_mode == view_mode.EDIT
    assert mode_control.currentData() == view_mode.EDIT
    assert window._edit_preview is None
