"""Focus-triggered library change detection (work package I1)."""

from __future__ import annotations

import functools
import inspect
import logging
import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings
from PySide6.QtWidgets import QApplication

from app import library_watch, session_state
from app import window as window_mod
from app.library_watch import (
    LibraryChangeWatcher,
    LibrarySignature,
    compute_library_signature,
)
from app.recovery import RecoveryStore
from tests.test_window_integration import (
    _dispose_window,
    _FakePanel,
    _FakePdfView,
    _FakeRenderer,
    _FakeTagIndex,
)


def _pump(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return predicate()


def _bump(path: Path, delta_ns=2_000_000_000):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + delta_ns))


# ---------------------------------------------------------------- signature
def test_signature_changes_on_add_delete_modify(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    base = compute_library_signature([tmp_path])
    assert base is not None and base.count == 1

    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    added = compute_library_signature([tmp_path])
    assert added.differs_from(base) and added.count == 2

    _bump(tmp_path / "b.md")
    modified = compute_library_signature([tmp_path])
    assert modified.differs_from(added) and modified.count == 2

    (tmp_path / "b.md").unlink()
    deleted = compute_library_signature([tmp_path])
    assert deleted.differs_from(modified) and not deleted.differs_from(base)


def test_signature_ignores_excluded_and_non_markdown(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "private").mkdir()
    (tmp_path / ".hidden").mkdir()
    base = compute_library_signature([tmp_path], ["private"])
    (tmp_path / "node_modules" / "x.md").write_text("x", encoding="utf-8")
    (tmp_path / "private" / "y.md").write_text("y", encoding="utf-8")
    (tmp_path / ".hidden" / "z.md").write_text("z", encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(b"1")
    assert not compute_library_signature([tmp_path], ["private"]).differs_from(base)
    # Without the user exclusion the same change is visible.
    assert compute_library_signature([tmp_path], []).differs_from(base)


def test_signature_over_cap_is_stable_and_detects_first_n(tmp_path):
    for i in range(5):
        (tmp_path / f"{i}.md").write_text("x", encoding="utf-8")
    first = compute_library_signature([tmp_path], max_files=3)
    second = compute_library_signature([tmp_path], max_files=3)
    assert first.truncated and first.count == 3
    assert not second.differs_from(first)  # no change -> no rebuild
    _bump(tmp_path / "4.md")  # beyond the first N: undetected by design
    assert not compute_library_signature([tmp_path], max_files=3).differs_from(first)
    _bump(tmp_path / "1.md")  # inside the first N
    assert compute_library_signature([tmp_path], max_files=3).differs_from(first)


def test_exactly_cap_files_is_not_truncated(tmp_path):
    for i in range(3):
        (tmp_path / f"{i}.md").write_text("x", encoding="utf-8")
    assert not compute_library_signature([tmp_path], max_files=3).truncated


def test_over_cap_watcher_rebuilds_only_on_change(tmp_path, qapp):
    for i in range(5):
        (tmp_path / f"{i}.md").write_text("x", encoding="utf-8")
    w = LibraryChangeWatcher(
        lambda: [tmp_path], lambda: True, lambda: [], None, debounce_ms=30,
        min_interval_s=0.0,
        scan_fn=lambda roots, ex, cancel=None: compute_library_signature(
            roots, ex, max_files=3, cancel=cancel),
    )
    emitted = []
    w.changed.connect(lambda: emitted.append(1))
    w.rebaseline()
    assert _idle(w)
    w.notify_activated()
    assert _idle(w)
    assert emitted == []
    _bump(tmp_path / "0.md")
    w.notify_activated()
    assert _idle(w)
    assert emitted == [1]


def test_signature_timeout_returns_none_quickly(tmp_path):
    for i in range(20):
        (tmp_path / f"{i}.md").write_text("x", encoding="utf-8")
    ticks = iter(range(0, 1000))
    started = time.monotonic()
    # Fake clock jumps 1s per call: the 5s budget is exhausted after a few entries.
    result = compute_library_signature(
        [tmp_path], [], timeout_s=5.0, clock=lambda: float(next(ticks))
    )
    assert result is None
    assert time.monotonic() - started < 2.0


def test_signature_cancel_event_aborts(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    cancel = threading.Event()
    cancel.set()
    assert compute_library_signature([tmp_path], cancel=cancel) is None


# ------------------------------------------------------------------ watcher
class _Probe:
    """Scan function whose results are scripted; counts invocations."""

    def __init__(self, *signatures):
        self.signatures = list(signatures)
        self.calls = 0

    def __call__(self, roots, excluded, cancel=None):
        self.calls += 1
        sig = self.signatures[min(self.calls - 1, len(self.signatures) - 1)]
        return sig


def _sig(n, digest="d"):
    return LibrarySignature(count=n, max_mtime_ns=n, digest=digest)


def _watcher(qapp, probe, *, enabled=True, min_interval_s=0.0, clock=time.monotonic):
    w = LibraryChangeWatcher(
        lambda: [Path(".")], lambda: enabled, lambda: [], None,
        debounce_ms=30, min_interval_s=min_interval_s, scan_fn=probe, clock=clock,
    )
    emitted = []
    w.changed.connect(lambda: emitted.append(1))
    return w, emitted


def _idle(w):
    return _pump(lambda: w._thread is None and not w._timer.isActive())


def test_debounce_collapses_bursts_into_one_scan(qapp):
    probe = _Probe(_sig(1))
    w, _ = _watcher(qapp, probe)
    for _ in range(10):
        w.notify_activated()
    assert _idle(w)
    assert probe.calls == 1


def test_min_interval_blocks_back_to_back_scans(qapp):
    now = [100.0]
    probe = _Probe(_sig(1))
    w, _ = _watcher(qapp, probe, min_interval_s=10.0, clock=lambda: now[0])
    w.notify_activated()
    assert _idle(w)
    assert probe.calls == 1
    now[0] += 5
    w.notify_activated()
    assert _idle(w)
    assert probe.calls == 1
    now[0] += 6
    w.notify_activated()
    assert _idle(w)
    assert probe.calls == 2


def test_changed_signature_emits_unchanged_does_not(qapp):
    probe = _Probe(_sig(1), _sig(1), _sig(2))
    w, emitted = _watcher(qapp, probe)
    w.notify_activated()          # first scan seeds the baseline silently
    assert _idle(w)
    assert emitted == []
    w.notify_activated()          # identical
    assert _idle(w)
    assert emitted == []
    w.notify_activated()          # different
    assert _idle(w)
    assert emitted == [1]


def test_timeout_abandons_round_without_blocking(qapp):
    probe = _Probe(_sig(1), None, _sig(1))
    w, emitted = _watcher(qapp, probe)
    w.notify_activated()
    assert _idle(w)
    w.notify_activated()          # scan reports timeout -> abandoned
    started = time.monotonic()
    assert _idle(w)
    assert time.monotonic() - started < 5
    assert emitted == []
    assert w._baseline == _sig(1)
    w.notify_activated()          # recovers on the next round
    assert _idle(w)
    assert probe.calls == 3


def test_disabled_never_scans(qapp):
    probe = _Probe(_sig(1))
    w, emitted = _watcher(qapp, probe, enabled=False)
    w.notify_activated()
    w.rebaseline()
    QCoreApplication.processEvents()
    time.sleep(0.1)
    QCoreApplication.processEvents()
    assert probe.calls == 0 and emitted == []


def test_rebaseline_sets_reference_without_emitting(qapp):
    probe = _Probe(_sig(1), _sig(2))
    w, emitted = _watcher(qapp, probe)
    w.rebaseline()
    assert _idle(w) and w._baseline == _sig(1)
    assert emitted == []
    w.notify_activated()
    assert _idle(w)
    assert emitted == [1]


# ------------------------------------------------- real MainWindow, real I/O
class _FakeStore:
    roots: list[Path] = []

    def load(self):
        class Lib:
            def __init__(self, p):
                self.path = str(p)

        return [Lib(p) for p in self.roots]


@pytest.fixture
def isolated_window_env(tmp_path, monkeypatch):
    monkeypatch.setattr(window_mod, "RendererView", _FakeRenderer)
    monkeypatch.setattr(window_mod, "PdfView", _FakePdfView)
    monkeypatch.setattr(window_mod, "LeftPanel", _FakePanel)
    monkeypatch.setattr(window_mod, "TagIndex", _FakeTagIndex)
    monkeypatch.setattr(window_mod.MainWindow, "_refresh_tags_panel", lambda self: None)
    settings_path = tmp_path / "settings" / "library-watch.ini"
    settings_path.parent.mkdir()

    def isolated_settings(*_a, **_k):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(window_mod, "QSettings", isolated_settings)
    monkeypatch.setattr(session_state, "QSettings", isolated_settings)
    monkeypatch.setattr(window_mod, "RecoveryStore", lambda: RecoveryStore(tmp_path / "recovery"))
    monkeypatch.setattr(window_mod, "DocumentLibraryStore", _FakeStore)
    monkeypatch.setattr(window_mod, "load_excluded_folders", lambda: [])
    return isolated_settings


def test_real_window_rebuilds_link_index_after_external_add(
    qapp, tmp_path, isolated_window_env, monkeypatch
):
    lib = tmp_path / "lib"
    lib.mkdir()
    target = lib / "Target.md"
    target.write_text("# Target\n", encoding="utf-8")
    _FakeStore.roots = [lib]

    window = window_mod.MainWindow()
    try:
        watcher = window._library_watcher
        watcher._timer.setInterval(30)
        window._refresh_link_index(force=True)
        assert _pump(lambda: window._link_thread is None and watcher._thread is None
                     and watcher._baseline is not None)
        assert window._link_index.backlinks(target) == []

        # External program adds a note that links to Target.
        (lib / "Source.md").write_text("see [[Target]]\n", encoding="utf-8")
        watcher._last_scan_at = None  # lift the 10 s rate limit for the test
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))

        assert _pump(lambda: bool(window._link_index.backlinks(target)))
        assert any("Source" in b for b in window._link_index.backlinks(target))
    finally:
        _dispose_window(window)


def test_real_window_switch_off_does_not_scan(
    qapp, tmp_path, isolated_window_env
):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Target.md").write_text("# Target\n", encoding="utf-8")
    _FakeStore.roots = [lib]
    isolated_window_env().setValue("library/rescan_on_focus", False)

    window = window_mod.MainWindow()
    try:
        window._refresh_link_index(force=True)
        assert _pump(lambda: window._link_thread is None)
        before = window._library_watcher.scan_count
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
        time.sleep(0.15)
        QCoreApplication.processEvents()
        assert window._library_watcher.scan_count == before == 0
    finally:
        _dispose_window(window)


# ------------------------------------------- I1b: no GUI-thread filesystem I/O
def test_scan_trigger_makes_no_filesystem_call_on_gui_thread(qapp, tmp_path, monkeypatch):
    gui = threading.get_ident()
    calls: list[str] = []
    real_exists = Path.exists

    def spy_exists(self, *a, **k):
        if threading.get_ident() == gui:
            calls.append(str(self))
            raise AssertionError("Path.exists on GUI thread")
        return real_exists(self, *a, **k)

    monkeypatch.setattr(Path, "exists", spy_exists)
    seen_roots: list = []

    def provider():
        return [str(tmp_path / "offline"), str(tmp_path)]

    w = LibraryChangeWatcher(provider, lambda: True, lambda: [], None, debounce_ms=1)
    try:
        w.rebaseline()
        assert w._thread is not None
        assert _pump(lambda: w._thread is None and w._baseline is not None)
        assert calls == []
        assert seen_roots == []
    finally:
        w.shutdown()


def test_link_index_rebuild_makes_no_filesystem_call_on_gui_thread(
    qapp, tmp_path, isolated_window_env, monkeypatch
):
    """F2: _refresh_link_index used to Path.exists() every library root on the
    GUI thread (an offline share blocks there) and walked a nested current
    folder a second time."""
    lib = tmp_path / "lib"
    sub = lib / "sub"
    sub.mkdir(parents=True)
    target = lib / "Target.md"
    target.write_text("# Target\n", encoding="utf-8")
    note = sub / "Note.md"
    note.write_text("see [[Target]]\n", encoding="utf-8")
    _FakeStore.roots = [lib, tmp_path / "offline"]
    window = window_mod.MainWindow()
    try:
        assert _pump(lambda: window._link_thread is None
                     and window._library_watcher._thread is None)
        window._current_file = note  # its folder is nested inside the library
        walked: list[list[str]] = []
        real_collect = window_mod.collect_markdown_files

        def recording_collect(roots, **kwargs):
            walked.append([str(root) for root in roots])
            return real_collect(roots, **kwargs)

        monkeypatch.setattr(window_mod, "collect_markdown_files", recording_collect)
        gui = threading.get_ident()
        calls: list[str] = []
        real_exists = Path.exists

        def spy_exists(self, *a, **k):
            if threading.get_ident() == gui:
                calls.append(str(self))
                raise AssertionError("Path.exists on GUI thread")
            return real_exists(self, *a, **k)

        monkeypatch.setattr(Path, "exists", spy_exists)
        window._refresh_link_index(force=True)
        assert _pump(lambda: window._link_thread is None)
        monkeypatch.setattr(Path, "exists", real_exists)
        assert calls == []
        # One walk of the library; the nested folder and the offline root are skipped.
        assert walked == [[str(lib)]]
        assert any("Note" in source for source in window._link_index.backlinks(target))
        assert window._link_index.completion_candidates == ["sub/Note", "Target"]
    finally:
        _dispose_window(window)


def test_cancelled_close_keeps_link_index_rebuild_and_watcher(
    qapp, tmp_path, isolated_window_env, monkeypatch
):
    """F2: closeEvent stopped both background scans before asking to save; a
    "Cancel" then dropped the rebuild and could freeze the GUI for seconds."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("# a\n", encoding="utf-8")
    _FakeStore.roots = [lib]
    window = window_mod.MainWindow()
    release = threading.Event()
    try:
        assert _pump(lambda: window._link_thread is None
                     and window._library_watcher._thread is None)

        def blocked_collect(roots, **kwargs):
            release.wait(10)
            return []

        monkeypatch.setattr(window_mod, "collect_markdown_files", blocked_collect)
        window._refresh_link_index(force=True)
        thread = window._link_thread
        assert thread is not None and thread.is_alive()
        watcher = window._library_watcher
        generation = watcher._generation
        index_before = window._link_index
        window._confirm_close_all_edits = lambda: False  # the user presses Cancel

        started = time.monotonic()
        assert window.close() is False
        assert time.monotonic() - started < 1.0
        assert window._link_thread is thread and not thread.cancel.is_set()
        assert watcher._generation == generation

        release.set()
        assert _pump(lambda: window._link_thread is None)
        assert window._link_index is not index_before  # the rebuild was delivered

        # A confirmed close still stops the scans.
        release.clear()
        window._refresh_link_index(force=True)
        thread = window._link_thread
        assert thread is not None and thread.is_alive()
        window._confirm_close_all_edits = lambda: True
        threading.Timer(0.2, release.set).start()
        assert window.close() is True
        assert window._link_thread is None and not thread.is_alive()
        assert watcher._generation > generation
    finally:
        release.set()
        _dispose_window(window)




# ------------------------------------------- background jobs hold no Qt object
def _qt_objects_held_by(job):
    """QObjects *job* references directly, through bound methods or partials.

    The job may be released on its own thread, so it must hold none (see
    app/worker_relay.py); its relay's emit is the sanctioned way back.
    """
    found = []

    def visit(value):
        if isinstance(value, QObject):
            found.append(value)
        elif inspect.ismethod(value):
            visit(value.__self__)
        elif isinstance(value, functools.partial):
            for part in (value.func, *value.args, *value.keywords.values()):
                visit(part)

    for value in vars(job).values():
        visit(value)
    return found


def test_scan_job_holds_no_qt_object(qapp, tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    w = LibraryChangeWatcher(lambda: [tmp_path], lambda: True, lambda: [], None)
    try:
        w.rebaseline()  # the default scan, bound to nothing Qt-owned
        job = w._thread
        assert job is not None
        assert _qt_objects_held_by(job) == []
        assert _idle(w) and w._baseline is not None and w._baseline.count == 1
    finally:
        w.shutdown()


def test_scan_report_after_shutdown_is_dropped(qapp):
    def scan(roots, excluded, cancel=None):
        cancel.wait(5)
        return _sig(1)  # a result that still arrives after shutdown()

    w, emitted = _watcher(qapp, scan)
    w.rebaseline()
    job = w._thread
    started = time.monotonic()
    w.shutdown()
    assert time.monotonic() - started < 1.0  # the scan honoured the cancel
    assert w._thread is None and job.cancel.is_set()
    assert _pump(lambda: not job.is_alive())
    QCoreApplication.processEvents()
    assert w._baseline is None and emitted == [] and w._thread is None


def test_link_build_holds_no_qt_object(qapp, tmp_path, isolated_window_env, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("# a\n", encoding="utf-8")
    _FakeStore.roots = [lib]
    monkeypatch.setattr(window_mod.MainWindow, "_check_updates_silent", lambda self: None)
    window = window_mod.MainWindow()
    release = threading.Event()
    try:
        assert _pump(lambda: window._link_thread is None)

        def blocked_collect(roots, **kwargs):
            release.wait(10)
            return []

        monkeypatch.setattr(window_mod, "collect_markdown_files", blocked_collect)
        window._refresh_link_index(force=True)
        build = window._link_thread
        assert build is not None and build.is_alive()
        assert _qt_objects_held_by(build) == []
        release.set()
        assert _pump(lambda: window._link_thread is None)
    finally:
        release.set()
        _dispose_window(window)


def test_refresh_while_an_ended_build_still_reports_queues_a_rebuild(
    qapp, tmp_path, isolated_window_env, monkeypatch
):
    """An ended build whose reports were still queued used to be replaced at
    once; its late ``finished`` then retired the newer, running QThread."""
    lib = tmp_path / "lib"
    lib.mkdir()
    target = lib / "Target.md"
    target.write_text("# Target\n", encoding="utf-8")
    _FakeStore.roots = [lib]
    monkeypatch.setattr(window_mod.MainWindow, "_check_updates_silent", lambda self: None)
    window = window_mod.MainWindow()
    try:
        assert _pump(lambda: window._link_thread is None)
        walks = []
        real_collect = window_mod.collect_markdown_files

        def counting_collect(roots, **kwargs):
            walks.append(1)
            return real_collect(roots, **kwargs)

        monkeypatch.setattr(window_mod, "collect_markdown_files", counting_collect)
        window._refresh_link_index(force=True)
        first = window._link_thread
        deadline = time.monotonic() + 10
        while first.is_alive() and time.monotonic() < deadline:
            time.sleep(0.005)  # no event processing: its reports stay queued
        assert not first.is_alive()

        (lib / "Source.md").write_text("see [[Target]]\n", encoding="utf-8")
        window._refresh_link_index(force=True)
        assert window._link_thread is first and window._link_refresh_pending

        assert _pump(lambda: window._link_thread is None and len(walks) == 2)
        assert any("Source" in b for b in window._link_index.backlinks(target))
    finally:
        _dispose_window(window)


def _refuse_to_start(self):
    raise RuntimeError("can't start new thread")


def test_scan_that_cannot_start_leaves_the_watcher_idle(qapp, monkeypatch, caplog):
    probe = _Probe(_sig(1))
    w, emitted = _watcher(qapp, probe)
    try:
        monkeypatch.setattr(library_watch._ScanThread, "start", _refuse_to_start)
        with caplog.at_level(logging.WARNING, logger="app.library_watch"):
            w.rebaseline()
        assert w._thread is None and w.scan_count == 0 and probe.calls == 0
        assert "Could not start a library signature scan" in caplog.text
        w.shutdown()  # nothing was started, so there is nothing to wait for

        monkeypatch.undo()
        w.rebaseline()
        assert _idle(w) and w._baseline == _sig(1) and emitted == []
    finally:
        w.shutdown()


def test_link_build_that_cannot_start_leaves_no_build_behind(
    qapp, tmp_path, isolated_window_env, monkeypatch, caplog
):
    lib = tmp_path / "lib"
    lib.mkdir()
    target = lib / "Target.md"
    target.write_text("# Target\n", encoding="utf-8")
    _FakeStore.roots = [lib]
    monkeypatch.setattr(window_mod.MainWindow, "_check_updates_silent", lambda self: None)
    window = window_mod.MainWindow()
    try:
        assert _pump(lambda: window._link_thread is None)
        (lib / "Source.md").write_text("see [[Target]]\n", encoding="utf-8")
        real_start = window_mod.LinkIndexThread.start
        monkeypatch.setattr(window_mod.LinkIndexThread, "start", _refuse_to_start)
        with caplog.at_level(logging.WARNING, logger="app.window"):
            window._refresh_link_index(force=True)
        assert window._link_thread is None
        assert "Could not start the link index build" in caplog.text
        window._shutdown_link_thread()  # must not wait() on a thread never started

        monkeypatch.setattr(window_mod.LinkIndexThread, "start", real_start)
        window._refresh_link_index()  # unforced: the failed roots never counted as indexed
        assert window._link_thread is not None
        assert _pump(lambda: window._link_thread is None)
        assert any("Source" in b for b in window._link_index.backlinks(target))
    finally:
        _dispose_window(window)


# ------------------------------------------- I1b: shutdown while indexing
def _run_script(tmp_path, source, *args):
    import subprocess
    import sys

    script = tmp_path / "crash_probe.py"
    script.write_text(source, encoding="utf-8")
    repo = Path(__file__).resolve().parent.parent
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(repo))
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(script), *args],
        cwd=repo, env=env, capture_output=True, text=True, timeout=300,
    )


_WINDOW_SCRIPT_PREAMBLE = r'''
import random, sys, threading, time, tempfile
from pathlib import Path
from PySide6.QtCore import QSettings, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
app = QApplication([])
from app import session_state, window as window_mod
from app.recovery import RecoveryStore
from tests.test_window_integration import _FakePanel, _FakePdfView, _FakeRenderer, _FakeTagIndex

tmp = Path(tempfile.mkdtemp())
ini = tmp / "s.ini"
def iso(*a, **k):
    return QSettings(str(ini), QSettings.Format.IniFormat)
window_mod.QSettings = iso
session_state.QSettings = iso
window_mod.RecoveryStore = lambda: RecoveryStore(tmp / "rec")
window_mod.RendererView = _FakeRenderer
window_mod.PdfView = _FakePdfView
window_mod.LeftPanel = _FakePanel
window_mod.TagIndex = _FakeTagIndex
window_mod.MainWindow._refresh_tags_panel = lambda self: None
window_mod.MainWindow._check_updates_silent = lambda self: None
(tmp / "n.md").write_text("# n\n", encoding="utf-8")
class Store:
    def load(self):
        class L:
            path = str(tmp)
        return [L()]
window_mod.DocumentLibraryStore = Store
window_mod.load_excluded_folders = lambda: []
'''

_CRASH_SCRIPT = _WINDOW_SCRIPT_PREAMBLE + r'''
mode = sys.argv[1]
delay = {"short": 1.0, "long": 4.5, "exit": 6.0}[mode]
def slow_collect(roots, **kw):
    time.sleep(delay)
    return []
window_mod.collect_markdown_files = slow_collect

w = window_mod.MainWindow()
w._refresh_link_index(force=True)
build = w._link_thread
assert build is not None and build.is_alive()
w.close()
w.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
assert not isValid(w)
if mode == "exit":
    # Still building when the interpreter exits: a QThread here was
    # destroyed while running and aborted the process.
    assert build.is_alive()
    print("OK")
    sys.exit(0)
deadline = time.monotonic() + 20
for _ in range(50):
    QCoreApplication.processEvents()
    time.sleep(0.02)
while build.is_alive() and time.monotonic() < deadline:
    QCoreApplication.processEvents()
    time.sleep(0.02)
assert not build.is_alive()
print("OK")
'''


@pytest.mark.parametrize("mode", ["short", "long", "exit"])
def test_closing_window_while_link_index_builds_does_not_crash(tmp_path, mode):
    proc = _run_script(tmp_path, _CRASH_SCRIPT, mode)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
    assert "QThread: Destroyed" not in proc.stderr
    assert "Warning" not in proc.stderr


_LINK_STRESS_SCRIPT = _WINDOW_SCRIPT_PREAMBLE + r'''
mode, rounds = sys.argv[1], int(sys.argv[2])
random.seed(7)
gate = threading.Event()
walks = [0]
def collect(roots, **kw):
    walks[0] += 1
    if walks[0] % 2 == 0:
        gate.wait(10)  # the second build of each round waits for the gate
    return []
window_mod.collect_markdown_files = collect

def pump(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()

w = window_mod.MainWindow()
assert pump(lambda: w._link_thread is None)
for i in range(rounds):
    gate.clear()
    walks[0] = 0
    w._refresh_link_index(force=True)
    first = w._link_thread
    if mode == "ended":
        while first.is_alive():  # ended, but its reports are still queued
            time.sleep(0.0005)
    else:
        time.sleep(random.uniform(0, 0.004))
    w._refresh_link_index(force=True)
    for _ in range(5):
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gate.set()
    assert pump(lambda: w._link_thread is None), f"round {i} never settled"
    assert not w._link_refresh_pending
w.close()
print("OK", rounds)
'''


@pytest.mark.parametrize("mode", ["ended", "racing"])
def test_link_index_refresh_racing_a_finished_build_does_not_crash(tmp_path, mode):
    proc = _run_script(tmp_path, _LINK_STRESS_SCRIPT, mode, "200")
    assert proc.returncode == 0, proc.stderr
    assert "OK 200" in proc.stdout


_WATCH_STRESS_SCRIPT = r'''
import random, sys, threading, time
from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import delete
app = QApplication([])
from app.library_watch import LibraryChangeWatcher, LibrarySignature

mode = sys.argv[1]
random.seed(11)

def spin(seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass

if mode == "owner":
    rounds = int(sys.argv[2])
    def scan(roots, excluded, cancel=None):
        spin(random.uniform(0, 0.002))
        return LibrarySignature(1, 1, "d")
    for i in range(rounds):
        owner = QObject()
        watcher = LibraryChangeWatcher(lambda: ["."], lambda: True, lambda: [], owner,
                                       scan_fn=scan)
        watcher.rebaseline()
        spin(random.uniform(0, 0.002))
        if i % 3 == 1:
            QCoreApplication.processEvents()
        delete(owner)  # no shutdown(): destroyed as the scan starts or ends
        del watcher
    deadline = time.monotonic() + 20
    while threading.active_count() > 1 and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    print("OK", rounds)
else:
    # A scan stuck on an offline share outlives shutdown() and the process.
    def scan(roots, excluded, cancel=None):
        time.sleep(4.0)
        return None
    watcher = LibraryChangeWatcher(lambda: ["."], lambda: True, lambda: [], None,
                                   scan_fn=scan)
    watcher.rebaseline()
    watcher.shutdown()
    assert watcher._thread is None
    print("OK exit")
    sys.exit(0)
'''


@pytest.mark.parametrize("mode", ["owner", "exit"])
def test_library_scan_outliving_its_owner_does_not_crash(tmp_path, mode):
    proc = _run_script(tmp_path, _WATCH_STRESS_SCRIPT, mode, "300")
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
    assert "QThread: Destroyed" not in proc.stderr
