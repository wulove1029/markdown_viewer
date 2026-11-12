"""Agents-only view: the AI Agents dock fills the window and is fully reversible."""

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest

from app import settings_store
from app.shortcuts import WINDOW_SHORTCUTS

from .test_window_integration import (  # noqa: F401  (autouse fixtures)
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
    md_files,
)


@pytest.fixture(autouse=True)
def _isolated_watchdog_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings_store, "settings",
        lambda: QSettings(str(tmp_path / "wd.ini"), QSettings.Format.IniFormat),
    )
    from app import agent_watchdog
    monkeypatch.setattr(agent_watchdog.WatchdogConfig, "load",
                        classmethod(lambda cls: cls(enabled=False)), raising=False)


@pytest.fixture
def win(qapp, make_window):
    window = make_window()
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    return window


def _normal(window):
    window._properties_dock.show()
    window._show_agents_panel()
    window._ensure_agents_dock()


def _visible(window):
    dock = window._agents_dock
    return {
        "central": window.centralWidget().isVisible(),
        "status": window.statusBar().isVisible(),
        "props": window._properties_dock.isVisible(),
        "reading": window._reading_tools.dock.isVisible(),
        "agents": dock.isVisible(),
        "floating": dock.isFloating(),
    }


def test_shortcut_unique_and_menu_action(win):
    seqs = [s for spec in WINDOW_SHORTCUTS for s in spec.sequences]
    assert seqs.count("Ctrl+Alt+A") == 1
    action = win._agents_only_action
    assert action.isCheckable() and not action.isChecked()
    actions = win._view_menu.actions()
    assert actions.index(action) == actions.index(win._agents_panel_action) + 1


def test_enter_and_leave_restores_layout(win, qapp):
    _normal(win)
    win._reading_tools.dock.show()
    qapp.processEvents()
    before = _visible(win)
    assert before["central"] and before["props"] and before["agents"]
    win._toggle_agents_only()
    qapp.processEvents()
    now = _visible(win)
    assert now["agents"] and not now["floating"]
    assert not any(now[k] for k in ("central", "status", "props", "reading"))
    assert not win._toolbar.isVisible()
    assert win.menuBar().isVisible()
    assert win._agents_only_action.isChecked()
    win._toggle_agents_only()
    qapp.processEvents()
    assert _visible(win) == before
    assert win._toolbar.isVisible()
    assert not win._agents_only_action.isChecked()


def test_floating_dock_is_docked_then_floats_again(win, qapp):
    _normal(win)
    win._agents_dock.setFloating(True)
    qapp.processEvents()
    win._enter_agents_only()
    assert not win._agents_dock.isFloating() and win._agents_dock.isVisible()
    win._leave_agents_only()
    qapp.processEvents()
    assert win._agents_dock.isFloating() and win._agents_dock.isVisible()
    assert win.centralWidget().isVisible()


def test_dock_hidden_before_stays_hidden_after(win, qapp):
    win._ensure_agents_dock().hide()
    win._enter_agents_only()
    assert win._agents_dock.isVisible()
    win._leave_agents_only()
    assert not win._agents_dock.isVisible()
    assert win.centralWidget().isVisible()


def test_escape_does_not_touch_layout_or_mode(win, qapp):
    from PySide6.QtGui import QShortcut

    _normal(win)
    esc = [s for s in win._agents_dock.findChildren(QShortcut)
           if s.key().matches(Qt.Key.Key_Escape)]
    assert esc == []
    before = _visible(win)
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert _visible(win) == before
    win._enter_agents_only()
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert win._agents_only_state is not None
    assert win._agents_dock.isVisible() and not win.centralWidget().isVisible()
    assert win._agents_only_action.isChecked()


def test_floating_dock_leaves_via_floating_path(win, qapp, monkeypatch):
    _normal(win)
    win._enter_agents_only()
    calls = []
    original = win._leave_agents_only

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(win, "_leave_agents_only", spy)
    shown = []
    win._agents_dock.visibilityChanged.connect(shown.append)
    win._agents_dock.setFloating(True)
    # Immediately after the float: left through the floating path, dock up.
    assert win._agents_only_state is None
    assert calls == [{"keep_dock_floating": True}]
    assert win._agents_dock.isFloating() and win._agents_dock.isVisible()
    qapp.processEvents()
    assert calls == [{"keep_dock_floating": True}]
    assert shown[-1] is True
    assert win.centralWidget().isVisible()
    assert not win._agents_only_action.isChecked()
    assert win._agents_dock.isFloating() and win._agents_dock.isVisible()


def test_closing_dock_leaves_and_keeps_it_closed(win, qapp):
    _normal(win)
    win._enter_agents_only()
    win._toggle_agents_panel()  # same path as the dock's close button
    qapp.processEvents()
    assert win._agents_only_state is None
    assert win.centralWidget().isVisible()
    assert not win._agents_dock.isVisible()
    assert not win._agents_only_action.isChecked()
    assert not win._agents_panel_action.isChecked()


def test_opening_document_leaves(win, md_files, qapp):
    _normal(win)
    win._enter_agents_only()
    win._open_file(str(md_files[0]))
    qapp.processEvents()
    assert win._agents_only_state is None
    assert win.centralWidget().isVisible()
    assert not win._agents_only_action.isChecked()


def test_focus_mode_leaves(win, qapp):
    _normal(win)
    win._enter_agents_only()
    win._workspace_controls.toggle_focus()
    qapp.processEvents()
    assert win._agents_only_state is None
    assert win._workspace_controls.focused
    assert not win._agents_only_action.isChecked()
    win._workspace_controls.toggle_focus()
    assert win.centralWidget().isVisible()


def test_entering_agents_only_ends_focus_mode(win, qapp):
    _normal(win)
    win._workspace_controls.toggle_focus()
    win._enter_agents_only()
    assert not win._workspace_controls.focused
    assert win._agents_dock.isVisible() and not win.centralWidget().isVisible()


def test_closing_window_restores_normal_layout(win, qapp):
    _normal(win)
    win._enter_agents_only()
    win.close()
    assert win._agents_only_state is None
    assert not win.centralWidget().isHidden()
    assert not win._agents_only_action.isChecked()
